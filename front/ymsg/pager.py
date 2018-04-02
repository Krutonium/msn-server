from typing import Optional, Dict, Any, List, Iterable, Set, Tuple
import secrets
import datetime
from multidict import MultiDict
import asyncio
import time
import binascii

from util.misc import Logger

from core import event, error
from core.backend import Backend, BackendSession, Chat, ChatSession
from core.models import Substatus, Lst, User, Contact, Group, TextWithData, MessageData, MessageType, UserStatus
from core.client import Client
from core.user import UserService
from core.auth import AuthService

from .ymsg_ctrl import YMSGCtrlBase
from .misc import YMSGService, YMSGStatus, yahoo_id_to_uuid
from . import misc, Y64

# "Pre" because it's needed before BackendSession is created.
PRE_SESSION_ID: Dict[str, int] = {}

class YMSGCtrlPager(YMSGCtrlBase):
	__slots__ = ('backend', 'dialect', 'yahoo_id', 'sess_id', 'challenge', 't_cookie_token', 'bs', 'private_chats', 'chat_sessions', 'client')
	
	backend: Backend
	dialect: int
	yahoo_id: str
	sess_id: int
	challenge: Optional[str]
	t_cookie_token: Optional[str]
	bs: Optional[BackendSession]
	private_chats: Dict[str, Tuple[ChatSession, 'ChatEventHandler']]
	chat_sessions: Dict[Chat, ChatSession]
	client: Client
	
	def __init__(self, logger: Logger, via: str, backend: Backend) -> None:
		super().__init__(logger)
		self.backend = backend
		self.dialect = 0
		self.yahoo_id = ''
		self.sess_id = 0
		self.challenge = None
		self.t_cookie_token = None
		self.bs = None
		self.private_chats = {}
		self.chat_sessions = {}
		self.client = Client('yahoo', '?', via)
	
	def _on_close(self) -> None:
		if self.yahoo_id:
			PRE_SESSION_ID.pop(self.yahoo_id, None)
		
		if self.bs:
			self.bs.close()
	
	# State = Auth
	
	def _y_004c(self, *args) -> None:
		# SERVICE_HANDSHAKE (0x4c); acknowledgement of the server
		
		self.client = Client('yahoo', 'YMSG' + str(args[0]), self.client.via)
		self.dialect = int(args[0])
		self.send_reply(YMSGService.Handshake, YMSGStatus.BRB, 0, None)
	
	def _y_0057(self, *args) -> None:
		# SERVICE_AUTH (0x57); send a challenge string for the client to craft two response strings with
		
		arg1 = args[4].get('1')
		assert isinstance(arg1, str)
		self.yahoo_id = arg1
		
		if yahoo_id_to_uuid(None, self.backend, self.yahoo_id) is None or self.yahoo_id.endswith('@yahoo.com'):
			self.send_reply(YMSGService.AuthResp, YMSGStatus.LoginError, 0, MultiDict([
				('66', int(YMSGStatus.NotAtHome))
			]))
			return
		
		if self.yahoo_id in PRE_SESSION_ID:
			self.send_reply(YMSGService.LogOff, YMSGStatus.Available, 0, None)
			self.close()
			return
		self.sess_id = secrets.randbelow(4294967294) + 1
		PRE_SESSION_ID[self.yahoo_id] = self.sess_id
		
		auth_dict = MultiDict([
			('1', self.yahoo_id),
		])
		
		if 9 <= self.dialect <= 10:
			self.challenge = generate_challenge_v1()
			auth_dict.add('94', self.challenge)
		elif self.dialect <= 11:
			# Implement V2 challenge string generation later
			auth_dict.add('94', '')
			auth_dict.add('13', 1)
		
		self.send_reply(YMSGService.Auth, YMSGStatus.BRB, self.sess_id, auth_dict)
	
	def _y_0054(self, *args) -> None:
		# SERVICE_AUTHRESP (0x54); verify response strings for successful authentication
		
		status = args[2]
		if status is YMSGStatus.WebLogin:
			status = YMSGStatus.Available
		
		resp_6 = args[4].get('6')
		resp_96 = args[4].get('96')
		
		version = args[4].get('135')
		version = version.split(', ')
		version = '.'.join(version)
		self.client = Client('yahoo', version, self.client.via)
		
		assert self.yahoo_id
		
		# TODO: Dialect 11 not supported yet?
		assert 9 <= self.dialect <= 10
		
		assert self.challenge is not None
		is_resp_correct = self._verify_challenge_v1(resp_6, resp_96)
		if is_resp_correct:
			uuid = yahoo_id_to_uuid(None, self.backend, self.yahoo_id)
			if uuid is None:
				is_resp_correct = False
			else:
				bs = self.backend.login(uuid, self.client, BackendEventHandler(self.backend.loop, self), front_needs_self_notify = True)
				if bs is None:
					is_resp_correct = False
				else:
					self.bs = bs
					self._util_authresp_final(status)
		
		if not is_resp_correct:
			self.send_reply(YMSGService.AuthResp, YMSGStatus.LoginError, self.sess_id, MultiDict([
				('66', int(YMSGStatus.Bad))
			]))
	
	def _util_authresp_final(self, status: YMSGStatus) -> None:
		bs = self.bs
		assert bs is not None
		
		user = bs.user
		detail = user.detail
		assert detail is not None
		
		self.t_cookie_token = AuthService.GenTokenStr()
		
		contacts = detail.contacts
		groups = detail.groups
		
		me_status_update(bs, status)
		
		self._update_buddy_list(contacts, groups, after_auth = True)
		
		bs.backend._sync_contact_statuses()
	
	# State = Live
	
	def _y_0004(self, *args) -> None:
		# SERVICE_ISBACK (0x04); notify contacts of online presence
		
		bs = self.bs
		assert bs is not None
		
		new_status = YMSGStatus(int(args[2]))
		
		me_status_update(bs, new_status)
	
	def _y_0003(self, *args) -> None:
		# SERVICE_ISAWAY (0x03); notify contacts of FYI idle presence
		
		bs = self.bs
		assert bs is not None
		
		new_status = YMSGStatus(int(args[4].get('10')))
		message = args[4].get('19') or ''
		is_away_message = (args[4].get('47') == '1')
		me_status_update(bs, new_status, message = message, is_away_message = is_away_message)
	
	def _y_0012(self, *args) -> None:
		# SERVICE_PINGCONFIGURATION (0x12); set the "ticks" and "tocks" of a ping sent
		
		self.send_reply(YMSGService.PingConfiguration, YMSGStatus.Available, self.sess_id, MultiDict([
			('143', 60),
			('144', 13)
		]))
	
	def _y_0016(self, *args) -> None:
		# SERVICE_PASSTHROUGH2 (0x16); collects OS version, processor, and time zone
		#
		# 1: YahooId
		# 25: unknown ('C=0[0x01]F=1,P=0,C=0,H=0,W=0,B=0,O=0,G=0[0x01]M=0,P=0,C=0,S=0,L=3,D=1,N=0,G=0,F=0,T=0')
		# 146: Base64-encoded string of host OS (e.g.: 'V2luZG93cyAyMDAwLCBTZXJ2aWNlIFBhY2sgNA==' = 'Windows 2000, Service Pack 4')
		# 145: Base64-encoded string of processor type (e.g.: 'SW50ZWwgUGVudGl1bSBQcm8gb3IgUGVudGl1bQ==' = 'Intel Pentium Pro or Pentium')
		# 147: Base64-encoded string of time zone (e.g.: 'RWFzdGVybiBTdGFuZGFyZCBUaW1l' = 'Eastern Standard Time')
		
		return
	
	def _y_0015(self, *args) -> None:
		# SERVICE_SKINNAME (0x15); used for IMVironments
		# Also happens when enabling/disabling Yahoo Helper.
		return
	
	def _y_0083(self, *args) -> None:
		# SERVICE_FRIENDADD (0x83); add a friend to your contact list
		
		contact_yahoo_id = args[4].get('7')
		message = args[4].get('14')
		buddy_group = args[4].get('65')
		utf8 = args[4].get('97')
		
		group = None
		
		add_request_response = MultiDict([
			('1', self.yahoo_id),
			('7', contact_yahoo_id),
			('65', buddy_group)
		])
		
		# Yahoo! Messenger has a function that lets you add people by email address (a.k.a. stripping the "@domain.tld" part of the address and
		# filling that out in the "Yahoo! ID" section of the contact add dialog). Treat as is.
		contact_uuid = yahoo_id_to_uuid(self.bs, self.backend, contact_yahoo_id)
		if contact_uuid is None:
			add_request_response.add('66', 3)
			self.send_reply(YMSGService.FriendAdd, YMSGStatus.BRB, self.sess_id, add_request_response)
			return
		
		bs = self.bs
		assert bs is not None
		user = bs.user
		detail = user.detail
		assert detail is not None
		
		contacts = detail.contacts
		groups = detail.groups
		
		contact = contacts.get(contact_uuid)
		if contact is not None and contact.lists & Lst.FL:
			for grp_uuid in contact.groups:
				if groups[grp_uuid].name == buddy_group:
					add_request_response.add('66', 2)
					self.send_reply(YMSGService.FriendAdd, YMSGStatus.BRB, self.sess_id, add_request_response)
					return
		
		for grp in groups.values():
			if grp.name == buddy_group:
				group = grp
				break
		
		if group is None:
			group = bs.me_group_add(buddy_group)
		
		ctc_head = self.backend._load_user_record(contact_uuid)
		assert ctc_head is not None
		
		if not ctc_head.status.is_offlineish():
			contact_struct = MultiDict([
				('0', self.yahoo_id),
			])
			add_contact_status_to_data(contact_struct, ctc_head.status, ctc_head)
		else:
			contact_struct = None
		
		self.send_reply(YMSGService.ContactNew, YMSGStatus.BRB, self.sess_id, contact_struct)
		
		if not contact or not contact.lists & Lst.FL:
			add_request_response.add('66', 0)
			self.send_reply(YMSGService.FriendAdd, YMSGStatus.BRB, self.sess_id, add_request_response)
			
			bs.me_contact_add(ctc_head.uuid, Lst.FL, message = (TextWithData(message, utf8) if message is not None else None))
		try:
			bs.me_group_contact_add(group.id, contact_uuid)
		except error.ContactAlreadyOnList:
			# Ignore, because this condition was checked earlier, so the only way this
			# can happen is if the the contact list gets in an inconsistent state.
			# (I.e. contact is not on FL, but still part of groups.)
			pass
		
		# Just in case Yahoo! doesn't send a LIST packet
		self._update_buddy_list(contacts, groups)
	
	def _y_0086(self, *args) -> None:
		# SERVICE_CONTACTDENY (0x86); deny a contact request
		
		adder_to_deny = args[4].get('7')
		deny_message = args[4].get('14')
		
		adder_uuid = self.backend.util_get_uuid_from_email(adder_to_deny)
		assert adder_uuid is not None
		bs = self.bs
		assert bs is not None
		bs.me_contact_deny(adder_uuid, deny_message)
	
	def _y_0089(self, *args) -> None:
		# SERVICE_GROUPRENAME (0x89); rename a contact group
		
		old_group_name = args[4].get('65')
		new_group_name = args[4].get('67')
		bs = self.bs
		assert bs is not None
		
		bs.me_group_edit(old_group_name, new_group_name)
		
		user = bs.user
		detail = user.detail
		assert detail is not None
		
		contacts = detail.contacts
		groups = detail.groups
		self._update_buddy_list(contacts, groups)
	
	def _y_0084(self, *args) -> None:
		# SERVICE_FRIENDREMOVE (0x84); remove a buddy from your list
		
		contact_id = args[4].get('7')
		buddy_group = args[4].get('65')
		
		remove_buddy_response = MultiDict([
			('1', self.yahoo_id),
			('7', contact_id),
			('65', buddy_group)
		])
		bs = self.bs
		assert bs is not None
		user = bs.user
		detail = user.detail
		assert detail is not None
		
		contacts = detail.contacts
		contact_uuid = yahoo_id_to_uuid(bs, self.backend, contact_id)
		if contact_uuid is None:
			remove_buddy_response.add('66', 3)
			self.send_reply(YMSGService.FriendRemove, YMSGStatus.BRB, self.sess_id, remove_buddy_response)
			return
		
		bs.me_contact_remove(contact_uuid, Lst.FL)
		
		groups = detail.groups
		self._update_buddy_list(contacts, groups)
	
	def _y_0085(self, *args) -> None:
		# SERVICE_IGNORE (0x85); add/remove someone from your ignore list
		
		ignored_yahoo_id = args[4].get('7')
		ignore_mode = args[4].get('13')
		
		ignore_reply_response = MultiDict([
			('0', self.yahoo_id),
			('7', ignored_yahoo_id),
			('13', ignore_mode)
		])
		
		bs = self.bs
		assert bs is not None
		user = bs.user
		detail = user.detail
		assert detail is not None
		contacts = detail.contacts
		
		ignored_uuid = yahoo_id_to_uuid(bs, self.backend, ignored_yahoo_id)
		if ignored_uuid is None:
			ignore_reply_response.add('66', 3)
			self.send_reply(YMSGService.Ignore, YMSGStatus.BRB, self.sess_id, ignore_reply_response)
			return
		
		if int(ignore_mode) == 1:
			contact = contacts[ignored_uuid]
			if contact is not None:
				if not contact.groups:
					ignore_reply_response.add('66', 2)
					self.send_reply(YMSGService.Ignore, YMSGStatus.BRB, self.sess_id, ignore_reply_response)
				else:
					ignore_reply_response.add('66', 12)
					self.send_reply(YMSGService.Ignore, YMSGStatus.BRB, self.sess_id, ignore_reply_response)
				return
			bs.me_contact_add(ignored_uuid, Lst.BL)
		elif int(ignore_mode) == 2:
			bs.me_contact_remove(ignored_uuid, Lst.BL)
		
		self.send_reply(YMSGService.AddIgnore, YMSGStatus.BRB, self.sess_id, None)
		ignore_reply_response.add('66', 0)
		self.send_reply(YMSGService.Ignore, YMSGStatus.BRB, self.sess_id, ignore_reply_response)
	
	def _y_000a(self, *args) -> None:
		# SERVICE_USERSTAT (0x0a); synchronize logged on user's status
		
		if self.yahoo_id != args[4].get('0'):
			return
		
		bs = self.bs
		assert bs is not None
		user = bs.user
		detail = user.detail
		assert detail is not None
		
		contacts = detail.contacts
		groups = detail.groups
		
		self.send_reply(YMSGService.UserStat, bs.front_data.get('ymsg_status') or YMSGStatus.Available, self.sess_id, None)
		self._update_buddy_list(contacts, groups)
	
	def _y_0055(self, *args) -> None:
		# SERVICE_LIST (0x55); send a user's buddy list
		
		bs = self.bs
		assert bs is not None
		user = bs.user
		detail = user.detail
		assert detail is not None
		
		contacts = detail.contacts
		groups = detail.groups
		
		self._update_buddy_list(contacts, groups)
	
	def _y_008a(self, *args) -> None:
		# SERVICE_PING (0x8a); send a response ping after the client pings
		
		self.send_reply(YMSGService.Ping, YMSGStatus.Available, self.sess_id, MultiDict([
			('1', self.yahoo_id),
		]))
	
	def _y_004f(self, *args) -> None:
		# SERVICE_PEERTOPEER (0x4f); possibly to either see if P2P file transfer or if P2P messaging was possible; dig into this later
		
		return
	
	def _y_004b(self, *args) -> None:
		# SERVICE_NOTIFY (0x4b); notify a contact of an action (typing, games, etc.)
		
		yahoo_data = args[4]
		notify_type = yahoo_data.get('49') # typing, games, etc.
		contact_yahoo_id = yahoo_data.get('5')
		contact_uuid = yahoo_id_to_uuid(self.bs, self.backend, contact_yahoo_id)
		if contact_uuid is None:
			return
		
		cs, _ = self._get_private_chat_with(contact_uuid)
		cs.send_message_to_everyone(messagedata_from_ymsg(cs.user, yahoo_data, notify_type = notify_type))
	
	# TODO: Implement offline messaging for both `SERVICE_MESSAGE` and `SERVICE_MASSMESSAGE`.
	# 
	# Obviously, source has to be Chet Simpson. Where else can we get this juicy data? :P
	# https://github.com/TheGibletInitiative/Giblet/blob/master/Protocols/YMsg/include/Protocols/YMsg/Server/Builders/OfflineMessage.h
	
	def _y_0006(self, *args) -> None:
		# SERVICE_MESSAGE (0x06); send a message to a user
		
		yahoo_data = args[4]
		contact_yahoo_id = yahoo_data.get('5')
		contact_uuid = yahoo_id_to_uuid(self.bs, self.backend, contact_yahoo_id)
		if contact_uuid is None:
			return
		
		cs, evt = self._get_private_chat_with(contact_uuid)
		evt._send_when_user_joins(contact_uuid, messagedata_from_ymsg(cs.user, yahoo_data))
	
	def _y_0017(self, *args) -> None:
		# SERVICE_MASSMESSAGE (0x17); send a message to multiple users
		
		yahoo_data = args[4]
		contact_yahoo_ids = yahoo_data.getall('5')
		if contact_yahoo_ids:
			for yahoo_id in contact_yahoo_ids:
				contact_uuid = yahoo_id_to_uuid(self.bs, self.backend, yahoo_id)
				if contact_uuid is None:
					continue
				
				cs, evt = self._get_private_chat_with(contact_uuid)
				evt._send_when_user_joins(contact_uuid, messagedata_from_ymsg(cs.user, yahoo_data))
	
	def _y_004d(self, *args) -> None:
		# SERVICE_P2PFILEXFER (0x4d); initiate P2P file transfer. Due to this service being present in 3rd-party libraries; we can implement it here
		
		bs = self.bs
		assert bs is not None
		
		yahoo_data = args[4]
		contact_uuid = yahoo_id_to_uuid(bs, self.backend, yahoo_data.get('5'))
		if contact_uuid is None:
			return
		
		for bs_other in bs.backend._sc.iter_sessions():
			if bs_other.user.uuid == contact_uuid:
				bs_other.evt.ymsg_on_xfer_init(bs.user, yahoo_data)
	
	def _y_0018(self, *args) -> None:
		# SERVICE_CONFINVITE (0x18); send a conference invite to one or more people
		
		bs = self.bs
		assert bs is not None
		
		yahoo_data = args[4]
		conf_roster = yahoo_data.getall('52', None)
		if conf_roster is None:
			return
		# Comma-separated yahoo ids
		conf_roster_2 = yahoo_data.get('51')
		if conf_roster_2:
			conf_roster.extend(conf_roster_2.split(','))
		conf_id = yahoo_data.get('57')
		invite_msg = yahoo_data.get('58')
		voice_chat = yahoo_data.get('13')
		
		chat = self._get_chat_by_id('ymsg/conf', conf_id, create = True)
		assert chat is not None
		cs = self._get_chat_session(chat, create = True)
		assert cs is not None
		
		for conf_user_yahoo_id in conf_roster:
			conf_user_uuid = yahoo_id_to_uuid(self.bs, self.backend, conf_user_yahoo_id)
			if conf_user_uuid is None:
				continue
			cs.invite(conf_user_uuid, invite_msg = invite_msg, roster = conf_roster, voice_chat = voice_chat)
	
	def _y_001c(self, *args) -> None:
		# SERVICE_CONFADDINVITE (0x1c); send a conference invite to an existing conference to one or more people
		
		yahoo_data = args[4]
		conf_new_roster_str = yahoo_data.get('51')
		if conf_new_roster_str is None:
			return
		conf_new_roster = conf_new_roster_str.split(',')
		conf_roster = yahoo_data.getall('52', None)
		if conf_roster is None:
			conf_roster = yahoo_data.getall('53', None)
			if conf_roster is None:
				conf_roster = []
		conf_id = yahoo_data.get('57')
		invite_msg = yahoo_data.get('58')
		voice_chat = yahoo_data.get('13')
		
		chat = self._get_chat_by_id('ymsg/conf', conf_id)
		assert chat is not None
		cs = self._get_chat_session(chat)
		assert cs is not None
		
		for conf_user_yahoo_id in conf_new_roster:
			conf_user_uuid = yahoo_id_to_uuid(self.bs, self.backend, conf_user_yahoo_id)
			if conf_user_uuid is None:
				continue
			cs.invite(conf_user_uuid, invite_msg = invite_msg, roster = conf_roster, voice_chat = voice_chat, existing = True)
	
	def _y_0019(self, *args) -> None:
		# SERVICE_CONFLOGON (0x19); request for me to join a conference
		
		bs = self.bs
		assert bs is not None
		
		#inviter_ids = args[4].getall('3', None)
		#if inviter_ids is None:
		#	return
		
		conf_id = args[4].get('57')
		chat = self._get_chat_by_id('ymsg/conf', conf_id)
		assert chat is not None
		cs = self._get_chat_session(chat, create = True)
		assert cs is not None
	
	def _y_001a(self, *args) -> None:
		# SERVICE_CONFDECLINE (0x1a); decline a request to join a conference
		
		bs = self.bs
		assert bs is not None
		
		inviter_ids = args[4].getall('3', None)
		if inviter_ids is None:
			return
		conf_id = args[4].get('57')
		deny_msg = args[4].get('14')
		
		chat = self._get_chat_by_id('ymsg/conf', conf_id)
		if chat is None:
			return
		
		for cs in chat.get_roster():
			if misc.yahoo_id(cs.user.email) not in inviter_ids:
				continue
			cs.evt.on_invite_declined(bs.user, message = deny_msg)
	
	def _y_001d(self, *args) -> None:
		# SERVICE_CONFMSG (0x1d); send a message in a conference
		
		#conf_user_ids = args[4].getall('53', None)
		#if conf_user_ids is None:
		#	return
		
		yahoo_data = args[4]
		conf_id = yahoo_data.get('57')
		
		chat = self._get_chat_by_id('ymsg/conf', conf_id)
		assert chat is not None
		cs = self._get_chat_session(chat)
		assert cs is not None
		cs.send_message_to_everyone(messagedata_from_ymsg(cs.user, yahoo_data))
	
	def _y_001b(self, *args) -> None:
		# SERVICE_CONFLOGOFF (0x1b); leave a conference
		
		#conf_roster = args[4].getall('3', None)
		#if conf_roster is None:
		#	return
		
		conf_id = args[4].get('57')
		chat = self._get_chat_by_id('ymsg/conf', conf_id)
		if chat is None:
			return
		cs = self._get_chat_session(chat)
		if cs is not None:
			cs.close()
	
	# Other functions
	
	def _get_private_chat_with(self, other_user_uuid: str) -> Tuple[ChatSession, 'ChatEventHandler']:
		assert self.bs is not None
		
		if other_user_uuid not in self.private_chats:
			chat = self.backend.chat_create(twoway_only = True)
			
			# `user` joins
			evt = ChatEventHandler(self.backend.loop, self)
			cs = chat.join('yahoo', self.bs, evt)
			self.private_chats[other_user_uuid] = (cs, evt)
			cs.invite(other_user_uuid)
		return self.private_chats[other_user_uuid]
	
	def _get_chat_by_id(self, scope: str, id: str, *, create: bool = False) -> Optional[Chat]:
		chat = self.backend.chat_get(scope, id)
		if chat is None and create:
			chat = self.backend.chat_create()
			chat.add_id(scope, id)
		return chat
	
	def _get_chat_session(self, chat: Chat, *, create: bool = False) -> Optional[ChatSession]:
		assert self.bs is not None
		cs = self.chat_sessions.get(chat)
		if cs is None and create:
			cs = chat.join('yahoo', self.bs, ChatEventHandler(self.backend.loop, self))
			self.chat_sessions[chat] = cs
			chat.send_participant_joined(cs)
		return cs
	
	def _update_buddy_list(self, contacts: Dict[str, Contact], groups: Dict[str, Group], after_auth: bool = False) -> None:
		cs = list(contacts.values())
		cs_fl = [c for c in cs if c.lists & Lst.FL]
		
		contact_group_list = []
		for grp in groups.values():
			contact_list = []
			for c in cs_fl:
				if grp.id in c.groups:
					contact_list.append(misc.yahoo_id(c.head.email))
			if contact_list:
				contact_group_list.append(grp.name + ':' + ','.join(contact_list) + '\n')
		# Handle contacts that aren't part of any groups
		contact_list = [misc.yahoo_id(c.head.email) for c in cs_fl if not c.groups]
		if contact_list:
			contact_group_list.append('(No Group):' + ','.join(contact_list) + '\n')
		
		ignore_list = []
		for c in cs:
			if c.lists & Lst.BL:
				ignore_list.append(misc.yahoo_id(c.head.email))
		
		(y_cookie, t_cookie, cookie_expiry) = self._refresh_cookies()
		
		self.send_reply(YMSGService.List, YMSGStatus.Available, self.sess_id, MultiDict([
			('87', ''.join(contact_group_list)),
			('88', ','.join(ignore_list)),
			('89', self.yahoo_id),
			('59', '{}={}; expires={}; path=/; domain=.yahoo.com'.format('Y', y_cookie.replace('=', '\t', 1), cookie_expiry)),
			('59', '{}={}; expires={}; path=/; domain=.yahoo.com'.format('T', t_cookie.replace('=', '\t', 1), cookie_expiry)),
			('59', 'C\tmg=1'),
			('3', self.yahoo_id),
			('90', '1'),
			('100', '0'),
			('101', ''),
			('102', ''),
			('93', '86400')
		]))
		
		logon_payload = MultiDict([
			('0', self.yahoo_id),
			('1', self.yahoo_id),
			('8', len(cs_fl))
		])
		
		for c in cs_fl:
			add_contact_status_to_data(logon_payload, c.status, c.head)
		
		if after_auth:
			if self.dialect >= 10:
				self.send_reply(YMSGService.LogOn, YMSGStatus.Available, self.sess_id, logon_payload)
				self.send_reply(YMSGService.PingConfiguration, YMSGStatus.Available, self.sess_id, MultiDict([
					('143', 60),
					('144', 13)
				]))
		else:
			self.send_reply(YMSGService.LogOn, YMSGStatus.Available, self.sess_id, logon_payload)
	
	def _verify_challenge_v1(self, resp_6: str, resp_96: str) -> bool:
		from hashlib import md5
		
		chal = self.challenge
		if chal is None:
			return False
		
		yahoo_id = self.yahoo_id
		if yahoo_id is None:
			return False
		
		uuid = yahoo_id_to_uuid(self.bs, self.backend, yahoo_id)
		if uuid is None:
			return False
		
		# Retrieve Yahoo64-encoded MD5 hash of the user's password from the database
		# NOTE: The MD5 hash of the password is literally unsalted. Good grief, Yahoo!
		pass_md5 = Y64.Y64Encode(self.backend.user_service.yahoo_get_md5_password(uuid) or b'')
		# Retrieve MD5-crypt(3)'d hash of the user's password from the database
		pass_md5crypt = Y64.Y64Encode(md5(self.backend.user_service.yahoo_get_md5crypt_password(uuid) or b'').digest())
		
		seed_val = (ord(chal[15]) % 8) % 5
		
		if seed_val == 0:
			checksum = chal[ord(chal[7]) % 16]
			hash_p = checksum + pass_md5 + yahoo_id + chal
			hash_c = checksum + pass_md5crypt + yahoo_id + chal
		elif seed_val == 1:
			checksum = chal[ord(chal[9]) % 16]
			hash_p = checksum + yahoo_id + chal + pass_md5
			hash_c = checksum + yahoo_id + chal + pass_md5crypt
		elif seed_val == 2:
			checksum = chal[ord(chal[15]) % 16]
			hash_p = checksum + chal + pass_md5 + yahoo_id
			hash_c = checksum + chal + pass_md5crypt + yahoo_id
		elif seed_val == 3:
			checksum = chal[ord(chal[1]) % 16]
			hash_p = checksum + yahoo_id + pass_md5 + chal
			hash_c = checksum + yahoo_id + pass_md5crypt + chal
		elif seed_val == 4:
			checksum = chal[ord(chal[3]) % 16]
			hash_p = checksum + pass_md5 + chal + yahoo_id
			hash_c = checksum + pass_md5crypt + chal + yahoo_id
		
		resp_6_server = Y64.Y64Encode(md5(hash_p.encode()).digest())
		resp_96_server = Y64.Y64Encode(md5(hash_c.encode()).digest())
		
		return resp_6 == resp_6_server and resp_96 == resp_96_server
	
	def _refresh_cookies(self) -> Tuple[str, str, str]:
		# Creates the cookies if they don't exist, or bumps their expiry if they do.
		
		assert self.t_cookie_token is not None
		
		auth_service = self.backend.auth_service
		
		timestamp = int(time.time())
		expiry = datetime.datetime.utcfromtimestamp(timestamp + 86400).strftime('%a, %d %b %Y %H:%M:%S GMT')
		
		y_cookie = Y_COOKIE_TEMPLATE.format(encodedname = _encode_yahoo_id(self.yahoo_id))
		t_cookie = T_COOKIE_TEMPLATE.format(token = self.t_cookie_token)
		
		auth_service.pop_token('ymsg/y_cookie', y_cookie)
		auth_service.pop_token('ymsg/t_cookie', t_cookie)
		
		auth_service.create_token('ymsg/cookie', self.yahoo_id, token = y_cookie, lifetime = 86400)
		auth_service.create_token('ymsg/cookie', self.bs, token = t_cookie, lifetime = 86400)
		
		return (y_cookie, t_cookie, expiry)

Y_COOKIE_TEMPLATE = 'v=1&n=&l={encodedname}&p=&r=&lg=&intl=&np='
T_COOKIE_TEMPLATE = 'z={token}&a=&sk={token}&ks={token}&kt=&ku=&d={token}'

def _encode_yahoo_id(yahoo_id: str) -> str:
	return ''.join(
		YAHOO_ID_ENCODING.get(c) or c
		for c in yahoo_id
	)

YAHOO_ID_ENCODING = {
	'k': 'a',
	'l': 'b',
	'm': 'c',
	'n': 'd',
	'o': 'e',
	'p': 'f',
	'q': 'g',
	'r': 'h',
	's': 'i',
	't': 'j',
	'u': 'k',
	'v': 'l',
	'w': 'm',
	'x': 'n',
	'y': 'o',
	'z': 'p',
	'0': 'q',
	'1': 'r',
	'2': 's',
	'3': 't',
	'4': 'u',
	'5': 'v',
	'7': 'x',
	'8': 'y',
	'9': 'z',
	'6': 'w',
	'a': '0',
	'b': '1',
	'c': '2',
	'd': '3',
	'e': '4',
	'f': '5',
	'g': '6',
	'h': '7',
	'i': '8',
	'j': '9',
}

def add_contact_status_to_data(data: Any, status: UserStatus, contact: User) -> None:
	is_offlineish = status.is_offlineish()
	contact_yahoo_id = misc.yahoo_id(contact.email)
	key_11_val = contact.uuid[:8].upper()
	
	data.add('7', contact_yahoo_id)
	
	if is_offlineish or not status.message:
		data.add('10', int(YMSGStatus.Available if is_offlineish else YMSGStatus.FromSubstatus(status.substatus)))
		data.add('11', key_11_val)
	else:
		data.add('10', int(YMSGStatus.Custom))
		data.add('11', key_11_val)
		data.add('19', status.message)
		is_away_message = (status.substatus is not Substatus.Online)
		data.add('47', int(is_away_message))
	
	data.add('17', 0)
	data.add('13', (0 if is_offlineish else 1))

class BackendEventHandler(event.BackendEventHandler):
	__slots__ = ('loop', 'ctrl', 'dialect', 'sess_id', 'bs')
	
	loop: asyncio.AbstractEventLoop
	ctrl: YMSGCtrlPager
	dialect: int
	sess_id: int
	bs: BackendSession
	
	def __init__(self, loop: asyncio.AbstractEventLoop, ctrl: YMSGCtrlPager) -> None:
		self.loop = loop
		self.ctrl = ctrl
		self.dialect = ctrl.dialect
		self.sess_id = ctrl.sess_id
	
	def on_presence_notification(self, contact: Contact, old_substatus: Substatus) -> None:
		if contact.status.is_offlineish():
			service = YMSGService.LogOff
		elif old_substatus.is_offlineish():
			service = YMSGService.LogOn
		elif contact.status.substatus is Substatus.Online:
			service = YMSGService.IsBack
		else:
			service = YMSGService.IsAway
		
		yahoo_data = MultiDict()
		if service is not YMSGService.LogOff:
			yahoo_data.add('0', self.ctrl.yahoo_id)
		
		add_contact_status_to_data(yahoo_data, contact.status, contact.head)
		
		self.ctrl.send_reply(service, YMSGStatus.BRB, self.sess_id, yahoo_data)
	
	def on_contact_request_denied(self, user: User, message: str) -> None:
		for y in misc.build_contact_deny_notif(user, self.bs, message):
			self.ctrl.send_reply(y[0], y[1], self.ctrl.sess_id, y[2])
	
	def on_xfer_init(self, sender: User, yahoo_data: Dict[str, Any]) -> None:
		for y in misc.build_ft_packet(sender, self.bs, yahoo_data):
			self.ctrl.send_reply(y[0], y[1], self.ctrl.sess_id, y[2])
	
	def on_upload_file_ft(self, recipient: str, message: str):
		for y in misc.build_http_ft_ack_packet(self.bs, recipient, message):
			self.ctrl.send_reply(y[0], y[1], self.ctrl.sess_id, y[2])
	
	def on_sent_ft_http(self, sender: str, url_path: str, message: str):
		for y in misc.build_http_ft_packet(self.bs, sender, url_path, message):
			self.ctrl.send_reply(y[0], y[1], self.ctrl.sess_id, y[2])
	
	def on_chat_invite(self, chat: 'Chat', inviter: User, *, invite_msg: str = '', roster: Optional[List[str]] = None, voice_chat: Optional[int] = None, existing: bool = False) -> None:
		if chat.twoway_only:
			# A Yahoo! non-conference chat; auto-accepted invite
			evt = ChatEventHandler(self.loop, self.ctrl)
			cs = chat.join('yahoo', self.bs, evt)
			chat.send_participant_joined(cs)
			self.ctrl.private_chats[inviter.uuid] = (cs, evt)
		else:
			# Regular chat
			if 'ymsg/conf' not in chat.ids:
				chat.add_id('ymsg/conf', chat.ids['main'])
			for y in misc.build_conf_invite(inviter, self.bs, chat.ids['ymsg/conf'], invite_msg, roster or [], voice_chat or 0, existing_conf = existing):
				self.ctrl.send_reply(y[0], y[1], self.ctrl.sess_id, y[2])
	
	def on_added_me(self, user: User, *, message: Optional[TextWithData] = None) -> None:
		for y in misc.build_contact_request_notif(user, self.bs.user, ('' if message is None else message.text), (None if message is None else message.yahoo_utf8)):
			self.ctrl.send_reply(y[0], y[1], self.ctrl.sess_id, y[2])
	
	def on_pop_boot(self) -> None:
		pass
	
	def on_pop_notify(self) -> None:
		pass
	
	def on_close(self) -> None:
		self.ctrl.close()

class ChatEventHandler(event.ChatEventHandler):
	__slots__ = ('loop', 'ctrl', 'bs', 'cs')
	
	loop: asyncio.AbstractEventLoop
	ctrl: YMSGCtrlPager
	bs: BackendSession
	cs: ChatSession
	
	def __init__(self, loop: asyncio.AbstractEventLoop, ctrl: YMSGCtrlPager) -> None:
		self.loop = loop
		self.ctrl = ctrl
		assert ctrl.bs is not None
		self.bs = ctrl.bs
	
	def on_close(self) -> None:
		self.ctrl.chat_sessions.pop(self.cs.chat, None)
	
	def on_participant_joined(self, cs_other: ChatSession) -> None:
		if self.cs.chat.twoway_only:
			return
		for y in misc.build_conf_logon(self.bs, cs_other):
			self.ctrl.send_reply(y[0], y[1], self.ctrl.sess_id, y[2])
	
	def on_participant_left(self, cs_other: ChatSession) -> None:
		if 'ymsg/conf' not in cs_other.chat.ids:
			# Yahoo only receives this event in "conferences"
			return
		for y in misc.build_conf_logoff(self.bs, cs_other):
			self.ctrl.send_reply(y[0], y[1], self.ctrl.sess_id, y[2])
	
	def on_invite_declined(self, invited_user: User, *, message: str = '') -> None:
		for y in misc.build_conf_invite_decline(invited_user, self.bs, self.cs.chat.ids['ymsg/conf'], message):
			self.ctrl.send_reply(y[0], y[1], self.ctrl.sess_id, y[2])
	
	def on_message(self, data: MessageData) -> None:
		sender = data.sender
		yahoo_data = messagedata_to_ymsg(data)
		
		if data.type is MessageType.Chat:
			if self.cs.chat.twoway_only:
				for y in misc.build_message_packet(sender, self.bs, yahoo_data):
					self.ctrl.send_reply(y[0], y[1], self.ctrl.sess_id, y[2])
			else:
				for y in misc.build_conf_message_packet(sender, self.cs, yahoo_data):
					self.ctrl.send_reply(y[0], y[1], self.ctrl.sess_id, y[2])
		elif data.type is MessageType.Typing:
			for y in misc.build_notify_notif(sender, self.bs, yahoo_data):
				self.ctrl.send_reply(y[0], y[1], self.ctrl.sess_id, y[2])
	
	def _send_when_user_joins(self, user_uuid: str, data: MessageData) -> None:
		# Send to everyone currently in chat
		self.cs.send_message_to_everyone(data)
		
		if self._user_in_chat(user_uuid):
			return
		
		# If `user_uuid` hasn't joined yet, send it later
		self.loop.create_task(self._send_delayed(user_uuid, data))
	
	async def _send_delayed(self, user_uuid: str, data: MessageData) -> None:
		delay = 0.1
		for _ in range(3):
			await asyncio.sleep(delay)
			delay *= 3
			if self._user_in_chat(user_uuid):
				self.cs.send_message_to_user(user_uuid, data)
				return
	
	def _user_in_chat(self, user_uuid: str) -> bool:
		for cs_other in self.cs.chat.get_roster():
			if cs_other.user.uuid == user_uuid:
				return True
		return False

def messagedata_from_ymsg(sender: User, data: Dict[str, Any], *, notify_type: Optional[str] = None) -> MessageData:
	text = data.get('14') or ''
	
	if notify_type is None:
		type = MessageType.Chat
	elif notify_type == 'TYPING':
		type = MessageType.Typing
	else:
		# TODO: other `notify_type`s
		raise Exception("Unknown notify_type", notify_type)
	
	message = MessageData(sender = sender, type = type, text = text)
	message.front_cache['ymsg'] = data
	return message

def messagedata_to_ymsg(data: MessageData) -> Dict[str, Any]:
	if 'ymsg' not in data.front_cache:
		data.front_cache['ymsg'] = MultiDict([
			('14', data.text),
			('63', ';0'),
			('64', 0),
			('97', 1),
		])
	return data.front_cache['ymsg']

def me_status_update(bs: BackendSession, status_new: YMSGStatus, *, message: str = '', is_away_message: bool = False) -> None:
	bs.front_data['ymsg_status'] = status_new
	if status_new is YMSGStatus.Custom:
		substatus = (Substatus.Busy if is_away_message else Substatus.Online)
	else:
		substatus = YMSGStatus.ToSubstatus(status_new)
	bs.me_update({
		'message': message,
		'substatus': substatus,
	})

def generate_challenge_v1() -> str:
	from uuid import uuid4
	
	# Yahoo64-encode the raw 16 bytes of a UUID
	return Y64.Y64Encode(uuid4().bytes)
