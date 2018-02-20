from typing import Optional, Dict, Any, List
import secrets
import datetime
from multidict import MultiDict
import asyncio
import time
import binascii

from util.misc import Logger

from core import event
from core.backend import Backend, YahooBackendSession, ConferenceSession, Conference
from core.models import Substatus, Lst, UserYahoo, YahooContact, YMSGService, YMSGStatus
from core.client import Client
from core.yahoo.YSC import YahooSessionClearing
from .misc import *

from .ymsg_ctrl import YMSGCtrlBase

class YMSGCtrlPager(YMSGCtrlBase):
	__slots__ = ('backend', 'dialect', 'usr_name', 'uuid', 'status', 'sess_id', 'challenge', 'ybs', 'sc', 'cs', 'pending_confs', 'client')
	
	backend: Backend
	dialect: int
	usr_name: Optional[str]
	uuid: Optional[str]
	status: int
	sess_id: int
	challenge: Optional[str]
	ybs: Optional[YahooBackendSession]
	sc: Optional[YahooSessionClearing]
	cs: Optional[ConferenceSession]
	pending_confs: Dict[UserYahoo, Conference]
	client: Client
	
	def __init__(self, logger: Logger, via: str, backend: Backend) -> None:
		super().__init__(logger)
		self.backend = backend
		self.dialect = 0
		self.usr_name = None
		self.uuid = None
		self.status = 0
		self.challenge = None
		self.ybs = None
		self.sc = None
		self.cs = None
		self.pending_confs = {}
		self.client = Client('yahoo', '?', via)
	
	def _on_close(self) -> None:
		if len(self.pending_confs) > 0:
			self.pending_confs = {}
		
		if self.sc:
			self.sc.pop_session(self.usr_name)
		
		if self.cs:
			self.cs.close(None)
		
		if self.ybs:
			self.ybs.close()
	
	# State = Auth
	
	def _y_004c(self, *args) -> None:
		# SERVICE_HANDSHAKE (0x4c); acknowledgement of the server
		
		self.client = Client('yahoo', 'YMSG' + str(args[0]), self.client.via)
		self.dialect = int(args[0])
		self.send_reply(YMSGService.Handshake, YMSGStatus.BRB, 0, None)
		return
	
	def _y_0057(self, *args) -> None:
		# SERVICE_AUTH (0x57); send a challenge string for the client to craft two response strings with
		
		self.usr_name = args[4].get('1')
		self.sc = YahooSessionClearing(self.usr_name)
		if self.sc.dup:
			self.send_reply(YMSGService.LogOff, YMSGStatus.Available, 0, None)
			self.close(duplicate = True)
			return
		self.sess_id = self.sc.retreive_session_id(self.usr_name)
		
		auth_dict = MultiDict(
			[
				('1', self.usr_name)
			]
		)
		
		if self.dialect in (9, 10):
			self.challenge = self.backend.generate_challenge_v1()
			auth_dict.add('94', self.challenge)
		elif self.dialect in (11,):
			# Implement V2 challenge string generation later
			auth_dict.add('94', '')
			auth_dict.add('13', 1)
		
		self.send_reply(YMSGService.Auth, YMSGStatus.BRB, self.sess_id, auth_dict)
	
	def _y_0054(self, *args) -> None:
		# SERVICE_AUTHRESP (0x54); verify response strings for successful authentication
		
		if args[2] == YMSGStatus.WebLogin:
			self.status = YMSGStatus.Available
		else:
			self.status = args[2]
		
		if not self.backend.verify_yahoo_user(self.usr_name):
			self.send_reply(YMSGService.AuthResp, YMSGStatus.LoginError, self.sess_id, MultiDict(
				[
					('66', YMSGStatus.NotAtHome)
				]
			))
			self.close()
			return
		if args[4].get('1') != self.usr_name and args[4].get('2') != '1':
			self.logger.info('auth resp. failed')
			self.send_reply(YMSGService.AuthResp, YMSGStatus.LoginError, self.sess_id, MultiDict(
				[
					('66', YMSGStatus.Bad)
				]
			))
			self.close()
			return
		resp_6 = args[4].get('6')
		resp_96 = args[4].get('96')
		
		version = args[4].get('135')
		version = version.split(', ')
		version = '.'.join(version)
		self.client = Client('yahoo', version, self.client.via)
		
		if self.dialect in (9, 10):
			is_resp_correct = self.backend.verify_challenge_v1(self.usr_name, self.challenge, resp_6, resp_96)
			if is_resp_correct:
				self.logger.info('auth success')
				backend = self.backend
				uuid = backend.util_get_yahoo_uuid_from_email(self.usr_name)
				self.ybs = backend.login_yahoo(uuid, self.client, YahooBackendEventHandler(self))
				
				self._util_authresp_final(self.status)
			else:
				self.send_reply(YMSGService.AuthResp, YMSGStatus.LoginError, self.sess_id, MultiDict(
					[
						('66', YMSGStatus.Bad)
					]
				))
				self.close()
				return
	
	def _util_authresp_final(self, status):
		ybs = self.ybs
		backend = self.backend
		
		assert ybs is not None
		
		user_yahoo = ybs.user_yahoo
		detail = user_yahoo.detail
		assert detail is not None
		
		contacts = detail.contacts
		groups = detail.groups
		
		ybs.me_status_update(status, send_presence = False)
		
		self._update_buddy_list(contacts, groups, after_auth = True)
		
		ybs.send_login_presence()
	
	# State = Live
	
	def _y_0004(self, *args) -> None:
		# SERVICE_ISBACK (0x04); notify contacts of online presence
		
		ybs = self.ybs
		new_status = int(args[2])
		
		self.status = new_status
		ybs.me_status_update(self.status, message = None)
	
	def _y_0003(self, *args) -> None:
		# SERVICE_ISAWAY (0x03); notify contacts of FYI idle presence
		
		ybs = self.ybs
		
		new_status = int(args[4].get('10'))
		status_custom = None
		message_dict = None
		
		if new_status == YMSGStatus.Custom:
			message = args[4].get('19')
			is_away_message = int(args[4].get('47'))
			message_dict = {'text': message, 'is_away_message': is_away_message}
		
		self.status = new_status
		ybs.me_status_update(self.status, message = message_dict)
	
	def _y_0012(self, *args) -> None:
		# SERVICE_PINGCONFIGURATION (0x12); set the "ticks" and "tocks" of a ping sent
		
		self.send_reply(YMSGService.PingConfiguration, YMSGStatus.Available, self.sess_id, MultiDict(
			[
				('143', 60),
				('144', 13)
			]
		))
	
	def _y_0016(self, *args) -> None:
		# SERVICE_PASSTHROUGH2 (0x16); collects OS version, processor, and time zone
		#
		# 1: YahooId
		# 25: unknown ('C=0[0x01]F=1,P=0,C=0,H=0,W=0,B=0,O=0,G=0[0x01]M=0,P=0,C=0,S=0,L=3,D=1,N=0,G=0,F=0,T=0')
		# 146: Base64-encoded host OS (e.g.: 'V2luZG93cyAyMDAwLCBTZXJ2aWNlIFBhY2sgNA==' = 'Windows 2000, Service Pack 4')
		# 145: Base64-encoded processor type (e.g.: 'SW50ZWwgUGVudGl1bSBQcm8gb3IgUGVudGl1bQ==' = 'Intel Pentium Pro or Pentium')
		# 147: Base64-encoded time zone (e.g.: 'RWFzdGVybiBTdGFuZGFyZCBUaW1l' = 'Eastern Standard Time')
		
		return
	
	def _y_0015(self, *args) -> None:
		# SERVICE_SKINNAME (0x15); used for IMVironments
		
		return
	
	def _y_0083(self, *args) -> None:
		# SERVICE_FRIENDADD (0x83); add a friend to your contact list
		
		contact_to_add = args[4].get('7')
		message = args[4].get('14')
		buddy_group = args[4].get('65')
		
		group = None
		
		add_request_response = MultiDict(
			[
				('1', self.usr_name),
				('7', contact_to_add),
				('65', buddy_group)
			]
		)
		user_contact_uuid = self.backend.util_get_yahoo_uuid_from_email(contact_to_add)
		if user_contact_uuid is None:
			add_request_response.add('66', 3)
			self.send_reply(YMSGService.FriendAdd, YMSGStatus.BRB, self.sess_id, add_request_response)
			return
		
		ybs = self.ybs
		user_yahoo = ybs.user_yahoo
		detail = user_yahoo.detail
		assert detail is not None
		
		contacts = detail.contacts
		groups = detail.groups
		
		contact_yahoo = contacts.get(user_contact_uuid)
		
		if contact_yahoo is not None:
			if contact_yahoo.status.name == contact_to_add:
				for grp in contact_yahoo.groups:
					if groups[grp].name == buddy_group:
						add_request_response.add('66', 2)
						self.send_reply(YMSGService.FriendAdd, YMSGStatus.BRB, self.sess_id, add_request_response)
						return
		
		for grp in groups.values():
			if grp.name == buddy_group:
				group = grp
				break
		
		if group is None: group = ybs.me_group_add(buddy_group)
		
		ctc_head = self.backend._load_yahoo_user_record(user_contact_uuid)
		
		if ctc_head.status.substatus != YMSGStatus.Offline:
			contact_struct = MultiDict(
				[
					('0', self.usr_name),
					('7', contact_to_add),
					('10', ctc_head.status.substatus)
				]
			)
			
			if ctc_head.status.message not in ({'text': '', 'is_away_message': 0},):
				contact_struct.add('19', ctc_head.status.message['text'])
				contact_struct.add('47', ctc_head.status.message['is_away_message'])
			
			contact_struct.add('17', 0)
			contact_struct.add('13', 1)
			
			self.send_reply(YMSGService.ContactNew, YMSGStatus.BRB, self.sess_id, contact_struct)
		else:
			self.send_reply(YMSGService.ContactNew, YMSGStatus.BRB, self.sess_id, None)
		
		if not contact_yahoo:
			add_request_response.add('66', 0)
			self.send_reply(YMSGService.FriendAdd, YMSGStatus.BRB, self.sess_id, add_request_response)
			
			ybs.me_contact_add(ctc_head, contact_to_add, group, message)
		else:
			ybs.me_group_contact_move(group.id, user_contact_uuid)
		
		ybs.me_check_empty_groups()
		# Just in case Yahoo! doesn't send a LIST packet
		self._update_buddy_list(contacts, groups)
	
	def _y_0086(self, *args) -> None:
		# SERVICE_CONTACTDENY (0x86); deny a contact request
		
		adder_to_deny = args[4].get('7')
		deny_message = args[4].get('14')
		
		adder_uuid = self.backend.util_get_yahoo_uuid_from_email(adder_to_deny)
		ybs = self.ybs
		ybs.me_contact_deny(adder_uuid, deny_message)
	
	def _y_0089(self, *args) -> None:
		# SERVICE_GROUPRENAME (0x89); rename a contact group
		
		old_group_name = args[4].get('65')
		new_group_name = args[4].get('67')
		ybs = self.ybs
		
		ybs.me_group_edit(old_group_name, new_group_name)
		
		user_yahoo = ybs.user_yahoo
		detail = user_yahoo.detail
		
		contacts = detail.contacts
		groups = detail.groups
		self._update_buddy_list(contacts, groups)
	
	def _y_0084(self, *args):
		# SERVICE_FRIENDREMOVE (0x84); remove a buddy from your list
		
		buddy_to_remove = args[4].get('7')
		buddy_group = args[4].get('65')
		
		remove_buddy_response = MultiDict(
			[
				('1', self.usr_name),
				('7', buddy_to_remove),
				('65', buddy_group)
			]
		)
		ybs = self.ybs
		user_yahoo = ybs.user_yahoo
		detail = user_yahoo.detail
		assert detail is not None
		
		contacts = detail.contacts
		user_contact_uuid = self.backend.util_get_yahoo_uuid_from_email(buddy_to_remove)
		if user_contact_uuid is None:
			remove_buddy_response.add('66', 3)
			self.send_reply(YMSGService.FriendAdd, YMSGStatus.BRB, self.sess_id, remove_buddy_response)
			return
		
		ybs.me_contact_remove(user_contact_uuid)
		ybs.me_check_empty_groups()
		
		groups = detail.groups
		self._update_buddy_list(contacts, groups)
	
	def _y_0085(self, *args):
		# SERVICE_IGNORE (0x85); add/remove someone from your ignore list
		
		user_to_ignore = args[4].get('7')
		ignore_mode = args[4].get('13')
		
		ignore_reply_response = MultiDict(
			[
				('0', self.usr_name),
				('7', user_to_ignore),
				('13', ignore_mode)
			]
		)
		
		ybs = self.ybs
		user_yahoo = ybs.user_yahoo
		detail = user_yahoo.detail
		assert detail is not None
		contacts = detail.contacts
		
		if int(ignore_mode) == 1:
			cs = [c for c in contacts.values()]
			if cs:
				for c in cs:
					if c.status.name == user_to_ignore:
						if len(c.groups) == 0:
							ignore_reply_response.add('66', 2)
							self.send_reply(YMSGService.Ignore, YMSGStatus.BRB, self.sess_id, ignore_reply_response)
							return
						else:
							ignore_reply_response.add('66', 12)
							self.send_reply(YMSGService.Ignore, YMSGStatus.BRB, self.sess_id, ignore_reply_response)
							return
			
			user_uuid = self.backend.util_get_yahoo_uuid_from_email(user_to_ignore)
			if user_uuid is None: return
			ybs.me_contact_add_ignore(user_uuid, user_to_ignore)
		elif int(ignore_mode) == 2:
			user_uuid = self.backend.util_get_yahoo_uuid_from_email(user_to_ignore)
			ybs.me_contact_remove(user_uuid)
		
		self.send_reply(YMSGService.AddIgnore, YMSGStatus.BRB, self.sess_id, None)
		ignore_reply_response.add('66', 0)
		self.send_reply(YMSGService.Ignore, YMSGStatus.BRB, self.sess_id, ignore_reply_response)
	
	def _y_000a(self, *args) -> None:
		# SERVICE_USERSTAT (0x0a); synchronize logged on user's status
		
		if self.usr_name == args[4].get('0'):
			ybs = self.ybs
			user_yahoo = ybs.user_yahoo
			detail = user_yahoo.detail
			
			contacts = detail.contacts
			groups = detail.groups
			
			self.send_reply(YMSGService.UserStat, self.status, self.sess_id, None)
			self._update_buddy_list(contacts, groups)
	
	def _y_0055(self, *args) -> None:
		# SERVICE_LIST (0x55); send a user's buddy list
		
		ybs = self.ybs
		user_yahoo = ybs.user_yahoo
		detail = user_yahoo.detail
		
		contacts = detail.contacts
		groups = detail.groups
		
		self._update_buddy_list(contacts, groups)
	
	def _y_008a(self, *args) -> None:
		# SERVICE_PING (0x8a); send a response ping after the client pings
		
		self.send_reply(YMSGService.Ping, YMSGStatus.Available, self.sess_id, None)
	
	# State = Messaging
	
	def _y_004f(self, *args):
		# SERVICE_PEERTOPEER (0x4f); possibly to either see if P2P file transfer or if P2P messaging was possible; dig into this later
		
		return
	
	def _y_004b(self, *args) -> None:
		# SERVICE_NOTIFY (0x4b); notify a contact of an action (typing, games, etc.)
		
		ybs = self.ybs
		notify_type = args[4].get('49')
		person_to_chat = args[4].get('5')
		
		invitee_uuid = self.backend.util_get_yahoo_uuid_from_email(person_to_chat)
		if invitee_uuid is None:
			return
			
		ybs.me_send_notify_pkt(invitee_uuid, args[4])
	
	def _y_0006(self, *args):
		# SERVICE_MESSAGE (0x06); send a message to a user
		
		ybs = self.ybs
		person_to_msg = args[4].get('5')
		
		invitee_uuid = self.backend.util_get_yahoo_uuid_from_email(person_to_msg)
		if invitee_uuid is None:
			return
		
		ybs.me_send_im(invitee_uuid, args[4])
	
	def _y_004d(self, *args):
		# SERVICE_P2PFILEXFER (0x4d); initiate P2P file transfer. Due to this service being present in 3rd-party libraries; we can implement it here
		
		ybs = self.ybs
		ft_target = args[4].get('5')
		
		target_uuid = self.backend.util_get_yahoo_uuid_from_email(ft_target)
		if target_uuid is None:
			return
		
		ybs.me_send_filexfer(target_uuid, args[4])
	
	def _y_0018(self, *args):
		# SERVICE_CONFINVITE (0x18); send a conference invite to one or more people
		
		ybs = self.ybs
		conf_roster = args[4].getall('52', None)
		if conf_roster is None:
			return
		conf_id = args[4].get('57')
		invite_msg = args[4].get('58')
		voice_chat = args[4].get('13')
		
		conf = self.backend.conference_create(conf_id)
		
		cs = conf.join(ybs, ConferenceEventHandler(self))
		self.cs = cs
		
		for conf_user in conf_roster:
			conf_user_uuid = self.backend.util_get_yahoo_uuid_from_email(conf_user)
			if conf_user_uuid is None:
				return
			cs.invite(conf_user_uuid, invite_msg, conf_roster, voice_chat)
	
	def _y_001c(self, *args):
		# SERVICE_CONFADDINVITE (0x1c); send a conference invite to an existing conference to one or more people
		
		conf_new_roster = args[4].getall('51', None)
		if conf_new_roster is None:
			return
		conf_roster = args[4].getall('52', None)
		if conf_roster is None:
			conf_roster = args[4].getall('53', None)
			if conf_roster is None:
				conf_roster = []
		conf_id = args[4].get('57')
		invite_msg = args[4].get('58')
		voice_chat = args[4].get('13')
		
		cs = self.cs
		assert cs is not None
		
		for conf_new_user in conf_new_roster:
			conf_user_uuid = self.backend.util_get_yahoo_uuid_from_email(conf_new_user)
			if conf_user_uuid is None:
				return
			cs.invite(conf_user_uuid, invite_msg, conf_roster, voice_chat, existing = True)
	
	def _y_0019(self, *args):
		# SERVICE_CONFLOGON (0x19); request someone to join a conference
		
		ybs = self.ybs
		inviter_ids = args[4].getall('3', None)
		if inviter_ids is None:
			return
		conf_id = args[4].get('57')
		
		for inviter_id in inviter_ids:
			inviter_uuid = self.backend.util_get_yahoo_uuid_from_email(inviter_id)
			if inviter_uuid is None:
				return
			inviter = self.backend._load_yahoo_user_record(inviter_uuid)
			conf = self.pending_confs.get(inviter)
			if conf is None:
				continue
			if conf.id != conf_id:
				continue
			else:
				break
		if conf is None: return
		del self.pending_confs[inviter]
		cs = conf.join(ybs, ConferenceEventHandler(self))
		self.cs = cs
		
		conf.send_participant_joined(cs)
	
	def _y_001a(self, *args):
		# SERVICE_CONFDECLINE (0x1a); decline a request to join a conference
		
		ybs = self.ybs
		inviter_ids = args[4].getall('3', None)
		if inviter_ids is None:
			return
		conf_id = args[4].get('57')
		deny_msg = args[4].get('14')
		
		for inviter_id in inviter_ids:
			inviter_uuid = self.backend.util_get_yahoo_uuid_from_email(inviter_id)
			if inviter_uuid is None:
				return
			inviter = self.backend._load_yahoo_user_record(inviter_uuid)
			conf = self.pending_confs.get(inviter)
			if conf is None:
				return
			del self.pending_confs[inviter]
			if conf.id != conf_id:
				return
			
			ybs.me_decline_conf_invite(inviter, conf.id, deny_msg)
	
	def _y_001d(self, *args):
		# SERVICE_CONFMSG (0x1d); send a message in a conference
		
		conf_user_ids = args[4].getall('53', None)
		if conf_user_ids is None:
			return
		conf_id = args[4].get('57')
		
		cs = self.cs
		assert cs is not None
		
		cs.send_message_to_everyone(conf_id, args[4])
	
	def _y_001b(self, *args):
		# SERVICE_CONFLOGOFF (0x1b); leave a conference
		
		conf_roster = args[4].getall('3', None)
		if conf_roster is None:
			return
		conf_id = args[4].get('57')
		
		cs = self.cs
		assert cs is not None
		
		conf = cs.conf
		if conf.id != conf_id:
			return
		
		cs.close(conf_roster)
	
	# Other functions
	
	def _update_buddy_list(self, contacts, groups, after_auth = False):
		contact_pkt_format = ''
		ignore_pkt_format = ''
		
		for grp in groups.values():
			cat = grp.name + ":"
			cat_id = grp.id
			
			cs = [c for c in contacts.values()]
			if cs:
				contact_list = []
				for c in cs:
					for grp_id in c.groups:
						if grp_id == cat_id:
							contact_list.append(c.status.name)
							break
				
				if len(contact_list) == 0: continue
				contact_pkt_format += cat + ','.join(contact_list)
			
			contact_pkt_format += '\n'
		
		cs = [c for c in contacts.values()]
		if cs:
			ignore_list = []
			for c in cs:
				if len(c.groups) == 0: ignore_list.append(c.status.name)
			ignore_pkt_format = ','.join(ignore_list)
		
		expiry = datetime.datetime.utcnow() + datetime.timedelta(days=1)
		expiry = expiry.strftime('%a, %d %b %Y %H:%M:%S GMT')
		
		self.send_reply(YMSGService.List, YMSGStatus.Available, self.sess_id, MultiDict(
			[
				('87', contact_pkt_format),
				('88', ignore_pkt_format),
				('89', self.usr_name),
				('59', 'Y\tv=1&n=&l=&p=&r=&lg=&intl=&np=; expires=' + expiry + '; path=/; domain=.yahoo.com'),
				('59', 'T\tz=&a=&sk=&ks=&kt=&ku=&d=; expires=' + expiry + '; path=/; domain=.yahoo.com'),
				('59', 'C\tmg=1'),
				('3', self.usr_name),
				('90', '1'),
				('100', '0'),
				('101', ''),
				('102', ''),
				('93', '86400')
			]
		))
		
		# TODO: Current contact detail implementation results in the Yahoo! Messenger clients appending "()" to offline contacts' names
		
		logon_payload = MultiDict(
			[
				('0', self.usr_name),
				('1', self.usr_name),
				# ('8', (len(cs) if len(cs) != 1 else ''))
				('8', len(cs))
			]
		)
		
		if cs:
			for c in cs:
				logon_payload.add('7', c.status.name)
				logon_payload.add('10', (YMSGStatus.Offline if c.status.substatus == YMSGStatus.Invisible else c.status.substatus))
				logon_payload.add('11', c.head.uuid[:8].upper())
				if c.status.substatus == YMSGStatus.Custom and c.status.message not in ({'text': '', 'is_away_message': 0},):
					logon_payload.add('19', c.status.message['text'])
					logon_payload.add('47', c.status.message['is_away_message'])
				logon_payload.add('17', 0)
				logon_payload.add('13', (0 if c.status.substatus == YMSGStatus.Offline else 1))
				if c.status.substatus == YMSGStatus.Offline:
					logon_payload.add('60', '2')
		
		# logon_payload.add('16', 'This is a dummy error message. We will replace this soon...')
		
		if after_auth:
			if self.dialect >= 10:
				self.send_reply_multiple([YMSGService.LogOn, YMSGStatus.Available, self.sess_id, logon_payload], [YMSGService.PingConfiguration, YMSGStatus.Available, self.sess_id, MultiDict(
					[
						('143', 60),
						('144', 13)
					]
				)])
		else:
			self.send_reply(YMSGService.LogOn, YMSGStatus.Available, self.sess_id, logon_payload)

class YahooBackendEventHandler(event.YahooBackendEventHandler):
	__slots__ = ('ctrl',)
	
	ctrl: YMSGCtrlPager
	
	def __init__(self, ctrl: YMSGCtrlPager) -> None:
		self.ctrl = ctrl
	
	def on_presence_notification(self, contact: YahooContact) -> None:
		for y in build_yahoo_presence_notif(contact, self.ctrl.dialect, self.ctrl.backend, self.ctrl.ybs):
			self.ctrl.send_reply(y[0], y[1], self.ctrl.sess_id, y[2])
	
	def on_login_presence_notification(self, contact: YahooContact) -> None:
		for y in build_yahoo_login_presence_notif(contact, self.ctrl.dialect, self.ctrl.backend, self.ctrl.ybs):
			self.ctrl.send_reply(y[0], y[1], self.ctrl.sess_id, y[2])
	
	def on_logout_notification(self, contact: YahooContact) -> None:
		for y in build_yahoo_logout_notif(contact, self.ctrl.dialect, self.ctrl.backend, self.ctrl.ybs):
			self.ctrl.send_reply(y[0], y[1], self.ctrl.sess_id, y[2])
	
	def on_invisible_absence_notification(self, contact: YahooContact) -> None:
		for y in build_yahoo_invisible_absence_notif(contact, self.ctrl.dialect, self.ctrl.backend, self.ctrl.ybs):
			self.ctrl.send_reply(y[0], y[1], self.ctrl.sess_id, y[2])
	
	def on_invisible_presence_notification(self, contact: YahooContact) -> None:
		for y in build_yahoo_presence_invisible_notif(contact, self.ctrl.dialect, self.ctrl.backend, self.ctrl.ybs):
			self.ctrl.send_reply(y[0], y[1], self.ctrl.sess_id, y[2])
	
	def on_absence_notification(self, contact: YahooContact) -> None:
		for y in build_yahoo_absence_notif(contact, self.ctrl.dialect, self.ctrl.backend, self.ctrl.ybs):
			self.ctrl.send_reply(y[0], y[1], self.ctrl.sess_id, y[2])
	
	def on_conf_invite(self, conf: Conference, inviter: UserYahoo, invite_msg: Optional[str], conf_roster: List[str], voice_chat: int, existing_conf: bool = False) -> None:
		self.ctrl.pending_confs[inviter] = conf
		
		for y in build_yahoo_conf_invite(inviter, self.ctrl.ybs, conf.id, invite_msg, conf_roster, voice_chat, existing_conf = existing_conf):
			self.ctrl.send_reply(y[0], y[1], self.ctrl.sess_id, y[2])
	
	def on_conf_invite_decline(self, inviter: UserYahoo, conf_id: str, deny_msg: Optional[str]):
		for y in build_yahoo_conf_invite_decline(inviter, self.ctrl.ybs, conf_id, deny_msg):
			self.ctrl.send_reply(y[0], y[1], self.ctrl.sess_id, y[2])
	
	def on_init_contact_request(self, user_adder: UserYahoo, user_added: UserYahoo, message: Optional[str]) -> None:
		for y in build_yahoo_contact_request_notif(user_adder, user_added, message):
			self.ctrl.send_reply(y[0], y[1], self.ctrl.sess_id, y[2])
	
	def on_deny_contact_request(self, user_denier: UserYahoo, deny_message: Optional[str]) -> None:
		for y in build_yahoo_contact_deny_notif(user_denier, deny_message):
			self.ctrl.send_reply(y[0], y[1], self.ctrl.sess_id, y[2])
	
	def on_notify_notification(self, sender: UserYahoo, notif_dict: Dict[str, Any]) -> None:
		for y in build_yahoo_notify_notif(sender, self.ctrl.ybs, notif_dict):
			self.ctrl.send_reply(y[0], y[1], self.ctrl.sess_id, y[2])
	
	def on_im_message(self, sender: UserYahoo, message_dict: Dict[str, Any]) -> None:
		for y in build_yahoo_message_packet(sender, self.ctrl.ybs, message_dict):
			self.ctrl.send_reply(y[0], y[1], self.ctrl.sess_id, y[2])
	
	def on_xfer_init(self, sender: UserYahoo, xfer_dict: Dict[str, Any]) -> None:
		for y in build_yahoo_ft_packet(sender, self.ctrl.ybs, xfer_dict):
			self.ctrl.send_reply(y[0], y[1], self.ctrl.sess_id, y[2])
	
	def on_close(self) -> None:
		self.ctrl.close()

class ConferenceEventHandler(event.ConferenceEventHandler):
	__slots__ = ('ctrl',)
	
	ctrl: YMSGCtrlPager
	
	def __init__(self, ctrl: YMSGCtrlPager) -> None:
		self.ctrl = ctrl
	
	def on_participant_joined(self, cs_other: ConferenceSession) -> None:
		for y in build_yahoo_conf_logon(self.ctrl.ybs, cs_other):
			self.ctrl.send_reply(y[0], y[1], self.ctrl.sess_id, y[2])
	
	def on_participant_left(self, cs_other: ConferenceSession) -> None:
		for y in build_yahoo_conf_logoff(self.ctrl.ybs, cs_other):
			self.ctrl.send_reply(y[0], y[1], self.ctrl.sess_id, y[2])
	
	def on_message(self, sender: UserYahoo, message_dict: Dict[str, Any]) -> None:
		for y in build_yahoo_conf_message_packet(sender, self.ctrl.cs, message_dict):
			self.ctrl.send_reply(y[0], y[1], self.ctrl.sess_id, y[2])
