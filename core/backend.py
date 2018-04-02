from typing import Dict, List, Set, Any, Tuple, Optional, Sequence, FrozenSet, Iterable
from abc import ABCMeta, abstractmethod
import asyncio, time
from collections import defaultdict
from enum import IntFlag

from util.misc import gen_uuid, EMPTY_SET, run_loop, Runner

from .user import UserService
from .auth import AuthService
from .stats import Stats
from .client import Client
from .models import User, UserDetail, Group, Lst, Contact, UserStatus, TextWithData, MessageData, Substatus
from . import error, event

class Ack(IntFlag):
	Zero = 0
	NAK = 1
	ACK = 2
	Full = 3

class Backend:
	__slots__ = (
		'user_service', 'auth_service', 'loop', '_stats', '_sc',
		'_chats_by_id', '_user_by_uuid', '_unsynced_db', '_runners',
		'_dev',
	)
	
	user_service: UserService
	auth_service: AuthService
	loop: asyncio.AbstractEventLoop
	_stats: Stats
	_sc: '_SessionCollection'
	_chats_by_id: Dict[Tuple[str, str], 'Chat']
	_user_by_uuid: Dict[str, User]
	_unsynced_db: Dict[User, UserDetail]
	_runners: List[Runner]
	_dev: Optional[Any]
	
	def __init__(self, loop: asyncio.AbstractEventLoop, *, user_service: Optional[UserService] = None, auth_service: Optional[AuthService] = None) -> None:
		self.user_service = user_service or UserService()
		self.auth_service = auth_service or AuthService()
		self.loop = loop
		self._stats = Stats()
		self._sc = _SessionCollection()
		self._chats_by_id = {}
		self._user_by_uuid = {}
		self._unsynced_db = {}
		self._runners = []
		self._dev = None
		
		loop.create_task(self._sync_db())
		loop.create_task(self._clean_sessions())
		loop.create_task(self._sync_stats())
	
	def add_runner(self, runner: Runner) -> None:
		self._runners.append(runner)
	
	def run_forever(self) -> None:
		run_loop(self.loop, self._runners)
	
	def on_leave(self, sess: 'BackendSession') -> None:
		user = sess.user
		old_substatus = user.status.substatus
		self._stats.on_logout()
		self._sc.remove_session(sess)
		if self._sc.get_sessions_by_user(user):
			# There are still other people logged in as this user,
			# so don't send offline notifications.
			return
		# User is offline, send notifications
		user.detail = None
		self._sync_contact_statuses()
		self._generic_notify(sess, old_substatus = old_substatus)
	
	def login(self, uuid: str, client: Client, evt: event.BackendEventHandler, *, front_needs_self_notify: bool = False) -> Optional['BackendSession']:
		user = self._load_user_record(uuid)
		if user is None: return None
		self.user_service.update_date_login(uuid)
		bs = BackendSession(self, user, client, evt, front_needs_self_notify = front_needs_self_notify)
		bs.evt.bs = bs
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
		detail = self.user_service.get_detail(user.uuid)
		assert detail is not None
		return detail
	
	def chat_create(self, *, twoway_only: bool = False) -> 'Chat':
		return Chat(self, self._stats, twoway_only = twoway_only)
	
	def chat_get(self, scope: str, id: str) -> Optional['Chat']:
		return self._chats_by_id.get((scope, id))
	
	def _generic_notify(self, bs: 'BackendSession', *, old_substatus: Substatus) -> None:
		# TODO: This should be done async, with a slight delay, to reduce unnecessary traffic.
		# (Similar to how `_unsynced_db` works.)
		# Notify relevant `BackendSession`s of status, name, message, media
		user = bs.user
		detail = self._load_detail(user)
		for ctc in detail.contacts.values():
			for bs_other in self._sc.get_sessions_by_user(ctc.head):
				if bs_other is bs and not bs.front_needs_self_notify: continue
				detail_other = bs_other.user.detail
				if detail_other is None: continue
				ctc_me = detail_other.contacts.get(user.uuid)
				# This shouldn't be `None`, since every contact should have
				# an `RL` contact on the other users' list (at the very least).
				if ctc_me is None: continue
				if not ctc_me.lists & Lst.FL: continue
				bs_other.evt.on_presence_notification(ctc_me, old_substatus)
	
	def _sync_contact_statuses(self) -> None:
		# TODO: This should be done async, with a slight delay, to reduce unnecessary traffic.
		# (Similar to how `_unsynced_db` works.)
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
	
	def dev_connect(self, obj: object) -> None:
		if self._dev is None: return
		self._dev.connect(obj)
	
	def dev_disconnect(self, obj: object) -> None:
		if self._dev is None: return
		self._dev.disconnect(obj)
	
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
	__slots__ = ('backend', 'user', 'client', 'evt', 'front_data', 'front_needs_self_notify')
	
	backend: Backend
	user: User
	client: Client
	evt: event.BackendEventHandler
	front_data: Dict[str, Any]
	front_needs_self_notify: bool
	
	def __init__(self, backend: Backend, user: User, client: Client, evt: event.BackendEventHandler, *, front_needs_self_notify: bool = False) -> None:
		super().__init__()
		self.backend = backend
		self.user = user
		self.client = client
		self.evt = evt
		self.front_data = {}
		self.front_needs_self_notify = front_needs_self_notify
	
	def _on_close(self) -> None:
		self.evt.on_close()
		self.backend.on_leave(self)
	
	def me_update(self, fields: Dict[str, Any]) -> None:
		user = self.user
		detail = user.detail
		assert detail is not None
		
		old_substatus = user.status.substatus
		
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
		self.backend._generic_notify(self, old_substatus = old_substatus)
	
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
	
	def me_contact_add(self, contact_uuid: str, lst: Lst, *, name: Optional[str] = None, message: Optional[TextWithData] = None) -> Tuple[Contact, User]:
		backend = self.backend
		ctc_head = backend._load_user_record(contact_uuid)
		if ctc_head is None:
			raise error.UserDoesNotExist()
		user = self.user
		ctc = self._add_to_list(user, ctc_head, lst, name)
		if lst is Lst.FL:
			# FL needs a matching RL on the contact
			ctc_me = self._add_to_list(ctc_head, user, Lst.RL, user.status.name)
			# If other user hasn't already allowed/blocked me, notify them that I added them to my list.
			if not ctc_me.lists & (Lst.AL | Lst.BL):
				# `ctc_head` was added to `user`'s RL
				for sess_added in backend._sc.get_sessions_by_user(ctc_head):
					if sess_added is self: continue
					sess_added.evt.on_added_me(user, message = message)
		backend._sync_contact_statuses()
		self.evt.on_presence_notification(ctc, old_substatus = Substatus.Offline)
		backend._generic_notify(self, old_substatus = Substatus.Offline)
		return ctc, ctc_head
	
	def me_contact_edit(self, contact_uuid: str, *, is_messenger_user: Optional[bool] = None) -> None:
		user = self.user
		detail = user.detail
		assert detail is not None
		ctc = detail.contacts.get(contact_uuid)
		if ctc is None:
			raise error.ContactDoesNotExist()
		
		updated = False
		
		orig_is_messenger_user = ctc.is_messenger_user
		if is_messenger_user is not None:
			ctc.is_messenger_user = is_messenger_user
		
		if is_messenger_user != orig_is_messenger_user:
			updated = True
		
		if updated:
			self.backend._mark_modified(user)
	
	def me_contact_remove(self, contact_uuid: str, lst: Lst) -> None:
		user = self.user
		detail = user.detail
		assert detail is not None
		ctc = detail.contacts.get(contact_uuid)
		if ctc is None:
			raise error.ContactDoesNotExist()
		assert lst is not Lst.RL
		self._remove_from_list(user, ctc.head, lst)
		if lst is Lst.FL:
			ctc.groups = set()
			# Remove matching RL
			self._remove_from_list(ctc.head, user, Lst.RL)
		self.backend._sync_contact_statuses()
	
	def me_contact_deny(self, adder_uuid: str, deny_message: Optional[str]):
		user_adder = self.backend._load_user_record(adder_uuid)
		if user_adder is None:
			raise error.UserDoesNotExist()
		user = self.user
		for sess_adder in self.backend._sc.get_sessions_by_user(user_adder):
			if sess_adder is self: continue
			sess_adder.evt.on_contact_request_denied(user, deny_message or '')
	
	def _add_to_list(self, user: User, ctc_head: User, lst: Lst, name: Optional[str]) -> Contact:
		# Add `ctc_head` to `user`'s `lst`
		detail = self.backend._load_detail(user)
		contacts = detail.contacts
		if ctc_head.uuid not in contacts:
			contacts[ctc_head.uuid] = Contact(ctc_head, set(), Lst.Empty, UserStatus(name))
		ctc = contacts[ctc_head.uuid]
		
		updated = False
		
		orig_name = ctc.status.name
		if ctc.status.name is None:
			ctc.status.name = name
		
		if orig_name != name:
			updated = True
		
		# If I add someone to FL, and they're not already blocked,
		# they should also be added to AL.
		if lst == Lst.FL and not ctc.lists & Lst.BL:
			lst = lst | Lst.AL
		
		if (ctc.lists & lst) != lst:
			ctc.lists |= lst
			updated = True
		
		if updated:
			self.backend._mark_modified(user, detail = detail)
		
		return ctc
	
	def _remove_from_list(self, user: User, ctc_head: User, lst: Lst) -> None:
		# Remove `ctc_head` from `user`'s `lst`
		detail = self.backend._load_detail(user)
		contacts = detail.contacts
		ctc = contacts.get(ctc_head.uuid)
		if ctc is None: return
		
		updated = False
		if ctc.lists & lst:
			ctc.lists &= ~lst
			updated = True
		
		if not ctc.lists:
			del contacts[ctc_head.uuid]
			updated = True
		
		if updated:
			self.backend._mark_modified(user, detail = detail)
	
	def me_contact_notify_oim(self, uuid: str, oim_uuid: str) -> None:
		ctc_head = self.backend._load_user_record(uuid)
		if ctc_head is None:
			raise error.UserDoesNotExist()
		
		for sess_notify in self.backend._sc.get_sessions_by_user(ctc_head):
			if sess_notify is self: continue
			sess_notify.evt.msn_on_oim_sent(oim_uuid)
	
	def me_pop_boot_others(self) -> None:
		for sess_other in self.backend._sc.get_sessions_by_user(self.user):
			if self is sess_other: continue
			sess_other.evt.on_pop_boot()
	
	def me_pop_notify_others(self) -> None:
		for sess_other in self.backend.util_get_sessions_by_user(self.user):
			if self is sess_other: continue
			sess_other.evt.on_pop_notify()

class _SessionCollection:
	__slots__ = ('_sessions', '_sessions_by_user', '_sess_by_token', '_tokens_by_sess')
	
	_sessions: Set[BackendSession]
	_sessions_by_user: Dict[User, Set[BackendSession]]
	_sess_by_token: Dict[str, BackendSession]
	_tokens_by_sess: Dict[BackendSession, Set[str]]
	
	def __init__(self) -> None:
		self._sessions = set()
		self._sessions_by_user = defaultdict(set)
		self._sess_by_token = {}
		self._tokens_by_sess = defaultdict(set)
	
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
	__slots__ = ('ids', 'backend', 'twoway_only', '_users_by_sess', '_stats')
	
	ids: Dict[str, str]
	backend: Backend
	twoway_only: bool
	_users_by_sess: Dict['ChatSession', User]
	_stats: Any
	
	def __init__(self, backend: Backend, stats: Any, *, twoway_only: bool = False) -> None:
		super().__init__()
		self.ids = {}
		self.backend = backend
		self.twoway_only = twoway_only
		self._users_by_sess = {}
		self._stats = stats
		
		self.add_id('main', gen_uuid())
	
	def add_id(self, scope: str, id: str):
		assert id not in self.backend._chats_by_id
		self.ids[scope] = id
		self.backend._chats_by_id[(scope, id)] = self
	
	def join(self, origin: str, bs: BackendSession, evt: event.ChatEventHandler) -> 'ChatSession':
		cs = ChatSession(origin, bs, self, evt)
		cs.evt.cs = cs
		self._users_by_sess[cs] = cs.user
		cs.evt.on_open()
		return cs
	
	def add_session(self, sess: 'ChatSession') -> None:
		self._users_by_sess[sess] = sess.user
	
	def get_roster(self) -> Iterable['ChatSession']:
		return self._users_by_sess.keys()
	
	def send_participant_joined(self, cs: 'ChatSession') -> None:
		for cs_other in self.get_roster():
			if cs_other is cs and cs.origin is 'yahoo': continue
			cs_other.evt.on_participant_joined(cs)
	
	def on_leave(self, sess: 'ChatSession') -> None:
		su = self._users_by_sess.pop(sess, None)
		if su is None: return
		# TODO: If it goes down to only 1 connected user,
		# the chat and remaining session(s) should be automatically closed.
		if not self._users_by_sess:
			for scope_id in self.ids.items():
				del self.backend._chats_by_id[scope_id]
			return
		# Notify others that `sess` has left
		for sess1, _ in self._users_by_sess.items():
			if sess1 is sess: continue
			sess1.evt.on_participant_left(sess)

class ChatSession(Session):
	__slots__ = ('origin', 'user', 'chat', 'bs', 'evt')
	
	origin: Optional[str]
	user: User
	chat: Chat
	bs: BackendSession
	evt: event.ChatEventHandler
	
	def __init__(self, origin: str, bs: BackendSession, chat: Chat, evt: event.ChatEventHandler) -> None:
		super().__init__()
		self.origin = origin
		self.user = bs.user
		self.chat = chat
		self.bs = bs
		self.evt = evt
	
	def _on_close(self) -> None:
		self.evt.on_close()
		self.chat.on_leave(self)
	
	def invite(self, invitee_uuid: str, *, invite_msg: Optional[str] = None, roster: Optional[List[str]] = None, voice_chat: Optional[int] = None, existing: bool = False) -> None:
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
		for ctc_sess in ctc_sessions:
			ctc_sess.evt.on_chat_invite(self.chat, self.user, invite_msg = invite_msg or '', roster = roster, voice_chat = voice_chat, existing = existing)
	
	def send_message_to_everyone(self, data: MessageData) -> None:
		stats = self.chat._stats
		client = self.bs.client
		
		stats.on_message_sent(self.user, client)
		stats.on_user_active(self.user, client)
		
		for cs_other in self.chat._users_by_sess.keys():
			if cs_other is self: continue
			cs_other.evt.on_message(data)
			stats.on_message_received(cs_other.user, client)
	
	def send_message_to_user(self, user_uuid: str, data: MessageData) -> None:
		stats = self.chat._stats
		client = self.bs.client
		
		stats.on_message_sent(self.user, client)
		stats.on_user_active(self.user, client)
		
		for cs_other in self.chat._users_by_sess.keys():
			if cs_other is self: continue
			if cs_other.user.uuid != user_uuid: continue
			cs_other.evt.on_message(data)
			stats.on_message_received(cs_other.user, client)

def _gen_group_id(detail: UserDetail) -> str:
	id = 1
	s = str(id)
	while s in detail.groups:
		id += 1
		s = str(id)
	return s
	
MAX_GROUP_NAME_LENGTH = 61
