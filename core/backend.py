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
from .models import User, UserDetail, Group, Lst, Contact, UserStatus
from . import error, event
from util.yahoo.Y64 import Y64Encode

class Ack(IntFlag):
	Zero = 0
	NAK = 1
	ACK = 2
	Full = 3

class Backend:
	__slots__ = ('user_service', 'auth_service', '_loop', '_stats', '_sc', '_user_by_uuid', '_unsynced_db', '_runners')
	
	user_service: UserService
	auth_service: AuthService
	_loop: asyncio.AbstractEventLoop
	_stats: Stats
	_sc: '_SessionCollection'
	_user_by_uuid: Dict[str, User]
	_unsynced_db: Dict[User, UserDetail]
	_runners: List[Runner]
	
	def __init__(self, loop: asyncio.AbstractEventLoop, *, user_service: Optional[UserService] = None, auth_service: Optional[AuthService] = None) -> None:
		self.user_service = user_service or UserService()
		self.auth_service = auth_service or AuthService()
		self._loop = loop
		self._stats = Stats()
		self._sc = _SessionCollection()
		self._user_by_uuid = {}
		self._unsynced_db = {}
		self._runners = []
		
		loop.create_task(self._sync_db())
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
	
	def _load_user_record(self, uuid: str) -> Optional[User]:
		if uuid not in self._user_by_uuid:
			user = self.user_service.get(uuid)
			if user is None: return None
			self._user_by_uuid[uuid] = user
		return self._user_by_uuid[uuid]
	
	def _load_detail(self, user: User) -> UserDetail:
		if user.detail: return user.detail
		return self.user_service.get_detail(user.uuid)
	
	def chat_create(self) -> 'Chat':
		return Chat(self._stats)
	
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
	
	def _sync_contact_statuses(self) -> None:
		# Recompute all `Contact.status`'s
		for user in self._user_by_uuid.values():
			detail = user.detail
			if detail is None: continue
			for ctc in detail.contacts.values():
				ctc.compute_visible_status(user)
	
	def _mark_modified(self, user: User, *, detail: Optional[UserDetail] = None) -> None:
		ud = user.detail or detail
		if detail: assert ud is detail
		assert ud is not None
		self._unsynced_db[user] = ud
	
	def util_get_uuid_from_email(self, email: str) -> Optional[str]:
		return self.user_service.get_uuid(email)
	
	def util_set_sess_token(self, sess: 'BackendSession', token: str) -> None:
		self._sc.set_nc_by_token(sess, token)
	
	def util_get_sess_by_token(self, token: str) -> Optional['BackendSession']:
		return self._sc.get_nc_by_token(token)
	
	def util_get_sessions_by_user(self, user: User) -> Iterable['BackendSession']:
		return self._sc.get_sessions_by_user(user)
	
	async def _sync_db(self) -> None:
		while True:
			await asyncio.sleep(1)
			self._sync_db_impl()
	
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
	
	def generate_challenge_v1(self) -> str:
		# Yahoo64-encode the raw 16 bytes of a UUID
		return Y64Encode(uuid4().bytes)
	
	def verify_challenge_v1(user_y: str, chal: bytes, resp_6: bytes, resp_96: bytes) -> bool:
		from db import Session
		
		# Yahoo! clients tend to remove "@yahoo.com" if a user logs in, but it might not check for other domains; double check for that
		email = user_y
		if '@' not in email:
			email += '@yahoo.com'
		
		with Session() as sess:
			dbuser = sess.query(DBUser).filter(DBUser.email == email).one_or_none()
			if dbuser is None: return False
			fd = (dbuser.front_data or {}).get('ymsg') or {}
		# Retrieve Yahoo64-encoded MD5 hash of the user's password from the database
		# NOTE: The MD5 hash of the password is literally unsalted. Good grief, Yahoo!
		pass_md5 = fd.get('pw_md5') or ''
		# Retreive MD5-crypt(3)'d hash of the user's password from the database
		pass_md5crypt = fd.get('pw_md5crypt') or ''
		
		mode = ord(chal[15]) % 8
		
		# Note that the "checksum" is not a static character
		checksum = chal[ord(chal[CHECKSUM_POS[mode]]) % 16]
		
		resp6_md5 = md5()
		resp6_md5.update(checksum)
		resp6_md5.update(_chal_combine(user_y, pass_md5, chal, mode))
		resp_6_server = Y64Encode(resp6_md5.digest())
		
		# TODO: Only the first response string generated on the server side is correct for some odd reason.
		# Either YMSG10's response function is slightly modified or something is wrong.
		if resp_6 == resp_6_server:
			return True
		
		resp96_md5 = md5()
		resp96_md5.update(checksum)
		resp96_md5.update(_chal_combine(user_y, Y64Encode(md5(pass_md5crypt.encode()).digest()), chal, mode))
		resp_96_server = Y64Encode(resp96_md5.digest())
		
		return resp_96 == resp_6_server

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

def _gen_group_id(detail: UserDetail) -> str:
	id = 1
	s = str(id)
	while s in detail.groups:
		id += 1
		s = str(id)
	return s
	
MAX_GROUP_NAME_LENGTH = 61

# Yahoo-specific functions and variables

CHECKSUM_POS = (
	7, 9, 15, 1, 3, 7, 9, 15
)

USERNAME = 0
PASSWORD = 1
CHALLENGE = 2

STRING_ORDER = (
	(PASSWORD, USERNAME, CHALLENGE),
	(USERNAME, CHALLENGE, PASSWORD),
	(CHALLENGE, PASSWORD, USERNAME),
	(USERNAME, PASSWORD, CHALLENGE),
	(PASSWORD, CHALLENGE, USERNAME),
	(PASSWORD, USERNAME, CHALLENGE),
	(USERNAME, CHALLENGE, PASSWORD),
	(CHALLENGE, PASSWORD, USERNAME)
)

def _chal_combine(username, passwd, chal, mode):
	out = ''
	cred_arr = [username, passwd, chal]
	
	for i in range(0, 2):
		out += cred_arr[STRING_ORDER[mode][i]]
	
	return out
