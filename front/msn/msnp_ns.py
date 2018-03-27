from typing import Tuple, Any, Optional, List
from datetime import datetime
from lxml.objectify import fromstring as parse_xml

from util.misc import Logger

from core import event
from core.backend import Backend, BackendSession, Chat
from core.models import Substatus, Lst, User, Contact, TextWithData
from core.client import Client

from .msnp import MSNPCtrl
from .misc import build_msnp_presence_notif, encode_msnobj, gen_mail_data, Err

MSNP_DIALECTS = ['MSNP{}'.format(d) for d in (
	# Actually supported
	18, 17, 16, 15, 14, 13, 12, 11,
	10, 9, 8, 7, 6, 5, 4, 3, 2,
	# Not actually supported
	19, 20, 21,
)]

class MSNPCtrlNS(MSNPCtrl):
	__slots__ = ('backend', 'dialect', 'usr_email', 'bs', 'client', 'syn_ser', 'iln_sent')
	
	backend: Backend
	dialect: int
	usr_email: Optional[str]
	bs: Optional[BackendSession]
	client: Client
	syn_ser: int
	iln_sent: bool
	
	def __init__(self, logger: Logger, via: str, backend: Backend) -> None:
		super().__init__(logger)
		self.backend = backend
		self.dialect = 0
		self.usr_email = None
		self.bs = None
		self.client = Client('msn', '?', via)
		self.syn_ser = 0
		self.iln_sent = False
	
	def _on_close(self) -> None:
		if self.bs:
			self.bs.close()
	
	# State = Auth
	
	def _m_ver(self, trid: str, *args) -> None:
		dialects = [a.upper() for a in args]
		try:
			t = int(trid)
		except ValueError:
			self.close(hard = True)
		d = None
		for d in MSNP_DIALECTS:
			if d in dialects: break
		if d not in dialects:
			self.send_reply('VER', trid, 0)
			self.close(hard = True)
		self.client = Client('msn', d, self.client.via)
		self.dialect = int(d[4:])
		self.send_reply('VER', trid, d)
	
	def _m_cvr(self, trid: str, *args) -> None:
		v = args[5]
		self.client = Client('msn', v, self.client.via)
		self.send_reply('CVR', trid, v, v, v, 'https://escargot.log1p.xyz', 'https://escargot.log1p.xyz')
	
	def _m_inf(self, trid: str) -> None:
		dialect = self.dialect
		if dialect < 8:
			self.send_reply('INF', trid, 'MD5')
		else:
			self.send_reply(Err.CommandDisabled, trid)
	
	def _m_usr(self, trid: str, authtype: str, stage: str, *args) -> None:
		dialect = self.dialect
		backend = self.backend
		
		if authtype == 'SHA':
			if dialect < 18:
				self.send_reply(Err.CommandDisabled, trid)
			# Used in MSNP18 (at least, for now) to validate Circle tickets
			# found in ABFindAll or ABFindContactsPaged response
			self.send_reply('USR', trid, 'OK', self.usr_email, 0, 0)
			return
		
		if authtype == 'MD5':
			if dialect >= 8:
				self.send_reply(Err.CommandDisabled, trid)
				return
			if stage == 'I':
				email = args[0]
				salt = backend.user_service.msn_get_md5_salt(email)
				if salt is None:
					# Account is not enabled for login via MD5
					# TODO: Can we pass an informative message to user?
					self.send_reply(Err.AuthFail, trid)
					return
				self.usr_email = email
				self.send_reply('USR', trid, authtype, 'S', salt)
				return
			if stage == 'S':
				md5_hash = args[0]
				usr_email = self.usr_email
				assert usr_email is not None
				uuid = backend.user_service.msn_login_md5(usr_email, md5_hash)
				if uuid is not None:
					self.bs = backend.login(uuid, self.client, BackendEventHandler(self))
				self._util_usr_final(trid, None)
				return
		
		if authtype in ('TWN', 'SSO'):
			if stage == 'I':
				#>>> USR trid TWN/SSO I email@example.com
				self.usr_email = args[0]
				if authtype == 'TWN':
					#extra = ('ct={},rver=5.5.4177.0,wp=FS_40SEC_0_COMPACT,lc=1033,id=507,ru=http:%2F%2Fmessenger.msn.com,tw=0,kpp=1,kv=4,ver=2.1.6000.1,rn=1lgjBfIL,tpf=b0735e3a873dfb5e75054465196398e0'.format(int(time())),)
					# This seems to work too:
					extra = ('ct=1,rver=1,wp=FS_40SEC_0_COMPACT,lc=1,id=1',) # type: Tuple[Any, ...]
				else:
					# https://web.archive.org/web/20100819015007/http://msnpiki.msnfanatic.com/index.php/MSNP15:SSO
					# TODO: Implement challenge string generation function (isn't mandatory, but will notch up security).
					extra = ('MBI_KEY_OLD', '8CLhG/xfgYZ7TyRQ/jIAWyDmd/w4R4GF2yKLS6tYrnjzi4cFag/Nr+hxsfg5zlCf')
				self.send_reply('USR', trid, authtype, 'S', *extra)
				return
			if stage == 'S':
				#>>> USR trid TWN S auth_token
				#>>> USR trid SSO S auth_token b64_response
				token = args[0]
				if token[0:2] == 't=':
					token = token[2:22]
				usr_email = self.usr_email
				assert usr_email is not None
				uuid = backend.auth_service.pop_token('nb/login', token)
				if uuid is not None:
					self.bs = backend.login(uuid, self.client, BackendEventHandler(self))
				self._util_usr_final(trid, token)
				return
		
		self.send_reply(Err.AuthFail, trid)
	
	def _util_usr_final(self, trid: str, token: Optional[str]) -> None:
		bs = self.bs
		
		if bs is None:
			self.send_reply(Err.AuthFail, trid)
			return
		
		if token:
			self.backend.util_set_sess_token(bs, token)
		
		dialect = self.dialect
		
		if dialect < 18:
			bs.me_pop_boot_others()
		else:
			bs.me_pop_notify_others()
		
		user = bs.user
		
		if dialect < 10:
			args = (user.status.name,) # type: Tuple[Any, ...]
		else:
			args = ()
		if dialect >= 8:
			#verified = user.verified
			verified = True
			args += ((1 if verified else 0), 0)
		
		self.send_reply('USR', trid, 'OK', user.email, *args)
		
		if dialect < 13:
			return
		
		(high, low) = _uuid_to_high_low(user.uuid)
		(ip, port) = self.peername
		now = datetime.utcnow()
		
		if dialect == 21:
			self.send_reply('CHL', 0, '1663122458434562624782678054')
			msg0 = _encode_payload(PAYLOAD_MSG_0,
				email_address = user.email,
				endpoint_ID = '{00000000-0000-0000-0000-000000000000}',
				timestamp = now.isoformat()[:19] + 'Z',
			)
			self.send_reply('NFY', 'PUT', msg0)
		else:
			self.send_reply('SBS', 0, 'null')
			if 18 <= dialect < 21:
				# MSNP21 doesn't use this; unsure if 19/20 use it
				self.send_reply('UBX', '1:' + user.email, b'')
			self.send_reply('PRP', 'MFN', user.status.name)
		
		msg1 = _encode_payload(PAYLOAD_MSG_1,
			time = int(now.timestamp()), high = high, low = low,
			token = token, ip = ip, port = port,
		)
		self.send_reply('MSG', 'Hotmail', 'Hotmail', msg1)
		
		msg2 = _encode_payload(PAYLOAD_MSG_2,
			md = gen_mail_data(user, self.backend),
		)
		self.send_reply('MSG', 'Hotmail', 'Hotmail', msg2)
	
	# State = Live
	
	def _m_syn(self, trid: str, *extra) -> None:
		bs = self.bs
		dialect = self.dialect
		
		assert bs is not None
		
		user = bs.user
		detail = user.detail
		assert detail is not None
		
		contacts = detail.contacts
		groups = detail.groups
		settings = detail.settings
		
		if dialect < 10:
			self.syn_ser = int(extra[0])
			ser = self._ser()
			if dialect < 6:
				self.send_reply('SYN', trid, ser)
				for lst in (Lst.FL, Lst.AL, Lst.BL, Lst.RL):
					cs = [c for c in contacts.values() if c.lists & lst]
					if cs:
						for i, c in enumerate(cs):
							self.send_reply('LST', trid, lst.name, ser, len(cs), i + 1, c.head.email, c.status.name)
					else:
						self.send_reply('LST', trid, lst.name, ser, 0, 0)
				self.send_reply('GTC', trid, ser, settings.get('GTC', 'A'))
				self.send_reply('BLP', trid, ser, settings.get('BLP', 'AL'))
			elif dialect < 8:
				self.send_reply('SYN', trid, ser)
				num_groups = len(groups) + 1
				self.send_reply('LSG', trid, ser, 1, num_groups, '0', "Other Contacts", 0)
				for i, g in enumerate(groups.values()):
					self.send_reply('LSG', trid, ser, i + 2, num_groups, g.id, g.name, 0)
				for lst in (Lst.FL, Lst.AL, Lst.BL, Lst.RL):
					cs = [c for c in contacts.values() if c.lists & lst]
					if cs:
						for i, c in enumerate(cs):
							gs = ((','.join(c.groups) or '0') if lst == Lst.FL else None)
							self.send_reply('LST', trid, lst.name, ser, i + 1, len(cs), c.head.email, c.status.name, gs)
					else:
						self.send_reply('LST', trid, lst.name, ser, 0, 0)
				self.send_reply('GTC', trid, ser, settings.get('GTC', 'A'))
				self.send_reply('BLP', trid, ser, settings.get('BLP', 'AL'))
			else:
				num_groups = len(groups) + 1
				self.send_reply('SYN', trid, ser, len(contacts), num_groups)
				self.send_reply('GTC', settings.get('GTC', 'A'))
				self.send_reply('BLP', settings.get('BLP', 'AL'))
				self.send_reply('LSG', '0', "Other Contacts", 0)
				for g in groups.values():
					self.send_reply('LSG', g.id, g.name, 0)
				for c in contacts.values():
					self.send_reply('LST', c.head.email, c.status.name, c.lists, ','.join(c.groups) or '0')
		else:
			self.send_reply('SYN', trid, TIMESTAMP, TIMESTAMP, len(contacts), len(groups))
			self.send_reply('GTC', settings.get('GTC', 'A'))
			self.send_reply('BLP', settings.get('BLP', 'AL'))
			self.send_reply('PRP', 'MFN', user.status.name)
			
			for g in groups.values():
				self.send_reply('LSG', g.name, g.id)
			for c in contacts.values():
				self.send_reply('LST', 'N={}'.format(c.head.email), 'F={}'.format(c.status.name), 'C={}'.format(c.head.uuid),
					c.lists, (None if dialect < 12 else 1), ','.join(c.groups)
				)
	
	def _m_gcf(self, trid: str, filename: str) -> None:
		self.send_reply('GCF', trid, filename, SHIELDS)
	
	def _m_png(self) -> None:
		self.send_reply('QNG', (60 if self.dialect >= 9 else None))
	
	def _m_uux(self, trid: str, data: bytes) -> None:
		bs = self.bs
		assert bs is not None
		
		elm = parse_xml(data.decode('utf-8'))
		
		psm = elm.find('PSM')
		cm = elm.find('CurrentMedia')
		if psm or cm:
			bs.me_update({
				'message': str(elm.find('PSM')),
				'media': str(elm.find('CurrentMedia')),
			})
		
		mg = elm.find('MachineGuid')
		if mg:
			bs.front_data['msn_pop_id'] = str(mg)[1:-1]
		
		self.send_reply('UUX', trid, 0)
	
	def _m_url(self, trid: str, *ignored) -> None:
		self.send_reply('URL', trid, '/unused1', '/unused2', 1)
	
	def _m_adg(self, trid: str, name: str, ignored = None) -> None:
		#>>> ADG 276 New Group
		bs = self.bs
		assert bs is not None
		try:
			group = bs.me_group_add(name)
		except Exception as ex:
			self.send_reply(Err.GetCodeForException(ex), trid)
			return
		self.send_reply('ADG', trid, self._ser(), name, group.id, 0)
	
	def _m_rmg(self, trid: str, group_id: str) -> None:
		#>>> RMG 250 00000000-0000-0000-0001-000000000001
		bs = self.bs
		assert bs is not None
		
		if group_id == 'New%20Group':
			# Bug: MSN 7.0 sends name instead of id in a particular scenario
			detail = bs.user.detail
			assert detail is not None
			
			for g in detail.groups.values():
				if g.name != 'New Group': continue
				group_id = g.id
				break
		
		try:
			bs.me_group_remove(group_id)
		except Exception as ex:
			self.send_reply(Err.GetCodeForException(ex), trid)
			return
		
		self.send_reply('RMG', trid, self._ser() or 1, group_id)
	
	def _m_reg(self, trid: str, group_id: str, name: str, ignored = None) -> None:
		#>>> REG 275 00000000-0000-0000-0001-000000000001 newname
		bs = self.bs
		assert bs is not None
		
		try:
			bs.me_group_edit(group_id, name)
		except Exception as ex:
			self.send_reply(Err.GetCodeForException(ex), trid)
			return
		if self.dialect < 10:
			self.send_reply('REG', trid, self._ser(), group_id, name, 0)
		else:
			self.send_reply('REG', trid, 1, name, group_id, 0)
	
	def _m_adc(self, trid: str, lst_name: str, arg1: str, arg2: Optional[str] = None) -> None:
		if arg1.startswith('N='):
			#>>> ADC 249 BL N=bob1@hotmail.com
			#>>> ADC 278 AL N=foo@hotmail.com
			#>>> ADC 277 FL N=foo@hotmail.com F=foo@hotmail.com
			contact_uuid = self.backend.util_get_uuid_from_email(arg1[2:])
			group_id = None
			name = (arg2[2:] if arg2 else None)
		else:
			# Add C= to group
			#>>> ADC 246 FL C=00000000-0000-0000-0002-000000000002 00000000-0000-0000-0001-000000000003
			contact_uuid = arg1[2:]
			group_id = arg2
			name = None
		
		self._add_common(trid, lst_name, contact_uuid, name, group_id)
	
	def _m_add(self, trid: str, lst_name: str, email: str, name: Optional[str] = None, group_id: Optional[str] = None) -> None:
		#>>> ADD 122 FL email name group
		contact_uuid = self.backend.util_get_uuid_from_email(email)
		self._add_common(trid, lst_name, contact_uuid, name, group_id)
	
	def _add_common(self, trid: str, lst_name: str, contact_uuid: Optional[str], name: Optional[str] = None, group_id: Optional[str] = None) -> None:
		bs = self.bs
		assert bs is not None
		
		if contact_uuid is None:
			self.send_reply(Err.InvalidUser, trid)
			return
		
		lst = getattr(Lst, lst_name)
		
		try:
			ctc, ctc_head = bs.me_contact_add(contact_uuid, lst, name = name)
			if group_id:
				bs.me_group_contact_add(group_id, contact_uuid)
		except Exception as ex:
			self.send_reply(Err.GetCodeForException(ex), trid)
			return
		
		if self.dialect >= 10:
			if lst == Lst.FL:
				if group_id:
					self.send_reply('ADC', trid, lst_name, 'C={}'.format(ctc_head.uuid), group_id)
				else:
					self.send_reply('ADC', trid, lst_name, 'N={}'.format(ctc_head.email), 'C={}'.format(ctc_head.uuid))
			else:
				self.send_reply('ADC', trid, lst_name, 'N={}'.format(ctc_head.email))
		else:
			self.send_reply('ADD', trid, lst_name, self._ser(), ctc_head.email, name, group_id)
	
	def _m_rem(self, trid: str, lst_name: str, usr: str, group_id: Optional[str] = None) -> None:
		bs = self.bs
		assert bs is not None
		
		lst = getattr(Lst, lst_name)
		if lst is Lst.RL:
			bs.close()
			return
		if lst is Lst.FL:
			#>>> REM 279 FL 00000000-0000-0000-0002-000000000001
			#>>> REM 247 FL 00000000-0000-0000-0002-000000000002 00000000-0000-0000-0001-000000000002
			if self.dialect < 10:
				contact_uuid = self.backend.util_get_uuid_from_email(usr)
			else:
				contact_uuid = usr
		else:
			#>>> REM 248 AL bob1@hotmail.com
			contact_uuid = self.backend.util_get_uuid_from_email(usr)
		if contact_uuid is None:
			self.send_reply(Err.InvalidPrincipal, trid)
			return
		try:
			if group_id:
				bs.me_group_contact_remove(group_id, contact_uuid)
			else:
				bs.me_contact_remove(contact_uuid, lst)
		except Exception as ex:
			self.send_reply(Err.GetCodeForException(ex), trid)
			return
		self.send_reply('REM', trid, lst_name, self._ser(), usr, group_id)
	
	def _m_gtc(self, trid: str, value: str) -> None:
		if self.dialect >= 13:
			self.send_reply(Err.CommandDisabled, trid)
			return
		# "Alert me when other people add me ..." Y/N
		#>>> GTC 152 N
		bs = self.bs
		assert bs is not None
		
		bs.me_update({ 'gtc': value })
		self.send_reply('GTC', trid, self._ser(), value)
	
	def _m_blp(self, trid: str, value: str) -> None:
		# Check "Only people on my Allow List ..." AL/BL
		#>>> BLP 143 BL
		bs = self.bs
		assert bs is not None
		bs.me_update({ 'blp': value })
		self.send_reply('BLP', trid, self._ser(), value)
	
	def _m_chg(self, trid: str, sts_name: str, capabilities: Optional[int] = None, msnobj: Optional[str] = None) -> None:
		#>>> CHG 120 BSY 1073791020 <msnobj .../>
		bs = self.bs
		assert bs is not None
		
		capabilities = capabilities or 0
		bs.me_update({
			'substatus': getattr(Substatus, sts_name),
		})
		bs.front_data['msn_capabilities'] = capabilities
		bs.front_data['msn_msnobj'] = msnobj
		self.send_reply('CHG', trid, sts_name, capabilities, encode_msnobj(msnobj))
		
		# Send ILNs
		if self.iln_sent:
			return
		self.iln_sent = True
		user = bs.user
		detail = user.detail
		assert detail is not None
		dialect = self.dialect
		for ctc in detail.contacts.values():
			for m in build_msnp_presence_notif(trid, ctc, dialect, self.backend):
				self.send_reply(*m)
	
	def _m_rea(self, trid: str, email: str, name: str) -> None:
		if self.dialect >= 10:
			self.send_reply(Err.CommandDisabled, trid)
			return
		
		bs = self.bs
		assert bs is not None
		
		if email == bs.user.email:
			bs.me_update({ 'name': name })
		self.send_reply('REA', trid, self._ser(), email, name)
	
	def _m_snd(self, trid: str, email: str) -> None:
		# Send email about how to use MSN. Ignore it for now.
		self.send_reply('SND', trid, email)
	
	def _m_prp(self, trid: str, key: str, value: str, *rest) -> None:
		#>>> PRP 115 MFN ~~woot~~
		bs = self.bs
		assert bs is not None
		
		if key == 'MFN':
			bs.me_update({ 'name': value })
		# TODO: Save other settings?
		self.send_reply('PRP', trid, key, value)
	
	def _m_sbp(self, trid: str, uuid: str, key: str, value: str) -> None:
		#>>> SBP 153 00000000-0000-0000-0002-000000000002 MFN Bob%201%20New
		# Can be ignored: core handles syncing contact names
		self.send_reply('SBP', trid, uuid, key, value)
	
	def _m_xfr(self, trid: str, dest: str) -> None:
		bs = self.bs
		assert bs is not None
		
		if dest != 'SB':
			self.send_reply(Err.InvalidParameter, trid)
			return
		
		dialect = self.dialect
		token = self.backend.auth_service.create_token('sb/xfr', (bs, dialect))
		extra = () # type: Tuple[Any, ...]
		if dialect >= 13:
			extra = ('U', 'messenger.msn.com')
		if dialect >= 14:
			extra += (1,)
		self.send_reply('XFR', trid, dest, 'm1.escargot.log1p.xyz:1864', 'CKI', token, *extra)
	
	# These four commands appear to be useless:
	def _m_adl(self, trid: str, data: bytes) -> None:
		self.send_reply('ADL', trid, 'OK')
	def _m_rml(self, trid: str, data: bytes) -> None:
		self.send_reply('RML', trid, 'OK')
	def _m_fqy(self, trid: str, data: bytes) -> None:
		self.send_reply('FQY', trid, b'')
	def _m_uun(self, trid: str, email: str, arg0: str, data: bytes) -> None:
		self.send_reply('UUN', trid, 'OK')
	
	def _ser(self) -> Optional[int]:
		if self.dialect >= 10:
			return None
		self.syn_ser += 1
		return self.syn_ser

class BackendEventHandler(event.BackendEventHandler):
	__slots__ = ('ctrl',)
	
	ctrl: MSNPCtrlNS
	
	def __init__(self, ctrl: MSNPCtrlNS) -> None:
		self.ctrl = ctrl
	
	def on_presence_notification(self, contact: Contact, old_substatus: Substatus) -> None:
		for m in build_msnp_presence_notif(None, contact, self.ctrl.dialect, self.ctrl.backend):
			self.ctrl.send_reply(*m)
	
	def on_chat_invite(self, chat: Chat, inviter: User, *, invite_msg: Optional[str] = None, roster: Optional[List[str]] = None, voice_chat: Optional[int] = None, existing: bool = False) -> None:
		extra = () # type: Tuple[Any, ...]
		dialect = self.ctrl.dialect
		if dialect >= 13:
			extra = ('U', 'messenger.hotmail.com')
		if dialect >= 14:
			extra += (1,)
		token = self.ctrl.backend.auth_service.create_token('sb/cal', (self.ctrl.bs, dialect, chat))
		self.ctrl.send_reply('RNG', chat.ids['main'], 'm1.escargot.log1p.xyz:1864', 'CKI', token, inviter.email, inviter.status.name, *extra)
	
	def on_added_to_list(self, user: User, *, message: Optional[TextWithData] = None) -> None:
		email = user.email
		name = (user.status.name or email)
		dialect = self.ctrl.dialect
		if dialect < 10:
			m = ('ADD', 0, Lst.RL.name, email, name)
		else:
			m = ('ADC', 0, Lst.RL.name, 'N={}'.format(email), 'F={}'.format(name))
		self.ctrl.send_reply(*m)
	
	def on_contact_request_denied(self, user: User, message: Optional[str]) -> None:
		pass
	
	def on_oim_sent(self, oim_uuid: str) -> None:
		assert self.ctrl.bs is not None
		self.ctrl.send_reply('MSG', 'Hotmail', 'Hotmail', _encode_payload(PAYLOAD_MSG_3,
			md = gen_mail_data(self.ctrl.bs.user, self.ctrl.backend, oim_uuid = oim_uuid, just_sent = True, e_node = False, q_node = False)
		))
	
	def on_oim_deletion(self) -> None:
		self.ctrl.send_reply('MSG', 'Hotmail', 'Hotmail', _encode_payload(PAYLOAD_MSG_4))
	
	def on_pop_boot(self) -> None:
		self.ctrl.send_reply('OUT', 'OTH')
	
	def on_pop_notify(self) -> None:
		# TODO: What do?
		pass
	
	def on_close(self) -> None:
		self.ctrl.close()

def _encode_payload(tmpl: str, **kwargs: Any) -> bytes:
	return tmpl.format(**kwargs).replace('\n', '\r\n').encode('utf-8')

PAYLOAD_MSG_0 = '''Routing: 1.0
To: 1:{email_address};epid={endpoint_ID}
From: 1:{email_address}

Reliability: 1.0

Notification: 1.0
NotifNum: 0
Uri: /user
NotifType: Partial
Content-Type: application/user+xml
Content-Length: 53

<user><s n="PF" ts="{timestamp}"></s></user>'''

PAYLOAD_MSG_1 = '''MIME-Version: 1.0
Content-Type: text/x-msmsgsprofile; charset=UTF-8
LoginTime: {time}
EmailEnabled: 1
MemberIdHigh: {high}
MemberIdLow: {low}
lang_preference: 1033
preferredEmail:
country:
PostalCode:
Gender:
Kid: 0
Age:
BDayPre:
Birthday:
Wallet:
Flags: 536872513
MSPAuth: {token}Y6+H31sTUOFkqjNTDYqAAFLr5Ote7BMrMnUIzpg860jh084QMgs5djRQLLQP0TVOFkKdWDwAJdEWcfsI9YL8otN9kSfhTaPHR1njHmG0H98O2NE/Ck6zrog3UJFmYlCnHidZk1g3AzUNVXmjZoyMSyVvoHLjQSzoGRpgHg3hHdi7zrFhcYKWD8XeNYdoz9wfA2YAAAgZIgF9kFvsy2AC0Fl/ezc/fSo6YgB9TwmXyoK0wm0F9nz5EfhHQLu2xxgsvMOiXUSFSpN1cZaNzEk/KGVa3Z33Mcu0qJqvXoLyv2VjQyI0VLH6YlW5E+GMwWcQurXB9hT/DnddM5Ggzk3nX8uMSV4kV+AgF1EWpiCdLViRI6DmwwYDtUJU6W6wQXsfyTm6CNMv0eE0wFXmZvoKaL24fggkp99dX+m1vgMQJ39JblVH9cmnnkBQcKkV8lnQJ003fd6iIFzGpgPBW5Z3T1Bp7uzSGMWnHmrEw8eOpKC5ny4x8uoViXDmA2UId23xYSoJ/GQrMjqB+NslqnuVsOBE1oWpNrmfSKhGU1X0kR4Eves56t5i5n3XU+7ne0MkcUzlrMi89n2j8aouf0zeuD7o+ngqvfRCsOqjaU71XWtuD4ogu2X7/Ajtwkxg/UJDFGAnCxFTTd4dqrrEpKyMK8eWBMaartFxwwrH39HMpx1T9JgknJ1hFWELzG8b302sKy64nCseOTGaZrdH63pjGkT7vzyIxVH/b+yJwDRmy/PlLz7fmUj6zpTBNmCtl1EGFOEFdtI2R04EprIkLXbtpoIPA7m0TPZURpnWufCSsDtD91ChxR8j/FnQ/gOOyKg/EJrTcHvM1e50PMRmoRZGlltBRRwBV+ArPO64On6zygr5zud5o/aADF1laBjkuYkjvUVsXwgnaIKbTLN2+sr/WjogxT1Yins79jPa1+3dDenxZtE/rHA/6qsdJmo5BJZqNYQUFrnpkU428LryMnBaNp2BW51JRsWXPAA7yCi0wDlHzEDxpqaOnhI4Ol87ra+VAg==&p=
sid: 507
ClientIP: {ip}
ClientPort: {port}
ABCHMigrated: 1
MPOPEnabled: 1

'''

PAYLOAD_MSG_2 = '''MIME-Version: 1.0
Content-Type: text/x-msmsgsinitialmdatanotification; charset=UTF-8

Mail-Data: {md}
Inbox-URL: /cgi-bin/HoTMaiL
Folders-URL: /cgi-bin/folders
Post-URL: http://www.hotmail.com
'''

PAYLOAD_MSG_3 = '''MIME-Version: 1.0
Content-Type: text/x-msmsgsoimnotification; charset=UTF-8

Mail-Data: {md}
'''

PAYLOAD_MSG_4 = '''MIME-Version: 1.0
Content-Type: text/x-msmsgsactivemailnotification; charset=UTF-8

Src-Folder: .!!OIM
Dest-Folder: .!!trAsH
Message-Delta: 1
'''

SHIELDS = '''<?xml version="1.0" encoding="utf-8" ?>
<config>
	<shield><cli maj="7" min="0" minbld="0" maxbld="9999" deny=" " /></shield>
	<block></block>
</config>'''.encode('utf-8')
TIMESTAMP = '2000-01-01T00:00:00.0-00:00'

def _uuid_to_high_low(uuid_str: str) -> Tuple[int, int]:
	import uuid
	u = uuid.UUID(uuid_str)
	high = u.time_low % (1<<32)
	low = u.node % (1<<32)
	return (high, low)
