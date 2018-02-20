import json
from contextlib import contextmanager
from datetime import datetime, timedelta
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base

from util import hash
from util.json_type import JSONType
import settings

class Base(declarative_base()): # type: ignore
	__abstract__ = True

TYPE_ESCARGOT = 1
TYPE_LIVE = 2
TYPE_YAHOO = 3

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

# Create seperate table for Yahoo! Messenger users to avoid conflict with MSN user table

class UserYahoo(Base):
	__tablename__ = 't_user_ymsgr'
	
	id = sa.Column(sa.Integer, nullable = False, primary_key = True)
	type = sa.Column(sa.Integer, nullable = False, default = TYPE_YAHOO)
	date_created = sa.Column(sa.DateTime, nullable = True, default = datetime.utcnow)
	date_login = sa.Column(sa.DateTime, nullable = True)
	uuid = sa.Column(sa.String, nullable = False, unique = True)
	email = sa.Column(sa.String, nullable = False, unique = True)
	verified = sa.Column(sa.Boolean, nullable = False)
	name = sa.Column(sa.String, nullable = False)
	# Currently Escargot only supports MD5 and MD5Crypt-based Yahoo! clients. Ignore for now.
	# password = sa.Column(sa.String, nullable = False)
	password_md5 = sa.Column(sa.String, nullable = False)
	password_md5crypt = sa.Column(sa.String, nullable = False)
	settings = sa.Column(JSONType, nullable = False)
	groups = sa.Column(JSONType, nullable = False)
	contacts = sa.Column(JSONType, nullable = False)

class Sound(Base):
	__tablename__ = 't_sound'
	
	hash = sa.Column(sa.String, nullable = False, primary_key = True)
	title = sa.Column(sa.String, nullable = False)
	category = sa.Column(sa.Integer, nullable = False)
	language = sa.Column(sa.Integer, nullable = False)
	is_public = sa.Column(sa.Boolean, nullable = False)


engine = sa.create_engine(settings.DB)
session_factory = sessionmaker(bind = engine)

@contextmanager
def Session():
	if Session._depth > 0: # type: ignore
		yield Session._global # type: ignore
		return
	session = session_factory()
	Session._global = session # type: ignore
	Session._depth += 1 # type: ignore
	try:
		yield session
		session.commit()
	except:
		session.rollback()
		raise
	finally:
		session.close()
		Session._global = None # type: ignore
		Session._depth -= 1 # type: ignore
Session._global = None # type: ignore
Session._depth = 0 # type: ignore
