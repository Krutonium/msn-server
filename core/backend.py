import asyncio
from collections import defaultdict
from enum import IntFlag

from util.misc import gen_uuid

from .models import User, Group, Lst, Contact, UserStatus
from . import error, event

class Ack(IntFlag):
	Zero = 0
	NAK = 1
	ACK = 2
	Full = 3

class Backend:
	def __init__(self, loop, user_service, auth_service):
		self._user_service = user_service
		self._auth_service = auth_service
		
		self._ncs = _NSSessCollection()
		# Dict[User.uuid, User]
		self._user_by_uuid = {}
		# Dict[User, UserDetail]
		self._unsynced_db = {}
		
		# Dict[chatid, Chat]
		self._chats = {}
		
		loop.create_task(self._sync_db())
	
	def on_leave(self, sess):
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
	
	def login_md5_get_salt(self, email):
		return self._user_service.get_md5_salt(email)
	
	def login_md5_verify(self, sess, email, md5_hash):
		uuid = self._user_service.login_md5(email, md5_hash)
		return self._login_common(sess, uuid, email)
	
	def login_twn_start(self, email, password):
		uuid = self._user_service.login(email, password)
		if uuid is None: return None
		return self._auth_service.create_token('nb/login', uuid)
	
	def login_twn_verify(self, sess, email, token):
		uuid = self._auth_service.pop_token('nb/login', token)
		return self._login_common(sess, uuid, email)
	
	def _login_common(self, sess, uuid, email):
		if uuid is None: return None
		self._user_service.update_date_login(uuid)
		user = self._load_user_record(uuid)
		sess.user = user
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
		for sess_other in self._ncs.get_ncs():
			if sess_other == sess: continue
			user_other = sess_other.user
			if user_other.detail is None: continue
			ctc = user_other.detail.contacts.get(user.uuid)
			if ctc is None: continue
			sess_other.send_event(event.PresenceNotificationEvent(ctc))
	
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
		return self._auth_service.create_token('sb/xfr', { 'uuid': sess.user.uuid, 'extra_data': extra_data })
	
	def me_update(self, sess, fields):
		user = sess.user
		
		if 'message' in fields:
			user.status.message = fields['message']
		if 'media' in fields:
			user.status.media = fields['media']
		if 'name' in fields:
			user.status.name = fields['name']
		if 'gtc' in fields:
			user.detail.settings['gtc'] = fields['gtc']
		if 'blp' in fields:
			user.detail.settings['blp'] = fields['blp']
		if 'substatus' in fields:
			user.status.substatus = fields['substatus']
		if 'capabilities' in fields:
			user.detail.capabilities = fields['capabilities']
		if 'msnobj' in fields:
			user.detail.msnobj = fields['msnobj']
		
		self._mark_modified(user)
		self._sync_contact_statuses()
		self._generic_notify(sess)
	
	def me_group_add(self, sess, name):
		if len(name) > MAX_GROUP_NAME_LENGTH:
			raise error.GroupNameTooLong()
		user = sess.user
		group = Group(_gen_group_id(user.detail), name)
		user.detail.groups[group.id] = group
		self._mark_modified(user)
		return group
	
	def me_group_remove(self, sess, group_id):
		if group_id == '0':
			raise error.CannotRemoveSpecialGroup()
		user = sess.user
		try:
			del user.detail.groups[group_id]
		except KeyError:
			raise error.GroupDoesNotExist()
		for ctc in user.detail.contacts.values():
			ctc.groups.discard(group_id)
		self._mark_modified(user)
	
	def me_group_edit(self, sess, group_id, new_name):
		user = sess.user
		g = user.detail.groups.get(group_id)
		if g is None:
			raise error.GroupDoesNotExist()
		if len(name) > MAX_GROUP_NAME_LENGTH:
			raise error.GroupNameTooLong()
		g.name = name
		self._mark_modified(user)
	
	def me_group_contact_add(self, sess, group_id, contact_uuid):
		if group_id == '0': return
		user = sess.user
		detail = user.detail
		if group_id not in detail.groups:
			raise error.GroupDoesNotExist()
		ctc = detail.contacts.get(contact_uuid)
		if ctc is None:
			raise error.ContactDoesNotExist()
		if group_id in ctc.groups:
			raise error.ContactAlreadyOnList()
		ctc.groups.add(group_id)
		self._mark_modified(user)
	
	def me_group_contact_remove(self, sess, group_id, contact_uuid):
		user = sess.user
		detail = user.detail
		ctc = detail.contacts.get(contact_uuid)
		if ctc is None:
			raise error.ContactDoesNotExist()
		if group_id not in detail.groups and group_id != '0':
			raise error.GroupDoesNotExist()
		try:
			ctc.groups.remove(group_id)
		except KeyError:
			if group_id == '0':
				raise error.ContactNotOnList()
		self._mark_modified(user)
	
	def me_contact_add(self, sess, contact_uuid, lst, name):
		ctc_head = self._load_user_record(contact_uuid)
		if ctc_head is None:
			raise error.UserDoesNotExist()
		user = sess.user
		ctc = self._add_to_list(user, ctc_head, lst, name)
		if lst is Lst.FL:
			# FL needs a matching RL on the contact
			self._add_to_list(ctc_head, user, Lst.RL, user.status.name)
			self._notify_reverse_add(sess, ctc_head)
		self._sync_contact_statuses()
		self._generic_notify(sess)
		return ctc, ctc_head
	
	def _notify_reverse_add(self, sess, user_added):
		user_adder = sess.user
		# `user_added` was added to `user_adder`'s RL
		for sess_added in self._ncs.get_ncs_by_user(user_added):
			if sess_added == sess: continue
			sess_added.send_event(event.AddedToListEvent(Lst.RL, user_adder))
	
	def me_contact_edit(self, sess, contact_uuid, *, is_messenger_user = None, is_favorite = None):
		user = sess.user
		ctc = user.detail.contacts.get(contact_uuid)
		if ctc is None:
			raise error.ContactDoesNotExist()
		if is_messenger_user is not None:
			ctc.is_messenger_user = is_messenger_user
		if is_favorite is not None:
			ctc.is_favorite = is_favorite
		self._mark_modified(user)
	
	def me_contact_remove(self, sess, contact_uuid, lst):
		user = sess.user
		ctc = user.detail.contacts.get(contact_uuid)
		if ctc is None:
			raise error.ContactDoesNotExist()
		if lst is Lst.FL:
			# Remove from FL
			self._remove_from_list(user, ctc.head, Lst.FL)
			# Remove matching RL
			self._remove_from_list(ctc.head, user, Lst.RL)
		else:
			assert lst is not Lst.RL
			ctc.lists &= ~lst
		self._mark_modified(user)
		self._sync_contact_statuses()
	
	def _add_to_list(self, user, ctc_head, lst, name):
		# Add `ctc_head` to `user`'s `lst`
		detail = self._load_detail(user)
		contacts = detail.contacts
		if ctc_head.uuid not in contacts:
			contacts[ctc_head.uuid] = Contact(ctc_head, set(), 0, UserStatus(name))
		ctc = contacts.get(ctc_head.uuid)
		if ctc.status.name is None:
			ctc.status.name = name
		ctc.lists |= lst
		self._mark_modified(user, detail = detail)
		return ctc
	
	def _remove_from_list(self, user, ctc_head, lst):
		# Remove `ctc_head` from `user`'s `lst`
		detail = self._load_detail(user)
		contacts = detail.contacts
		ctc = contacts.get(ctc_head.uuid)
		if ctc is None: return
		ctc.lists &= ~lst
		if not ctc.lists:
			del contacts[ctc_head.uuid]
		self._mark_modified(user, detail = detail)
	
	def login_xfr(self, sess, email, token):
		user, extra_data = self._load_user('sb/xfr', token)
		if user is None: return None
		if user.email != email: return None
		sess.user = user
		chat = Chat()
		self._chats[chat.id] = chat
		chat.add_session(sess)
		return chat, extra_data
	
	def auth_cal(self, uuid):
		return self._auth_service.create_token('sb/cal', uuid)
	
	def login_cal(self, sess, email, token, chatid):
		user, extra_data = self._load_user('sb/cal', token)
		if user is None: return None
		if user.email != email: return None
		sess.user = user
		chat = self._chats.get(chatid)
		if chat is None: return None
		for sc, _ in chat.get_roster(self):
			sc.send_event(event.ChatParticipantJoined(user))
		chat.add_session(sess)
		return chat, extra_data
		
	def _load_user(self, purpose, token):
		data = self._auth_service.pop_token(purpose, token)
		return self._user_service.get(data['uuid']), data['extra_data']
	
	def util_get_uuid_from_email(self, email):
		return self._user_service.get_uuid(email)
	
	def util_set_sess_token(self, sess, token):
		self._ncs.set_nc_by_token(sess, token)
	
	def util_get_sess_by_token(self, token):
		return self._ncs.get_nc_by_token(token)
	
	def notify_call(self, caller_uuid, callee_email, chatid):
		caller = self._user_by_uuid.get(caller_uuid)
		if caller is None: raise error.ServerError()
		if caller.detail is None: raise error.ServerError()
		callee_uuid = self._user_service.get_uuid(callee_email)
		if callee_uuid is None: raise error.UserDoesNotExist()
		ctc = caller.detail.contacts.get(callee_uuid)
		if ctc is None: raise error.ContactDoesNotExist()
		if ctc.status.is_offlineish(): raise error.ContactNotOnline()
		ctc_ncs = self._ncs.get_ncs_by_user(ctc.head)
		if not ctc_ncs: raise error.ContactNotOnline()
		
		for ctc_nc in ctc_ncs:
			token = self._auth_service.create_token('sb/cal', { 'uuid': ctc.head.uuid, 'extra_data': ctc_nc.state.get_sb_extra_data() })
			ctc_nc.send_event(event.InvitedToChatEvent(chatid, token, caller))
	
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
		self._sessions_by_user = defaultdict(set)
		# Dict[str, Session]
		self._sess_by_token = {}
	
	def get_ncs_by_user(self, user):
		if user not in self._sessions_by_user:
			return ()
		return self._sessions_by_user[user]
	
	def get_ncs(self):
		for ncs in self._sessions_by_user.values():
			yield from ncs
	
	def set_nc_by_token(self, sess, token: str):
		self._sess_by_token[token] = sess
	
	def get_nc_by_token(self, token: str):
		return self._sess_by_token.get(token)
	
	def add_nc(self, sess):
		assert sess.user
		self._sessions_by_user[sess.user].add(sess)
	
	def remove_nc(self, sess):
		# TODO: This also needs to remove it from _sess_by_token
		assert sess.user
		self._sessions_by_user[sess.user].discard(sess)

class Chat:
	def __init__(self):
		self.id = gen_uuid()
		# Dict[Session, User]
		self._users_by_sess = {}
	
	def add_session(self, sess):
		self._users_by_sess[sess] = sess.user
	
	def send_message_to_everyone(self, sess_sender, data):
		su_sender = self._users_by_sess[sess_sender]
		for sess in self._users_by_sess.keys():
			if sess == sess_sender: continue
			sess.send_event(event.ChatMessage(su_sender, data))
	
	def get_roster(self, sess):
		roster = []
		for sess1, su1 in self._users_by_sess.items():
			if sess1 == sess: continue
			roster.append((sess1, su1))
		return roster
	
	def on_leave(self, sess):
		su = self._users_by_sess.pop(sess, None)
		if su is None: return
		# Notify others that `sess` has left
		for sess1, su1 in self._users_by_sess.items():
			if sess1 == sess: continue
			sess1.send_event(event.ChatParticipantLeft(su))

def _gen_group_id(detail):
	id = 1
	s = str(id)
	while s in detail.groups:
		id += 1
		s = str(id)
	return s

MAX_GROUP_NAME_LENGTH = 61
