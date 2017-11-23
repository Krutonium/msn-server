import asyncio
from collections import defaultdict
from enum import IntFlag
from typing import Set

from .models import User
from .event import PresenceNotificationEvent

class Ack(IntFlag):
	Zero = 0
	NAK = 1
	ACK = 2
	Full = 3

class ServiceAddress:
	def __init__(self, host, port):
		self.host = host
		self.port = port

class NotificationServer:
	def __init__(self, loop, user_service, auth_service):
		self._user_service = user_service
		self._auth_service = auth_service
		self._sbservices = [ServiceAddress('m1.escargot.log1p.xyz', 1864)]
		
		self._ncs = _NSSessCollection()
		# Dict[User.uuid, User]
		self._user_by_uuid = {}
		# Dict[User, UserDetail]
		self._unsynced_db = {}
		
		# TODO: NS isn't guaranteed to be run in a loop (e.g. testing).
		# Need to figure out a better way to do this.
		asyncio.get_event_loop().create_task(self._sync_db())
	
	def on_connection_lost(self, sess):
		user = sess.user
		if user is None: return
		self._ncs.remove_nc(sess)
		if self._ncs.get_ncs_by_user(user):
			# There are still other people logged in as this user,
			# so don't send offline notifications.
			return
		# User is offline, send notifications
		user.detail = None
		self._sync_contact_statuses()
		self._generic_notify(sess)
	
	def login_md5_get_salt(self, sess, email):
		raise NotImplementedError
	
	def login_md5_verify(self, sess, email, md5_hash):
		uuid = self._user_service.login_md5(email, md5_hash)
		return self._login_common(sess, uuid, email, token)
	
	def login_twn_start(self, email, password):
		uuid = self._user_service.login(email, password)
		if uuid is None: return None
		return self._auth_service.create_token('nb/login', uuid)
	
	def login_twn_verify(self, sess, email, token):
		uuid = self._auth_service.pop_token('nb/login', token)
		return self._login_common(sess, uuid, email, token)
	
	def _login_common(self, sess, uuid, email, token):
		if uuid is None: return None
		self._user_service.update_date_login(uuid)
		user = self._load_user_record(uuid)
		sess.user = user
		sess.token = token
		self._ncs.add_nc(sess)
		user.detail = self._load_detail(user)
		return user
	
	def _load_user_record(self, uuid):
		if uuid not in self._user_by_uuid:
			user = self._user_service.get(uuid)
			if user is None: return None
			self._user_by_uuid[uuid] = user
		return self._user_by_uuid[uuid]
	
	def _load_detail(self, user):
		if user.detail: return user.detail
		return self._user_service.get_detail(user.uuid)
	
	def _generic_notify(self, sess):
		# Notify relevant `Session`s of status, name, message, media
		user = sess.user
		if user is None: return
		for nc1 in self._ncs.get_ncs():
			if nc1 == sess: continue
			user1 = nc1.user
			if user1.detail is None: continue
			ctc = user1.detail.contacts.get(user.uuid)
			if ctc is None: continue
			nc1.send_event(PresenceNotificationEvent(ctc))
	
	def _sync_contact_statuses(self):
		# Recompute all `Contact.status`'s
		for user in self._user_by_uuid.values():
			detail = user.detail
			if detail is None: continue
			for ctc in detail.contacts.values():
				ctc.compute_visible_status(user)
	
	def _mark_modified(self, user, *, detail = None):
		ud = user.detail or detail
		if detail: assert ud is detail
		assert ud is not None
		self._unsynced_db[user] = ud
	
	def sb_token_create(self, sess, *, extra_data = None):
		token = self._auth_service.create_token('sb/xfr', { 'uuid': sess.user.uuid, 'extra_data': extra_data })
		sb = self._sbservices[0]
		return (token, sb)
	
	def me_update(self, sess, fields):
		user = sess.user
		need_sync_contact_statuses = False
		
		if 'message' in fields:
			user.status.message = fields['message']
			need_sync_contact_statuses = True
		if 'media' in fields:
			user.status.media = fields['media']
			need_sync_contact_statuses = True
		if 'name' in fields:
			user.status.name = fields['name']
			need_sync_contact_statuses = True
		if 'gtc' in fields:
			user.detail.settings['gtc'] = fields['gtc']
		if 'blp' in fields:
			user.detail.settings['blp'] = fields['blp']
			need_sync_contact_statuses = True
		if 'substatus' in fields:
			user.status.substatus = fields['substatus']
			need_sync_contact_statuses = True
		if 'capabilities' in fields:
			user.detail.capabilities = fields['capabilities']
			need_sync_contact_statuses = True
		if 'msnobj' in fields:
			user.detail.msnobj = fields['msnobj']
			need_sync_contact_statuses = True
		
		self._mark_modified(user)
		
		if need_sync_contact_statuses:
			self._sync_contact_statuses()
			self._generic_notify(sess)
		else:
			if False:
				# TODO: This was also called during an ADD/ADC command
				self._generic_notify(sess)
	
	def me_group_add(self, sess, name):
		raise NotImplementedError
	
	def me_group_remove(self, sess, group_id):
		raise NotImplementedError
	
	def me_group_edit(self, sess, group_id, new_name):
		raise NotImplementedError
	
	def me_group_contact_add(self, sess, group_id, contact_uuid):
		raise NotImplementedError
	
	def me_group_contact_remove(self, sess, group_id, contact_uuid):
		raise NotImplementedError
	
	def me_contact_add(self, sess, contact_uuid, lst, name):
		# TODO: This also needs to send presence notifications (ILN/NLN/UBX in MSNP-speak)
		raise NotImplementedError
	
	def me_contact_remove(self, sess, contact_uuid, lst):
		raise NotImplementedError
	
	def util_get_uuid_from_email(self, email):
		raise NotImplementedError
	
	def util_get_sess_by_token(self, token):
		return self._ncs.get_nc_by_token(token)
	
	async def _sync_db(self):
		while True:
			await asyncio.sleep(1)
			self._sync_db_impl()
	
	def _sync_db_impl(self):
		if not self._unsynced_db: return
		try:
			users = list(self._unsynced_db.keys())[:100]
			batch = []
			for user in users:
				detail = self._unsynced_db.pop(user, None)
				if not detail: continue
				batch.append((user, detail))
			self._user_service.save_batch(batch)
		except Exception:
			import traceback
			traceback.print_exc()

class _NSSessCollection:
	def __init__(self):
		# Dict[User, Set[Session]]
		self._ncs_by_user = defaultdict(set)
		# Dict[Session.token, Session]
		self._nc_by_token = {}
	
	def get_ncs_by_user(self, user):
		if user not in self._ncs_by_user:
			return ()
		return self._ncs_by_user[user]
	
	def get_ncs(self):
		for ncs in self._ncs_by_user.values():
			yield from ncs
	
	def get_nc_by_token(self, token):
		return self._nc_by_token.get(token)
	
	def add_nc(self, nc):
		assert nc.user
		self._ncs_by_user[nc.user].add(nc)
		if nc.token:
			self._nc_by_token[nc.token] = nc
	
	def remove_nc(self, nc):
		assert nc.user
		self._ncs_by_user[nc.user].discard(nc)
		if nc.token:
			del self._nc_by_token[nc.token]

class Switchboard:
	def invite_user(self, user):
		# TODO
		pass
