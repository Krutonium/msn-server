from typing import Dict, List, Set, Any, Tuple, Optional, Sequence, FrozenSet, Iterable
from abc import ABCMeta, abstractmethod
import asyncio, time
from collections import defaultdict
from enum import IntFlag
from hashlib import md5
from uuid import uuid4

from util.misc import gen_uuid, EMPTY_SET, run_loop, Runner

from .user import UserService
from .auth import AuthService
from .stats import Stats
from .client import Client
from .models import User, UserYahoo, UserDetail, UserYahooDetail, Group, Lst, Contact, YahooContact, UserStatus, UserYahooStatus, YMSGStatus
from . import error, event
from util.yahoo import Y64

class Ack(IntFlag):
	Zero = 0
	NAK = 1
	ACK = 2
	Full = 3

class Backend:
	__slots__ = ('user_service', 'auth_service', '_loop', '_stats', '_sc', '_ysc', '_user_by_uuid', '_yahoo_user_by_uuid', '_unsynced_db', '_unsynced_db_yahoo', '_runners')
	
	user_service: UserService
	auth_service: AuthService
	_loop: asyncio.AbstractEventLoop
	_stats: Stats
	_sc: '_SessionCollection'
	_ysc: '_YahooSessionCollection'
	_user_by_uuid: Dict[str, User]
	_yahoo_user_by_uuid: Dict[str, UserYahoo]
	_unsynced_db: Dict[User, UserDetail]
	_unsynced_db_yahoo: Dict[UserYahoo, UserYahooDetail]
	_runners: List[Runner]
	
	def __init__(self, loop: asyncio.AbstractEventLoop, *, user_service: Optional[UserService] = None, auth_service: Optional[AuthService] = None) -> None:
		self.user_service = user_service or UserService()
		self.auth_service = auth_service or AuthService()
		self._loop = loop
		self._stats = Stats()
		self._sc = _SessionCollection()
		self._ysc = _YahooSessionCollection()
		self._user_by_uuid = {}
		self._yahoo_user_by_uuid = {}
		self._unsynced_db = {}
		self._unsynced_db_yahoo = {}
		self._runners = []
		
		loop.create_task(self._sync_db())
		loop.create_task(self._sync_yahoo_db())
		loop.create_task(self._clean_sessions())
		loop.create_task(self._sync_stats())
	
	def add_runner(self, runner: Runner) -> None:
		self._runners.append(runner)
	
	def run_forever(self) -> None:
		run_loop(self._loop, self._runners)
	
	def on_leave(self, sess: 'BackendSession') -> None:
		user = sess.user
		self._stats.on_logout()
		self._sc.remove_session(sess)
		if self._sc.get_sessions_by_user(user):
			# There are still other people logged in as this user,
			# so don't send offline notifications.
			return
		# User is offline, send notifications
		user.detail = None
		self._sync_contact_statuses()
		self._generic_notify(sess)
	
	def on_leave_yahoo(self, sess_yahoo: 'YahooBackendSession') -> None:
		user_yahoo = sess_yahoo.user_yahoo
		status = user_yahoo.status
		assert status is not None
		status.substatus = YMSGStatus.Offline
		self._stats.on_logout()
		self._ysc.remove_session(sess_yahoo)
		if self._ysc.get_sessions_by_user(user_yahoo):
			# There are still other people logged in as this user,
			# so don't send offline notifications.
			return
		# User is offline, send notifications
		user_yahoo.detail = None
		self._sync_yahoo_contact_statuses()
		self._yahoo_notify_logout(sess_yahoo)
	
	def login(self, uuid: str, client: Client, evt: event.BackendEventHandler) -> Optional['BackendSession']:
		user = self._load_user_record(uuid)
		if user is None: return None
		self.user_service.update_date_login(uuid)
		bs = BackendSession(self, user, client, evt)
		self._stats.on_login()
		self._stats.on_user_active(user, client)
		self._sc.add_session(bs)
		user.detail = self._load_detail(user)
		bs.evt.on_open()
		return bs
	
	def login_yahoo(self, uuid: str, client: Client, evt: event.YahooBackendEventHandler) -> Optional['YahooBackendSession']:
		user_yahoo = self._load_yahoo_user_record(uuid)
		if user_yahoo is None: return None
		self.user_service.update_date_login(uuid)
		ybs = YahooBackendSession(self, user_yahoo, client, evt)
		self._stats.on_login()
		self._stats.on_user_active(user_yahoo, client)
		self._ysc.add_session(ybs)
		user_yahoo.detail = self._load_yahoo_detail(user_yahoo)
		ybs.evt.on_open()
		return ybs
	
	def _load_user_record(self, uuid: str) -> Optional[User]:
		if uuid not in self._user_by_uuid:
			user = self.user_service.get(uuid)
			if user is None: return None
			self._user_by_uuid[uuid] = user
		return self._user_by_uuid[uuid]
	
	def _load_yahoo_user_record(self, uuid: str) -> Optional[UserYahoo]:
		if uuid not in self._yahoo_user_by_uuid:
			yahoo_user = self.user_service.yahoo_get(uuid)
			if yahoo_user is None: return None
			self._yahoo_user_by_uuid[uuid] = yahoo_user
		return self._yahoo_user_by_uuid[uuid]
	
	def _load_detail(self, user: User) -> UserDetail:
		if user.detail: return user.detail
		return self.user_service.get_detail(user.uuid)
	
	def _load_yahoo_detail(self, user_yahoo: UserYahoo) -> UserYahooDetail:
		if user_yahoo.detail: return user_yahoo.detail
		return self.user_service.get_yahoo_detail(user_yahoo.uuid)
	
	def chat_create(self) -> 'Chat':
		return Chat(self._stats)
	
	def conference_create(self, id: str) -> 'Conference':
		return Conference(self._stats, id)
	
	def _generic_notify(self, bs: 'BackendSession') -> None:
		# Notify relevant `Session`s of status, name, message, media
		user = bs.user
		# TODO: This does a lot of work, iterating through _every_ session.
		# If RL is set up properly, could iterate through `user.detail.contacts`.
		for bs_other in self._sc.iter_sessions():
			if bs_other == bs: continue
			user_other = bs_other.user
			if user_other is None: continue
			if user_other.detail is None: continue
			ctc = user_other.detail.contacts.get(user.uuid)
			if ctc is None: continue
			bs_other.evt.on_presence_notification(ctc)
	
	def _yahoo_notify_login_presence(self, ybs: 'YahooBackendSession') -> None:
		# Notify relevant `Session`s of Yahoo! presence after login
		user_yahoo = ybs.user_yahoo
		# TODO: This does a lot of work, iterating through _every_ session.
		for ybs_other in self._ysc.iter_sessions():
			if ybs_other == ybs: continue
			user_yahoo_other = ybs_other.user_yahoo
			if user_yahoo_other is None: continue
			if user_yahoo_other.detail is None: continue
			ctc = user_yahoo_other.detail.contacts.get(user_yahoo.uuid)
			if ctc is None: continue
			ybs_other.evt.on_login_presence_notification(ctc)
	
	def _yahoo_notify_presence(self, ybs: 'YahooBackendSession') -> None:
		# Notify relevant `Session`s of Yahoo! presence
		user_yahoo = ybs.user_yahoo
		# TODO: This does a lot of work, iterating through _every_ session.
		for ybs_other in self._ysc.iter_sessions():
			if ybs_other == ybs: continue
			user_yahoo_other = ybs_other.user_yahoo
			if user_yahoo_other is None: continue
			if user_yahoo_other.detail is None: continue
			ctc = user_yahoo_other.detail.contacts.get(user_yahoo.uuid)
			if ctc is None: continue
			ybs_other.evt.on_presence_notification(ctc)
	
	def _yahoo_notify_invisible_presence(self, ybs: 'YahooBackendSession') -> None:
		# Notify relevant `Session`s of Yahoo! presence after invisibility
		user_yahoo = ybs.user_yahoo
		# TODO: This does a lot of work, iterating through _every_ session.
		for ybs_other in self._ysc.iter_sessions():
			if ybs_other == ybs: continue
			user_yahoo_other = ybs_other.user_yahoo
			if user_yahoo_other is None: continue
			if user_yahoo_other.detail is None: continue
			ctc = user_yahoo_other.detail.contacts.get(user_yahoo.uuid)
			if ctc is None: continue
			ybs_other.evt.on_invisible_presence_notification(ctc)
	
	def _yahoo_notify_absence(self, ybs: 'YahooBackendSession') -> None:
		# Notify relevant `Session`s of Yahoo! absence
		user_yahoo = ybs.user_yahoo
		# TODO: This does a lot of work, iterating through _every_ session.
		for ybs_other in self._ysc.iter_sessions():
			if ybs_other == ybs: continue
			user_yahoo_other = ybs_other.user_yahoo
			if user_yahoo_other is None: continue
			if user_yahoo_other.detail is None: continue
			ctc = user_yahoo_other.detail.contacts.get(user_yahoo.uuid)
			if ctc is None: continue
			ybs_other.evt.on_absence_notification(ctc)
	
	def _yahoo_notify_logout(self, ybs: 'YahooBackendSession') -> None:
		# Notify relevant `Session`s of Yahoo! user logout
		user_yahoo = ybs.user_yahoo
		# TODO: This does a lot of work, iterating through _every_ session.
		for ybs_other in self._ysc.iter_sessions():
			if ybs_other == ybs: continue
			user_yahoo_other = ybs_other.user_yahoo
			if user_yahoo_other is None: continue
			if user_yahoo_other.detail is None: continue
			ctc = user_yahoo_other.detail.contacts.get(user_yahoo.uuid)
			if ctc is None: continue
			ybs_other.evt.on_logout_notification(ctc)
	
	def _yahoo_notify_im_notify(self, ybs: 'YahooBackendSession', head: UserYahoo, notify_dict: Dict[str, Any]):
		# Send a typing notification to a specified `UserYahoo`
		user_yahoo = ybs.user_yahoo
		# TODO: This does a lot of work, iterating through _every_ session.
		for ybs_other in self._ysc.iter_sessions():
			user_yahoo_other = ybs_other.user_yahoo
			if user_yahoo_other is None: continue
			if user_yahoo_other.detail is None: continue
			if user_yahoo_other == head: ybs_other.evt.on_notify_notification(ybs.user_yahoo, notify_dict)
	
	def _yahoo_im_message(self, ybs: 'YahooBackendSession', head: UserYahoo, message_dict: Dict[str, Any]):
		# Send an IM to a specified `UserYahoo`
		user_yahoo = ybs.user_yahoo
		# TODO: This does a lot of work, iterating through _every_ session.
		for ybs_other in self._ysc.iter_sessions():
			user_yahoo_other = ybs_other.user_yahoo
			if user_yahoo_other is None: continue
			if user_yahoo_other.detail is None: continue
			if user_yahoo_other == head: ybs_other.evt.on_im_message(ybs.user_yahoo, message_dict)
	
	def _yahoo_init_ft(self, ybs: 'YahooBackendSession', head: UserYahoo, xfer_dict: Dict[str, Any]):
		# Initiate a file transfer with a specified `UserYahoo`
		user_yahoo = ybs.user_yahoo
		# TODO: This does a lot of work, iterating through _every_ session.
		for ybs_other in self._ysc.iter_sessions():
			user_yahoo_other = ybs_other.user_yahoo
			if user_yahoo_other is None: continue
			if user_yahoo_other.detail is None: continue
			if user_yahoo_other == head: ybs_other.evt.on_xfer_init(ybs.user_yahoo, xfer_dict)
	
	def _yahoo_decline_conf_invite(self, ybs: 'YahooBackendSession', inviter: UserYahoo, conf_id: str, deny_msg: Optional[str]):
		# Decline a conference invite from a `UserYahoo`
		user_yahoo = ybs.user_yahoo
		# TODO: This does a lot of work, iterating through _every_ session.
		for ybs_other in self._ysc.iter_sessions():
			if ybs_other == ybs: continue
			user_yahoo_other = ybs_other.user_yahoo
			if user_yahoo_other is None: continue
			if user_yahoo_other.detail is None: continue
			if user_yahoo_other == inviter: ybs_other.evt.on_conf_invite_decline(ybs.user_yahoo, conf_id, deny_msg)
	
	def _sync_contact_statuses(self) -> None:
		# Recompute all `Contact.status`'s
		for user in self._user_by_uuid.values():
			detail = user.detail
			if detail is None: continue
			for ctc in detail.contacts.values():
				ctc.compute_visible_status(user)
	
	def _sync_yahoo_contact_statuses(self) -> None:
		# Recompute all `YahooContact.status`'s
		for user_yahoo in self._yahoo_user_by_uuid.values():
			detail = user_yahoo.detail
			if detail is None: continue
			for ctc in detail.contacts.values():
				ctc.compute_visible_status(user_yahoo)
	
	def _mark_modified(self, user: User, *, detail: Optional[UserDetail] = None) -> None:
		ud = user.detail or detail
		if detail: assert ud is detail
		assert ud is not None
		self._unsynced_db[user] = ud
	
	def _yahoo_mark_modified(self, user_yahoo: UserYahoo, *, detail: Optional[UserYahooDetail] = None) -> None:
		ud = user_yahoo.detail or detail
		if detail: assert ud is detail
		assert ud is not None
		self._unsynced_db_yahoo[user_yahoo] = ud
	
	def util_get_uuid_from_email(self, email: str) -> Optional[str]:
		return self.user_service.get_uuid(email)
	
	def util_get_yahoo_uuid_from_email(self, yahoo_id: str) -> Optional[str]:
		return self.user_service.get_uuid_yahoo(yahoo_id)
	
	def util_set_sess_token(self, sess: 'BackendSession', token: str) -> None:
		self._sc.set_nc_by_token(sess, token)
	
	def util_get_sess_by_token(self, token: str) -> Optional['BackendSession']:
		return self._sc.get_nc_by_token(token)
	
	def util_get_sessions_by_user(self, user: User) -> Iterable['BackendSession']:
		return self._sc.get_sessions_by_user(user)
	
	def util_get_sessions_by_user_yahoo(self, user_yahoo: UserYahoo) -> Iterable['YahooBackendSession']:
		return self._ysc.get_sessions_by_user(user_yahoo)
	
	async def _sync_db(self) -> None:
		while True:
			await asyncio.sleep(1)
			self._sync_db_impl()
	
	async def _sync_yahoo_db(self) -> None:
		while True:
			await asyncio.sleep(1)
			self._sync_yahoo_db_impl()
	
	def _sync_db_impl(self) -> None:
		if not self._unsynced_db: return
		try:
			users = list(self._unsynced_db.keys())[:100]
			batch = []
			for user in users:
				detail = self._unsynced_db.pop(user, None)
				if not detail: continue
				batch.append((user, detail))
			self.user_service.save_batch(batch)
		except Exception:
			import traceback
			traceback.print_exc()
	
	def _sync_yahoo_db_impl(self) -> None:
		if not self._unsynced_db_yahoo: return
		try:
			yahoo_users = list(self._unsynced_db_yahoo.keys())[:100]
			yahoo_batch = []
			for yahoo_user in yahoo_users:
				yahoo_detail = self._unsynced_db_yahoo.pop(yahoo_user, None)
				if not yahoo_detail: continue
				yahoo_batch.append((yahoo_user, yahoo_detail))
			self.user_service.save_batch_yahoo(yahoo_batch)
		except Exception:
			import traceback
			traceback.print_exc()
	
	async def _clean_sessions(self) -> None:
		while True:
			await asyncio.sleep(10)
			now = time.time()
			closed = []
			
			try:
				for sess in self._sc.iter_sessions():
					if sess.closed:
						closed.append(sess)
			except Exception:
				import traceback
				traceback.print_exc()
			
			for sess in closed:
				self._sc.remove_session(sess)
	
	async def _sync_stats(self) -> None:
		while True:
			await asyncio.sleep(60)
			try:
				self._stats.flush()
			except Exception:
				import traceback
				traceback.print_exc()
	
	# Yahoo-specific functions
	
	def verify_yahoo_user(self, user):
		user_in_database = self.user_service.verify_user_db_entry_yahoo(user)
		
		if user_in_database is None:
			return False
		else:
			return True
	
	def generate_challenge_v1(self):
		# Yahoo64-encode the raw 16 bytes of a UUID
		return Y64.Y64Encode(uuid4().bytes)
	
	def verify_challenge_v1(self, user_y, chal, resp_6, resp_96):
		pass_md5 = ''
		pass_md5crypt = ''
		
		# Retreive Yahoo64-encoded MD5 hash of the user's password from the database
		# NOTE: The MD5 hash of the password is literally unsalted. Good grief, Yahoo!
		pass_md5 = self.user_service.get_md5_password_yahoo(user_y)
		# Retreive MD5-crypt(3)'d hash of the user's password from the database
		pass_md5crypt = self.user_service.get_md5crypt_password_yahoo(user_y)
		pass_md5crypt = Y64.Y64Encode(md5(pass_md5crypt.encode()).digest())
		
		seed_val = (ord(chal[15]) % 8) % 5
		
		if seed_val == 0:
			checksum = chal[ord(chal[7]) % 16]
			hash_p = checksum + pass_md5 + user_y + chal
			hash_c = checksum + pass_md5crypt + user_y + chal
		elif seed_val == 1:
			checksum = chal[ord(chal[9]) % 16]
			hash_p = checksum + user_y + chal + pass_md5
			hash_c = checksum + user_y + chal + pass_md5crypt
		elif seed_val == 2:
			checksum = chal[ord(chal[15]) % 16]
			hash_p = checksum + chal + pass_md5 + user_y
			hash_c = checksum + chal + pass_md5crypt + user_y
		elif seed_val == 3:
			checksum = chal[ord(chal[1]) % 16]
			hash_p = checksum + user_y + pass_md5 + chal
			hash_c = checksum + user_y + pass_md5crypt + chal
		elif seed_val == 4:
			checksum = chal[ord(chal[3]) % 16]
			hash_p = checksum + pass_md5 + chal + user_y
			hash_c = checksum + pass_md5crypt + chal + user_y
		
		resp_6_server = Y64.Y64Encode(md5(hash_p.encode()).digest())
		resp_96_server = Y64.Y64Encode(md5(hash_c.encode()).digest())
		
		if resp_6 == resp_6_server and resp_96 == resp_96_server:
			return True
		else:
			return False

class Session(metaclass = ABCMeta):
	__slots__ = ('closed',)
	
	closed: bool
	
	def __init__(self) -> None:
		self.closed = False
	
	def close(self) -> None:
		if self.closed:
			return
		self.closed = True
		self._on_close()
	
	@abstractmethod
	def _on_close(self) -> None: pass

class BackendSession(Session):
	__slots__ = ('backend', 'user', 'client', 'evt', 'front_data')
	
	backend: Backend
	user: User
	client: Client
	evt: event.BackendEventHandler
	front_data: Dict[str, Any]
	
	def __init__(self, backend: Backend, user: User, client: Client, evt: event.BackendEventHandler) -> None:
		super().__init__()
		self.backend = backend
		self.user = user
		self.client = client
		self.evt = evt
		self.front_data = {}
	
	def _on_close(self) -> None:
		self.evt.on_close()
		self.backend.on_leave(self)
	
	def me_update(self, fields: Dict[str, Any]) -> None:
		user = self.user
		detail = user.detail
		assert detail is not None
		
		if 'message' in fields:
			user.status.message = fields['message']
		if 'media' in fields:
			user.status.media = fields['media']
		if 'name' in fields:
			user.status.name = fields['name']
		if 'gtc' in fields:
			detail.settings['gtc'] = fields['gtc']
		if 'blp' in fields:
			detail.settings['blp'] = fields['blp']
		if 'substatus' in fields:
			user.status.substatus = fields['substatus']
		
		self.backend._mark_modified(user)
		self.backend._sync_contact_statuses()
		self.backend._generic_notify(self)
	
	def me_group_add(self, name: str, *, is_favorite: Optional[bool] = None) -> Group:
		if len(name) > MAX_GROUP_NAME_LENGTH:
			raise error.GroupNameTooLong()
		user = self.user
		detail = user.detail
		assert detail is not None
		group = Group(_gen_group_id(detail), name, is_favorite = is_favorite)
		detail.groups[group.id] = group
		self.backend._mark_modified(user)
		return group
	
	def me_group_remove(self, group_id: str) -> None:
		if group_id == '0':
			raise error.CannotRemoveSpecialGroup()
		user = self.user
		detail = user.detail
		assert detail is not None
		try:
			del detail.groups[group_id]
		except KeyError:
			raise error.GroupDoesNotExist()
		for ctc in detail.contacts.values():
			ctc.groups.discard(group_id)
		self.backend._mark_modified(user)
	
	def me_group_edit(self, group_id: str, new_name: str, *, is_favorite: Optional[bool] = None) -> None:
		user = self.user
		detail = user.detail
		assert detail is not None
		g = detail.groups.get(group_id)
		if g is None:
			raise error.GroupDoesNotExist()
		if new_name is not None:
			if len(new_name) > MAX_GROUP_NAME_LENGTH:
				raise error.GroupNameTooLong()
			g.name = new_name
		if is_favorite is not None:
			g.is_favorite = is_favorite
		self.backend._mark_modified(user)
	
	def me_group_contact_add(self, group_id: str, contact_uuid: str) -> None:
		if group_id == '0': return
		user = self.user
		detail = user.detail
		assert detail is not None
		if group_id not in detail.groups:
			raise error.GroupDoesNotExist()
		ctc = detail.contacts.get(contact_uuid)
		if ctc is None:
			raise error.ContactDoesNotExist()
		if group_id in ctc.groups:
			raise error.ContactAlreadyOnList()
		ctc.groups.add(group_id)
		self.backend._mark_modified(user)
	
	def me_group_contact_remove(self, group_id: str, contact_uuid: str) -> None:
		user = self.user
		detail = user.detail
		assert detail is not None
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
		self.backend._mark_modified(user)
	
	def me_contact_add(self, contact_uuid: str, lst: Lst, name: Optional[str]) -> Tuple[Contact, User]:
		ctc_head = self.backend._load_user_record(contact_uuid)
		if ctc_head is None:
			raise error.UserDoesNotExist()
		user = self.user
		ctc = self._add_to_list(user, ctc_head, lst, name)
		if lst is Lst.FL:
			# FL needs a matching RL on the contact
			self._add_to_list(ctc_head, user, Lst.RL, user.status.name)
			self._notify_reverse_add(ctc_head)
		self.backend._sync_contact_statuses()
		self.backend._generic_notify(self)
		return ctc, ctc_head
	
	def _notify_reverse_add(self, user_added: User) -> None:
		user_adder = self.user
		# `user_added` was added to `user_adder`'s RL
		for sess_added in self.backend._sc.get_sessions_by_user(user_added):
			if sess_added == self: continue
			sess_added.evt.on_added_to_list(Lst.RL, user_adder)
	
	def me_contact_edit(self, contact_uuid: str, *, is_messenger_user: Optional[bool] = None) -> None:
		user = self.user
		detail = user.detail
		assert detail is not None
		ctc = detail.contacts.get(contact_uuid)
		if ctc is None:
			raise error.ContactDoesNotExist()
		if is_messenger_user is not None:
			ctc.is_messenger_user = is_messenger_user
		self.backend._mark_modified(user)
	
	def me_contact_remove(self, contact_uuid: str, lst: Lst) -> None:
		user = self.user
		detail = user.detail
		assert detail is not None
		ctc = detail.contacts.get(contact_uuid)
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
		self.backend._mark_modified(user)
		self.backend._sync_contact_statuses()
	
	def _add_to_list(self, user: User, ctc_head: User, lst: Lst, name: Optional[str]) -> Contact:
		# Add `ctc_head` to `user`'s `lst`
		detail = self.backend._load_detail(user)
		contacts = detail.contacts
		if ctc_head.uuid not in contacts:
			contacts[ctc_head.uuid] = Contact(ctc_head, set(), 0, UserStatus(name))
		ctc = contacts[ctc_head.uuid]
		if ctc.status.name is None:
			ctc.status.name = name
		ctc.lists |= lst
		self.backend._mark_modified(user, detail = detail)
		return ctc
	
	def _remove_from_list(self, user: User, ctc_head: User, lst: Lst) -> None:
		# Remove `ctc_head` from `user`'s `lst`
		detail = self.backend._load_detail(user)
		contacts = detail.contacts
		ctc = contacts.get(ctc_head.uuid)
		if ctc is None: return
		ctc.lists &= ~lst
		if not ctc.lists:
			del contacts[ctc_head.uuid]
		self.backend._mark_modified(user, detail = detail)
	
	def me_pop_boot_others(self) -> None:
		for sess_other in self.backend._sc.get_sessions_by_user(self.user):
			if self is sess_other: continue
			sess_other.evt.on_pop_boot()
	
	def me_pop_notify_others(self) -> None:
		for sess_other in self.backend.util_get_sessions_by_user(self.user):
			if self is sess_other: continue
			sess_other.evt.on_pop_notify()

class YahooBackendSession(Session):
	__slots__ = ('backend', 'user_yahoo', 'client', 'evt')
	
	backend: Backend
	user_yahoo: UserYahoo
	client: Client
	evt: event.YahooBackendEventHandler
	
	def __init__(self, backend: Backend, user_yahoo: UserYahoo, client: Client, evt: event.YahooBackendEventHandler) -> None:
		super().__init__()
		self.backend = backend
		self.user_yahoo = user_yahoo
		self.client = client
		self.evt = evt
	
	def _on_close(self) -> None:
		self.evt.on_close()
		self.backend.on_leave_yahoo(self)
	
	def send_login_presence(self):
		self.backend._sync_yahoo_contact_statuses()
		self.backend._yahoo_notify_login_presence(self)
	
	def me_group_add(self, name: str, *, is_favorite: Optional[bool] = None) -> Group:
		user_yahoo = self.user_yahoo
		detail = user_yahoo.detail
		assert detail is not None
		group = Group(_gen_group_id(detail), name, is_favorite = is_favorite)
		detail.groups[group.id] = group
		self.backend._yahoo_mark_modified(user_yahoo)
		return group
	
	def me_group_remove(self, group_id: str) -> None:
		if group_id == '0':
			raise error.CannotRemoveSpecialGroup()
		user_yahoo = self.user_yahoo
		detail = user_yahoo.detail
		assert detail is not None
		try:
			del detail.groups[group_id]
		except KeyError:
			raise error.GroupDoesNotExist()
		for ctc in detail.contacts.values():
			ctc.groups.discard(group_id)
		self.backend._yahoo_mark_modified(user_yahoo)
	
	def me_group_edit(self, old_name: str, new_name: str) -> None:
		g = None
		
		user_yahoo = self.user_yahoo
		detail = user_yahoo.detail
		assert detail is not None
		for grp in detail.groups.values():
			if grp.name == old_name:
				g = detail.groups.get(grp.id)
				break
		if g is None:
			raise error.GroupDoesNotExist()
		if new_name is not None:
			g.name = new_name
		self.backend._yahoo_mark_modified(user_yahoo)
	
	def me_group_contact_add(self, group_id: str, contact_uuid: str) -> None:
		if group_id == '0': return
		user_yahoo = self.user_yahoo
		detail = user_yahoo.detail
		assert detail is not None
		if group_id not in detail.groups:
			raise error.GroupDoesNotExist()
		ctc = detail.contacts.get(contact_uuid)
		if ctc is None:
			raise error.ContactDoesNotExist()
		if group_id in ctc.groups:
			raise error.ContactAlreadyOnList()
		ctc.groups.add(group_id)
		self.backend._yahoo_mark_modified(user_yahoo)
	
	def me_group_contact_move(self, group_id: str, contact_uuid: str) -> None:
		if group_id == '0': return
		user_yahoo = self.user_yahoo
		detail = user_yahoo.detail
		assert detail is not None
		if group_id not in detail.groups:
			raise error.GroupDoesNotExist()
		ctc = detail.contacts.get(contact_uuid)
		if ctc is None:
			raise error.ContactDoesNotExist()
		if group_id in ctc.groups:
			raise error.ContactAlreadyOnList()
		ctc.groups = set()
		ctc.groups.add(group_id)
		self.backend._yahoo_mark_modified(user_yahoo)
	
	def me_check_empty_groups(self):
		group_filled = None
		groups_to_delete = []
		
		user_yahoo = self.user_yahoo
		detail = user_yahoo.detail
		assert detail is not None
		contacts = detail.contacts
		groups = detail.groups
		
		for grp in groups.values():
			group_filled = False
			cs = [c for c in contacts.values()]
			if cs:
				for c in cs:
					if grp.id in c.groups:
						group_filled = True
						break
			
			if group_filled: continue
			groups_to_delete.append(grp.id)
		
		for group_id in groups_to_delete: self.me_group_remove(group_id)
	
	def me_status_update(self, status_new: int, message: Optional[Dict[str, Any]] = None, send_presence: bool = True) -> None:
		user_yahoo = self.user_yahoo
		status = user_yahoo.status
		assert status is not None
		old_status = status.substatus
		status.substatus = status_new
		if message is not None:
			status.message = message
		else:
			status.message = {'text': '', 'is_away_message': 0}
		
		self.backend._sync_yahoo_contact_statuses()
		
		if send_presence:
			if old_status == YMSGStatus.Invisible:
				self.backend._yahoo_notify_invisible_presence(self)
			elif status_new == YMSGStatus.Available:
				self.backend._yahoo_notify_presence(self)
			elif status_new == YMSGStatus.Invisible and not old_status = YMSGStatus.Offline:
				self.backend._yahoo_notify_logout(self)
			else:
				self.backend._yahoo_notify_absence(self)
	
	def me_contact_add(self, ctc_head: UserYahoo, group: Group, request_message: Optional[str], utf8: Optional[str]):
		if ctc_head is None:
			raise error.UserDoesNotExist()
		user_adder = self.user_yahoo
		detail = user_adder.detail
		contacts = detail.contacts
		
		ctc = self._add_to_yahoo_list(user_adder, ctc_head)
		self.me_group_contact_add(group.id, ctc_head.uuid)
		
		for sess_added in self.backend._ysc.get_sessions_by_user(ctc_head):
			if sess_added == self: continue
			sess_added.evt.on_init_contact_request(user_adder, ctc_head, request_message, utf8)
		
		self.backend._sync_yahoo_contact_statuses()
		return ctc_head
	
	def me_contact_deny(self, adder_uuid: str, deny_message: Optional[str]):
		adder_head = self.backend._load_yahoo_user_record(adder_uuid)
		if adder_head is None:
			raise error.UserDoesNotExist()
		user_yahoo = self.user_yahoo
		
		for sess_added in self.backend._ysc.get_sessions_by_user(adder_head):
			if sess_added == self: continue
			sess_added.evt.on_deny_contact_request(user_yahoo, deny_message)
		
		self.backend._sync_yahoo_contact_statuses()
	
	def me_contact_remove(self, contact_uuid: str) -> None:
		user_yahoo = self.user_yahoo
		detail = user_yahoo.detail
		assert detail is not None
		ctc = detail.contacts.get(contact_uuid)
		if ctc is None:
			raise error.ContactDoesNotExist()
		self._remove_from_yahoo_list(user_yahoo, ctc.head)
		self.backend._yahoo_mark_modified(user_yahoo)
		self.backend._sync_yahoo_contact_statuses()
	
	def me_contact_add_ignore(self, contact_uuid: str, yahoo_id: Optional[str]) -> None:
		ctc_head = self.backend._load_yahoo_user_record(contact_uuid)
		if ctc_head is None:
			raise error.UserDoesNotExist()
		user_yahoo = self.user_yahoo
		ctc = self._add_to_yahoo_list(user_yahoo, ctc_head, yahoo_id)
		self.backend._sync_yahoo_contact_statuses()
	
	def _add_to_yahoo_list(self, user_yahoo: UserYahoo, ctc_head: UserYahoo) -> YahooContact:
		# Add `ctc_head` to `user_yahoo`'s buddy list
		detail = self.backend._load_yahoo_detail(user_yahoo)
		contacts = detail.contacts
		if ctc_head.uuid not in contacts:
			contacts[ctc_head.uuid] = YahooContact(ctc_head, ctc_head.yahoo_id, set(), UserYahooStatus())
		ctc = contacts[ctc_head.uuid]
		if ctc.yahoo_id is None:
			ctc.yahoo_id = ctc_head.yahoo_id
		self.backend._yahoo_mark_modified(user_yahoo, detail = detail)
		return ctc
	
	def _remove_from_yahoo_list(self, user_yahoo: UserYahoo, ctc_head: UserYahoo) -> None:
		# Remove `ctc_head` from `user_yahoo`'s buddy list
		detail = self.backend._load_yahoo_detail(user_yahoo)
		contacts = detail.contacts
		ctc = contacts.get(ctc_head.uuid)
		if ctc is None: return
		del contacts[ctc_head.uuid]
		self.backend._yahoo_mark_modified(user_yahoo, detail = detail)
	
	def me_send_notify_pkt(self, uuid: str, notify_dict: Dict[str, Any]):
		head = self.backend._load_yahoo_user_record(uuid)
		if head is None:
			return
		self.backend._yahoo_notify_im_notify(self, head, notify_dict)
	
	def me_send_im(self, uuid: str, message_dict: Dict[str, Any]):
		head = self.backend._load_yahoo_user_record(uuid)
		if head is None:
			return
		
		stats = self.backend._stats
		client = self.client
		
		stats.on_message_sent(self.user_yahoo, client)
		stats.on_user_active(self.user_yahoo, client)
		
		self.backend._yahoo_im_message(self, head, message_dict)
		stats.on_message_received(head, client)
	
	def me_send_filexfer(self, uuid: str, xfer_dict: Dict[str, Any]):
		head = self.backend._load_yahoo_user_record(uuid)
		if head is None:
			return
		self.backend._yahoo_init_ft(self, head, xfer_dict)
	
	def me_decline_conf_invite(self, inviter: UserYahoo, conf_id: str, deny_msg: Optional[str]):
		self.backend._yahoo_decline_conf_invite(self, inviter, conf_id, deny_msg)

class _SessionCollection:
	def __init__(self):
		self._sessions = set() # type: Set[BackendSession]
		self._sessions_by_user = defaultdict(set) # type: Dict[User, Set[BackendSession]]
		self._sess_by_token = {} # type: Dict[str, BackendSession]
		self._tokens_by_sess = defaultdict(set) # type: Dict[BackendSession, Set[str]]
	
	def get_sessions_by_user(self, user: User) -> Iterable[BackendSession]:
		if user not in self._sessions_by_user:
			return EMPTY_SET
		return self._sessions_by_user[user]
	
	def iter_sessions(self) -> Iterable[BackendSession]:
		yield from self._sessions
	
	def set_nc_by_token(self, sess: BackendSession, token: str) -> None:
		self._sess_by_token[token] = sess
		self._tokens_by_sess[sess].add(token)
		self._sessions.add(sess)
	
	def get_nc_by_token(self, token: str) -> Optional[BackendSession]:
		return self._sess_by_token.get(token)
	
	def add_session(self, sess: BackendSession) -> None:
		if sess.user:
			self._sessions_by_user[sess.user].add(sess)
		self._sessions.add(sess)
	
	def remove_session(self, sess: BackendSession) -> None:
		if sess in self._tokens_by_sess:
			tokens = self._tokens_by_sess.pop(sess)
			for token in tokens:
				self._sess_by_token.pop(token, None)
		self._sessions.discard(sess)
		if sess.user in self._sessions_by_user:
			self._sessions_by_user[sess.user].discard(sess)

class _YahooSessionCollection:
	def __init__(self):
		self._sessions = set() # type: Set[YahooBackendSession]
		self._sessions_by_user = defaultdict(set) # type: Dict[YahooUser, Set[YahooBackendSession]]
		self._sess_by_id = {} # type: Dict[str, YahooBackendSession]
		self._ids_by_sess = defaultdict(set) # type: Dict[YahooBackendSession, Set[str]]
	
	def get_sessions_by_user(self, user_yahoo: UserYahoo) -> Iterable[YahooBackendSession]:
		if user_yahoo not in self._sessions_by_user:
			return EMPTY_SET
		return self._sessions_by_user[user_yahoo]
	
	def iter_sessions(self) -> Iterable[YahooBackendSession]:
		yield from self._sessions
	
	def set_nc_by_id(self, sess: YahooBackendSession, sess_id: str) -> None:
		self._sess_by_id[sess_id] = sess
		self._ids_by_sess[sess].add(sess_id)
		self._sessions.add(sess)
	
	def get_nc_by_id(self, sess_id: str) -> Optional[YahooBackendSession]:
		return self._sess_by_id.get(sess_id)
	
	def add_session(self, sess: YahooBackendSession) -> None:
		if sess.user_yahoo:
			self._sessions_by_user[sess.user_yahoo].add(sess)
		self._sessions.add(sess)
	
	def remove_session(self, sess: YahooBackendSession) -> None:
		if sess in self._ids_by_sess:
			ids = self._ids_by_sess.pop(sess)
			for id in ids:
				self._sess_by_id.pop(sess_id, None)
		self._sessions.discard(sess)
		if sess.user_yahoo in self._sessions_by_user:
			self._sessions_by_user[sess.user_yahoo].discard(sess)

class Chat:
	__slots__ = ('id', '_users_by_sess', '_stats')
	
	id: str
	_users_by_sess: Dict['ChatSession', User]
	_stats: Any
	
	def __init__(self, stats: Any) -> None:
		super().__init__()
		self.id = gen_uuid()
		self._users_by_sess = {}
		self._stats = stats
	
	def join(self, bs: BackendSession, evt: event.ChatEventHandler) -> 'ChatSession':
		cs = ChatSession(bs, self, evt)
		self._users_by_sess[cs] = cs.user
		cs.evt.on_open()
		return cs
	
	def add_session(self, sess: 'ChatSession') -> None:
		self._users_by_sess[sess] = sess.user
	
	def get_roster(self) -> Iterable['ChatSession']:
		return self._users_by_sess.keys()
	
	def send_participant_joined(self, cs: 'ChatSession') -> None:
		for cs_other in self.get_roster():
			cs_other.evt.on_participant_joined(cs)
	
	def on_leave(self, sess: 'ChatSession') -> None:
		su = self._users_by_sess.pop(sess, None)
		if su is None: return
		# Notify others that `sess` has left
		for sess1, _ in self._users_by_sess.items():
			if sess1 is sess: continue
			sess1.evt.on_participant_left(sess)

class Conference:
	__slots__ = ('id', '_users_by_sess', '_stats')
	
	id: str
	_users_by_sess: Dict['ConferenceSession', UserYahoo]
	_stats: Any
	
	def __init__(self, stats: Any, id: str) -> None:
		super().__init__()
		self.id = id
		self._users_by_sess = {}
		self._stats = stats
	
	def join(self, ybs: YahooBackendSession, evt: event.ConferenceEventHandler) -> 'ConferenceSession':
		cs = ConferenceSession(ybs, self, evt)
		self._users_by_sess[cs] = cs.user_yahoo
		cs.evt.on_open()
		return cs
	
	def add_session(self, sess_yahoo: 'ConferenceSession') -> None:
		self._users_by_sess[sess] = sess.user_yahoo
	
	def get_roster(self) -> Iterable['ConferenceSession']:
		return self._users_by_sess.keys()
	
	def send_participant_joined(self, cs: 'ConferenceSession') -> None:
		for cs_other in self.get_roster():
			if cs_other is cs: continue
			cs_other.evt.on_participant_joined(cs)
	
	def on_leave(self, sess: 'ConferenceSession', conf_roster: Optional[List[str]]) -> None:
		su = self._users_by_sess.pop(sess, None)
		if su is None: return
		# If `conf_roster` is None, generate our own `conf_roster`
		if conf_roster is None:
			roster_tmp = []
			for invitee in list(self._users_by_sess.values()):
				roster_tmp.append(invitee.yahoo_id)
			conf_roster = roster_tmp
			del roster_tmp
		# Notify others that `sess` has left
		for sess1, _ in self._users_by_sess.items():
			if sess1 is sess: continue
			if sess1.user_yahoo.yahoo_id in conf_roster:
				sess1.evt.on_participant_left(sess)

class ChatSession(Session):
	__slots__ = ('user', 'chat', 'bs', 'evt')
	
	user: User
	chat: Chat
	bs: BackendSession
	evt: event.ChatEventHandler
	
	def __init__(self, bs: BackendSession, chat: Chat, evt: event.ChatEventHandler) -> None:
		super().__init__()
		self.user = bs.user
		self.chat = chat
		self.bs = bs
		self.evt = evt
	
	def _on_close(self) -> None:
		self.evt.on_close()
		self.chat.on_leave(self)
	
	def invite(self, invitee_uuid: str) -> None:
		detail = self.user.detail
		assert detail is not None
		ctc = detail.contacts.get(invitee_uuid)
		if ctc is None:
			if self.user.uuid != invitee_uuid: raise error.ContactDoesNotExist()
			invitee = self.user
		else:
			if ctc.status.is_offlineish(): raise error.ContactNotOnline()
			invitee = ctc.head
		ctc_sessions = self.bs.backend.util_get_sessions_by_user(invitee)
		if not ctc_sessions: raise error.ContactNotOnline()
		for ctc_sess in ctc_sessions:
			ctc_sess.evt.on_chat_invite(self.chat, self.user)
	
	def send_message_to_everyone(self, data: bytes) -> None:
		stats = self.chat._stats
		client = self.bs.client
		
		stats.on_message_sent(self.user, client)
		stats.on_user_active(self.user, client)
		
		for cs_other in self.chat._users_by_sess.keys():
			if cs_other is self: continue
			cs_other.evt.on_message(self.user, data)
			stats.on_message_received(cs_other.user, client)

class ConferenceSession(Session):
	__slots__ = ('user_yahoo', 'conf', 'ybs', 'evt')
	
	user_yahoo: UserYahoo
	conf: Conference
	ybs: YahooBackendSession
	evt: event.ConferenceEventHandler
	
	def __init__(self, ybs: YahooBackendSession, conf: Conference, evt: event.ConferenceEventHandler) -> None:
		super().__init__()
		self.user_yahoo = ybs.user_yahoo
		self.conf = conf
		self.ybs = ybs
		self.evt = evt
	
	def close(self, conf_roster: Optional[List[str]]) -> None:
		if self.closed:
			return
		self.closed = True
		self._on_close(conf_roster)
	
	def _on_close(self, conf_roster: Optional[List[str]]) -> None:
		self.conf.on_leave(self, conf_roster)
	
	def invite(self, invitee_uuid: str, invite_msg: Optional[str], conf_roster: List[str], voice_chat: int, existing: bool = False) -> None:
		invitee = self.ybs.backend._load_yahoo_user_record(invitee_uuid)
		ctc_sessions = self.ybs.backend.util_get_sessions_by_user_yahoo(invitee)
		if not ctc_sessions: raise error.ContactNotOnline()
		for ctc_sess in ctc_sessions:
			ctc_sess.evt.on_conf_invite(self.conf, self.user_yahoo, invite_msg, conf_roster, voice_chat, existing_conf = existing)
	
	def send_message_to_everyone(self, conf_id: str, message_dict: Dict[str, Any]) -> None:
		stats = self.conf._stats
		client = self.ybs.client
		
		stats.on_message_sent(self.user_yahoo, client)
		stats.on_user_active(self.user_yahoo, client)
		
		for cs_other in self.conf._users_by_sess.keys():
			if cs_other is self: continue
			if cs_other.conf.id != conf_id: continue
			cs_other.evt.on_message(self.user_yahoo, message_dict)
			stats.on_message_received(cs_other.user_yahoo, client)

def _gen_group_id(detail: UserDetail) -> str:
	id = 1
	s = str(id)
	while s in detail.groups:
		id += 1
		s = str(id)
	return s
	
MAX_GROUP_NAME_LENGTH = 61