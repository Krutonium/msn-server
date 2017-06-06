import json
from contextlib import contextmanager
from datetime import datetime, timedelta
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base

from util import hash
from util.json_type import JSONType
import settings

class Base(declarative_base()):
	__abstract__ = True

TYPE_ESCARGOT = 1
TYPE_LIVE = 2

class User(Base):
	__tablename__ = 't_user'
	
	id = sa.Column(sa.Integer, nullable = False, primary_key = True)
	type = sa.Column(sa.Integer, nullable = False, default = TYPE_ESCARGOT)
	date_created = sa.Column(sa.DateTime, nullable = True, default = datetime.utcnow)
	date_login = sa.Column(sa.DateTime, nullable = True)
	uuid = sa.Column(sa.String, nullable = False, unique = True)
	email = sa.Column(sa.String, nullable = False, unique = True)
	verified = sa.Column(sa.Boolean, nullable = False)
	name = sa.Column(sa.String, nullable = False)
	message = sa.Column(sa.String, nullable = False)
	password = sa.Column(sa.String, nullable = False)
	password_md5 = sa.Column(sa.String, nullable = False)
	settings = sa.Column(JSONType, nullable = False)
	groups = sa.Column(JSONType, nullable = False)
	contacts = sa.Column(JSONType, nullable = False)

class Auth(Base):
	__tablename__ = 't_auth'
	
	id = sa.Column(sa.Integer, nullable = False, primary_key = True)
	token = sa.Column(sa.String, nullable = False, unique = True)
	email = sa.Column(sa.String, nullable = False)
	expiry = sa.Column(sa.DateTime, nullable = False)
	
	@classmethod
	def CreateToken(cls, email, lifetime = 30):
		token = hash.gen_salt(20)
		expiry = datetime.utcnow() + timedelta(seconds = lifetime)
		with Session() as sess:
			sess.add(Auth(token = token, email = email, expiry = expiry))
		return token
	
	@classmethod
	def PopToken(cls, token):
		with Session() as sess:
			email = cls.GetToken(token)
			cls.ClearToken(token)
			return email
	
	@classmethod
	def GetToken(cls, token):
		now = datetime.utcnow()
		with Session() as sess:
			sess.query(Auth).filter(Auth.expiry <= now).delete()
			auth = sess.query(Auth).filter(Auth.token == token).one_or_none()
			if auth is None: return None
			assert auth.expiry > now
			return auth.email
	
	@classmethod
	def ClearToken(cls, token):
		with Session() as sess:
			sess.query(Auth).filter(Auth.token == token).delete()

engine = sa.create_engine(settings.DB)
session_factory = sessionmaker(bind = engine)

@contextmanager
def Session():
	if Session._depth > 0:
		yield Session._global
		return
	session = session_factory()
	Session._global = session
	Session._depth += 1
	try:
		yield session
		session.commit()
	except:
		session.rollback()
		raise
	finally:
		session.close()
		Session._global = None
		Session._depth -= 1
Session._global = None
Session._depth = 0
