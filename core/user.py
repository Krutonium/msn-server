from typing import Dict
from datetime import datetime

from db import Session, User as DBUser, UserYahoo as DBUserYahoo
from util.hash import hasher, hasher_md5, hasher_md5crypt

from .models import User, UserYahoo, Contact, YahooContact, UserStatus, UserYahooStatus, UserDetail, UserYahooDetail, Group

class UserService:
	def __init__(self):
		self._cache_by_uuid = {} # type: Dict[str, User]
		self._yahoo_cache_by_uuid = {} # type: Dict[str, UserYahoo]
	
	def verify_user_db_entry_yahoo(self, email):
		# Yahoo! clients tend to remove "@yahoo.com" if a user logs in, but it might not check for other domains; double check for that
		if email.find('@') == -1: email += '@yahoo.com'
		
		with Session() as sess:
			dbuser = sess.query(DBUserYahoo).filter(DBUserYahoo.email == email).one_or_none()
			if dbuser is None: return None
			return dbuser.uuid
	
	def login(self, email, pwd):
		with Session() as sess:
			dbuser = sess.query(DBUser).filter(DBUser.email == email).one_or_none()
			if dbuser is None: return None
			if not hasher.verify(pwd, dbuser.password): return None
			return dbuser.uuid
	
	def login_md5(self, email, md5_hash):
		with Session() as sess:
			dbuser = sess.query(DBUser).filter(DBUser.email == email).one_or_none()
			if dbuser is None: return None
			if not hasher_md5.verify_hash(md5_hash, dbuser.password_md5): return None
			return dbuser.uuid
	
	def get_md5_password_yahoo(self, email):
		# Yahoo! clients tend to remove "@yahoo.com" if a user logs in, but it might not check for other domains; double check for that
		if email.find('@') == -1: email += '@yahoo.com'
		
		with Session() as sess:
			dbuser = sess.query(DBUserYahoo).filter(DBUserYahoo.email == email).one_or_none()
			if dbuser is None: return None
			return hasher_md5.extract_hash(dbuser.password_md5)
	
	def get_md5_salt(self, email):
		with Session() as sess:
			tmp = sess.query(DBUser.password_md5).filter(DBUser.email == email).one_or_none()
			password_md5 = tmp and tmp[0]
		if password_md5 is None: return None
		return hasher.extract_salt(password_md5)
	
	def get_md5crypt_password_yahoo(self, email):
		# Yahoo! clients tend to remove "@yahoo.com" if a user logs in, but it might not check for other domains; double check for that
		if email.find('@') == -1: email += '@yahoo.com'
		
		with Session() as sess:
			dbuser = sess.query(DBUserYahoo).filter(DBUserYahoo.email == email).one_or_none()
			if dbuser is None: return None
			return hasher_md5crypt.extract_hash(dbuser.password_md5crypt)
	
	def update_date_login(self, uuid):
		with Session() as sess:
			sess.query(DBUser).filter(DBUser.uuid == uuid).update({
				'date_login': datetime.utcnow(),
			})
	
	def get_uuid(self, email):
		with Session() as sess:
			tmp = sess.query(DBUser.uuid).filter(DBUser.email == email).one_or_none()
			return tmp and tmp[0]
	
	def get_uuid_yahoo(self, email):
		# Yahoo! clients tend to remove "@yahoo.com" if a user logs in, but it might not check for other domains; double check for that
		if email.find('@') == -1: email += '@yahoo.com'
		
		with Session() as sess:
			tmp = sess.query(DBUserYahoo.uuid).filter(DBUserYahoo.email == email).one_or_none()
			return tmp and tmp[0]
	
	def get(self, uuid):
		if uuid is None: return None
		if uuid not in self._cache_by_uuid:
			self._cache_by_uuid[uuid] = self._get_uncached(uuid)
		return self._cache_by_uuid[uuid]
	
	def yahoo_get(self, uuid):
		if uuid is None: return None
		if uuid not in self._yahoo_cache_by_uuid:
			self._yahoo_cache_by_uuid[uuid] = self._get_yahoo_uncached(uuid)
		return self._yahoo_cache_by_uuid[uuid]
	
	def _get_uncached(self, uuid):
		with Session() as sess:
			dbuser = sess.query(DBUser).filter(DBUser.uuid == uuid).one_or_none()
			if dbuser is None: return None
			status = UserStatus(dbuser.name, dbuser.message)
			return User(dbuser.uuid, dbuser.email, dbuser.verified, status, dbuser.date_created)
	
	def _get_yahoo_uncached(self, uuid):
		with Session() as sess:
			dbuser = sess.query(DBUserYahoo).filter(DBUserYahoo.uuid == uuid).one_or_none()
			if dbuser is None: return None
			yahoo_status = UserYahooStatus()
			return UserYahoo(dbuser.uuid, dbuser.email, dbuser.yahoo_id, dbuser.verified, yahoo_status, dbuser.date_created)
	
	def get_detail(self, uuid):
		with Session() as sess:
			dbuser = sess.query(DBUser).filter(DBUser.uuid == uuid).one_or_none()
			if dbuser is None: return None
			detail = UserDetail(dbuser.settings)
			for g in dbuser.groups:
				grp = Group(**g)
				detail.groups[grp.id] = grp
			for c in dbuser.contacts:
				ctc_head = self.get(c['uuid'])
				if ctc_head is None: continue
				status = UserStatus(c['name'], c['message'])
				ctc = Contact(
					ctc_head, set(c['groups']), c['lists'], status,
					is_messenger_user = c.get('is_messenger_user'),
				)
				detail.contacts[ctc.head.uuid] = ctc
		return detail
	
	def get_yahoo_detail(self, uuid):
		with Session() as sess:
			dbuser_yahoo = sess.query(DBUserYahoo).filter(DBUserYahoo.uuid == uuid).one_or_none()
			if dbuser_yahoo is None: return None
			detail = UserYahooDetail()
			for g in dbuser_yahoo.groups:
				grp = Group(**g)
				detail.groups[grp.id] = grp
			for c in dbuser_yahoo.contacts:
				ctc_head = self.yahoo_get(c['uuid'])
				if ctc_head is None: continue
				yahoo_status = UserYahooStatus()
				ctc = YahooContact(
					ctc_head, c['yahoo_id'], set(c['groups']), yahoo_status, is_messenger_user = c.get('is_messenger_user'),
				)
				detail.contacts[ctc.head.uuid] = ctc
		return detail
	
	def save_batch(self, to_save):
		with Session() as sess:
			for user, detail in to_save:
				dbuser = sess.query(DBUser).filter(DBUser.uuid == user.uuid).one()
				dbuser.name = user.status.name
				dbuser.message = user.status.message
				dbuser.settings = detail.settings
				dbuser.groups = [{
					'id': g.id, 'name': g.name,
					'is_favorite': g.is_favorite,
				} for g in detail.groups.values()]
				dbuser.contacts = [{
					'uuid': c.head.uuid, 'name': c.status.name, 'message': c.status.message,
					'lists': c.lists, 'groups': list(c.groups),
					'is_messenger_user': c.is_messenger_user,
				} for c in detail.contacts.values()]
				sess.add(dbuser)
	
	def save_batch_yahoo(self, to_save):
		with Session() as sess:
			for user, detail in to_save:
				dbuser = sess.query(DBUserYahoo).filter(DBUserYahoo.uuid == user.uuid).one()
				dbuser.groups = [{
					'id': g.id, 'name': g.name
				} for g in detail.groups.values()]
				dbuser.contacts = [{
					'uuid': c.head.uuid, 'yahoo_id': c.yahoo_id,
					'groups': list(c.groups),
					'is_messenger_user': c.is_messenger_user,
				} for c in detail.contacts.values()]
				sess.add(dbuser)
