from typing import Any
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

# `user.type != TYPE_ESCARGOT` means logging in is done via
# a third-party service (e.g. Windows Live for TYPE_LIVE).
# Not currently implemented, so all accounts are TYPE_ESCARGOT.
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
	settings = sa.Column(JSONType, nullable = False)
	groups = sa.Column(JSONType, nullable = False)
	contacts = sa.Column(JSONType, nullable = False)
	
	# Data specific to front-ends; e.g. different types of password hashes
	# E.g. front_data = { 'msn': { ... }, 'ymsg': { ... }, ... }
	_front_data = sa.Column(JSONType, name = 'front_data', nullable = False)
	
	def set_front_data(self, frontend: str, key: str, value: Any) -> None:
		fd = self._front_data or {}
		if frontend not in fd:
			fd[frontend] = {}
		fd[frontend][key] = value
		# As a side-effect, this also makes `._front_data` into a new object,
		# so SQLAlchemy picks up the fact that it's been changed.
		# (SQLAlchemy only does shallow comparisons on fields by default.)
		self._front_data = _simplify_json_data(fd)
	
	def get_front_data(self, frontend: str, key: str) -> Any:
		fd = self._front_data
		if not fd: return None
		fd = fd.get(frontend)
		if not fd: return None
		return fd.get(key)

def _simplify_json_data(data: Any) -> Any:
	if isinstance(data, dict):
		d = {}
		for k, v in data.items():
			v = _simplify_json_data(v)
			if v is not None:
				d[k] = v
		if not d:
			return None
		return d
	if isinstance(data, (list, tuple)):
		return [_simplify_json_data(x) for x in data]
	return data

class OIM(Base):
	__tablename__ = 't_oim'
	
	run_id = sa.Column(sa.String, nullable = False, unique = True, primary_key = True)
	oim_num = sa.Column(sa.Integer, nullable = False)
	from_member_name = sa.Column(sa.String, nullable = False)
	from_member_friendly = sa.Column(sa.String, nullable = False)
	to_member_name = sa.Column(sa.String, nullable = False)
	oim_sent = sa.Column(sa.DateTime, nullable = False)
	content = sa.Column(sa.String, nullable = False)
	is_read = sa.Column(sa.Boolean, nullable = False)

class YahooOIM(Base):
	__tablename__ = 't_yahoo_oim'
	
	id = sa.Column(sa.Integer, nullable = False, primary_key = True)
	from_id = sa.Column(sa.String, nullable = False)
	recipient_id = sa.Column(sa.String, nullable = False)
	sent = sa.Column(sa.DateTime, nullable = False)
	message = sa.Column(sa.String, nullable = False)
	utf8_kv = sa.Column(sa.Boolean, nullable = True)

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
