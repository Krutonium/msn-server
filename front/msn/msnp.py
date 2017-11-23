import io, time
from typing import List
from urllib.parse import quote, unquote
from lxml.objectify import fromstring as parse_xml

from core.models import Substatus, Lst
from core import event

class MSNPWriter:
	def __init__(self, logger, sess_state: 'MSNP_NS_SessState'):
		self.logger = logger
		self._buf = io.BytesIO()
		self._sess_state = sess_state
	
	def write(self, outgoing_event):
		if isinstance(outgoing_event, MSNPOutgoingEvent):
			_msnp_encode(outgoing_event.m, self._buf, self.logger)
			return
		if isinstance(outgoing_event, event.PresenceNotificationEvent):
			for m in _build_msnp_presence_notif(None, outgoing_event.contact, self._sess_state.dialect):
				_msnp_encode(m, self._buf, self.logger)
			return
		
		raise Exception("Unknown outgoing_event", outgoing_event)
	
	def flush(self):
		data = self._buf.getvalue()
		if data:
			self._buf = io.BytesIO()
		return data

class MSNPReader:
	def __init__(self, logger, sess, ns):
		self.logger = logger
		self._data = b''
		self._i = 0
		self.sess = sess
		self.ns = ns
	
	def __iter__(self):
		return self
	
	def data_received(self, data):
		if self._data:
			self._data += data
		else:
			self._data = data
		while self._data:
			m = self._read_msnp()
			if m is None: break
			yield m
	
	def _read_msnp(self):
		try:
			m, e = _msnp_try_decode(self._data, self._i)
		except AssertionError:
			return None
		except Exception:
			print("ERR _read_msnp", self._i, self._data)
			raise
		
		self._data = self._data[e:]
		self._i = 0
		if m[0] in ('UUX', 'MSG'):
			self.logger.info('>>>', *map(quote, m[:-1]), len(m[-1]))
		else:
			self.logger.info('>>>', *map(quote, m))
		return MSNPEvent(m, self.sess, self.ns)
	
	def _read_raw(self, n):
		i = self._i
		e = i + n
		assert e <= len(self._data)
		self._i += n
		return self._data[i:e]

def _msnp_try_decode(d, i) -> (List[str], int):
	# Try to parse an MSNP message from buffer `d` starting at index `i`
	# Returns (parsed message, end index)
	e = d.find(b'\n', i)
	assert e >= 0
	e += 1
	m = d[i:e].decode('utf-8').strip()
	assert len(m) > 1
	m = m.split()
	m = [unquote(x) for x in m]
	if m[0] in PAYLOAD_COMMANDS:
		n = int(m[-1])
		assert e+n <= len(d)
		m[-1] = d[e:e+n]
		e += n
	return m, e

def _msnp_encode(m: List[object], buf, logger) -> None:
	m = list(m)
	data = None
	if isinstance(m[-1], bytes):
		data = m[-1]
		m[-1] = len(data)
	m = tuple(str(x).replace(' ', '%20') for x in m if x is not None)
	logger.info('<<<', *m)
	w = buf.write
	w(' '.join(m).encode('utf-8'))
	w(b'\r\n')
	if data is not None:
		w(data)

def _encode_msnobj(msnobj):
	if msnobj is None: return None
	return quote(msnobj, safe = '')

MSNP_DIALECTS = ['MSNP{}'.format(d) for d in (
	# Not actually supported
	21, 20, 19, 18, 17, 16,
	# Actually supported
	15, 14, 13, 12, 11, 10,
	9, 8, 7, 6, 5, 4, 3, 2,
)]

PAYLOAD_COMMANDS = {
	'UUX', 'MSG', 'ADL', 'FQY', 'RML', 'UUN'
}

class MSNPEvent:
	def __init__(self, m, sess, ns):
		self.sess = sess
		self.ns = ns
		self.m = m
	
	def apply(self):
		m = self.m
		method = getattr(self, '_{}_{}'.format(self.sess.state.state, m[0].lower()), None)
		if method:
			method(*m[1:])
		else:
			self._generic_cmd(m)
	
	def _generic_cmd(self, m):
		sess = self.sess
		if m[0] == 'OUT':
			sess.close()
			return
		self.logger.info("unknown (state = {}): {}".format(sess.state.state, m))
	
	def _a_ver(self, trid, *args):
		dialects = [a.upper() for a in args]
		d = None
		for d in MSNP_DIALECTS:
			if d in dialects: break
		if d not in dialects:
			self._reply('VER', trid, 0, *MSNP_DIALECTS)
			return
		self.sess.state.dialect = int(d[4:])
		self._reply('VER', trid, d)
	
	def _a_cvr(self, trid, *args):
		v = args[5]
		self._reply('CVR', trid, v, v, v, 'https://escargot.log1p.xyz', 'https://escargot.log1p.xyz')
	
	def _a_inf(self, trid):
		if self.sess.state.dialect < 8:
			self._reply('INF', trid, 'MD5')
		else:
			self._reply(Err.CommandDisabled, trid)
	
	def _a_usr(self, trid, authtype, stage, *args):
		sess = self.sess
		
		if authtype == 'MD5':
			if sess.state.dialect >= 8:
				self._reply(Err.CommandDisabled, trid)
				return
			if stage == 'I':
				email = args[0]
				salt = self.ns.login_get_md5_salt(sess, email)
				if salt is None:
					# Account is not enabled for login via MD5
					# TODO: Can we pass an informative message to user?
					self._reply(Err.AuthFail, trid)
					return
				sess.state.usr_email = email
				self._reply('USR', trid, authtype, 'S', salt)
				return
			if stage == 'S':
				token = args[0]
				self.ns.login_md5_verify(sess, sess.state.usr_email, token)
				self._util_usr_final(trid)
				return
		
		if authtype in ('TWN', 'SSO'):
			if stage == 'I':
				#>>> USR trid TWN/SSO I email@example.com
				sess.state.usr_email = args[0]
				if authtype == 'TWN':
					#token = ('ct={},rver=5.5.4177.0,wp=FS_40SEC_0_COMPACT,lc=1033,id=507,ru=http:%2F%2Fmessenger.msn.com,tw=0,kpp=1,kv=4,ver=2.1.6000.1,rn=1lgjBfIL,tpf=b0735e3a873dfb5e75054465196398e0'.format(int(time())),)
					# This seems to work too:
					token = ('ct=1,rver=1,wp=FS_40SEC_0_COMPACT,lc=1,id=1',)
				else:
					token = ('MBI_KEY_OLD', 'Unused_USR_I_SSO')
				self._reply('USR', trid, authtype, 'S', *token)
				return
			if stage == 'S':
				#>>> USR trid TWN S auth_token
				#>>> USR trid SSO S auth_token b64_response
				token = args[0]
				if token[0:2] == 't=':
					token = token[2:22]
				self.ns.login_twn_verify(sess, sess.state.usr_email, token)
				self._util_usr_final(trid)
				return
		
		self._reply(Err.AuthFail, trid)
	
	def _util_usr_final(self, trid):
		sess = self.sess
		user = sess.user
		dialect = sess.state.dialect
		
		if user is None:
			self._reply(Err.AuthFail, trid)
			return
		
		if dialect < 10:
			args = (user.status.name,)
		else:
			args = ()
		if dialect >= 8:
			#verified = self.user.verified
			verified = True
			args += ((1 if verified else 0), 0)
		
		self._reply('USR', trid, 'OK', user.email, *args)
		sess.state.state = MSNP_NS_SessState.STATE_LIVE
		
		if dialect < 13:
			return
		
		(high, low) = _uuid_to_high_low(user.uuid)
		(ip, port) = sess.get_peername()
		now = time.time()
		
		self._reply('SBS', 0, 'null')
		self._reply('PRP', 'MFN', user.status.name)
		
		msg1 = PAYLOAD_MSG_1.format(
			time = now, high = high, low = low,
			token = sess.state.token, ip = ip, port = port,
		)
		self._reply('MSG', 'Hotmail', 'Hotmail', msg1.replace('\n', '\r\n').encode('ascii'))
		
		msg2 = PAYLOAD_MSG_2
		self._reply('MSG', 'Hotmail', 'Hotmail', msg2.replace('\n', '\r\n').encode('ascii'))
	
	# State = Live
	
	def _l_syn(self, trid, *extra):
		sess = self.sess
		user = sess.user
		dialect = sess.state.dialect
		detail = user.detail
		contacts = detail.contacts
		groups = detail.groups
		settings = detail.settings
		
		if dialect < 10:
			sess.state.syn_ser = int(extra[0])
			ser = self._ser()
			if dialect < 6:
				self._reply('SYN', trid, ser)
				for lst in (Lst.FL, Lst.AL, Lst.BL, Lst.RL):
					cs = [c for c in contacts.values() if c.lists & lst]
					if cs:
						for i, c in enumerate(cs):
							self._reply('LST', trid, lst.name, ser, len(cs), i + 1, c.head.email, c.status.name)
					else:
						self._reply('LST', trid, lst.name, ser, 0, 0)
				self._reply('GTC', trid, ser, settings.get('GTC', 'A'))
				self._reply('BLP', trid, ser, settings.get('BLP', 'AL'))
			elif dialect < 8:
				self._reply('SYN', trid, ser)
				num_groups = len(groups) + 1
				self._reply('LSG', trid, ser, 1, num_groups, '0', "Other Contacts", 0)
				for i, g in enumerate(groups.values()):
					self._reply('LSG', trid, ser, i + 2, num_groups, g.id, g.name, 0)
				for lst in (Lst.FL, Lst.AL, Lst.BL, Lst.RL):
					cs = [c for c in contacts.values() if c.lists & lst]
					if cs:
						for i, c in enumerate(cs):
							gs = ((','.join(c.groups) or '0') if lst == Lst.FL else None)
							self._reply('LST', trid, lst.name, ser, i + 1, len(cs), c.head.email, c.status.name, gs)
					else:
						self._reply('LST', trid, lst.name, ser, 0, 0)
				self._reply('GTC', trid, ser, settings.get('GTC', 'A'))
				self._reply('BLP', trid, ser, settings.get('BLP', 'AL'))
			else:
				num_groups = len(groups) + 1
				self._reply('SYN', trid, ser, len(contacts), num_groups)
				self._reply('GTC', settings.get('GTC', 'A'))
				self._reply('BLP', settings.get('BLP', 'AL'))
				self._reply('LSG', '0', "Other Contacts", 0)
				for g in groups.values():
					self._reply('LSG', g.id, g.name, 0)
				for c in contacts.values():
					self._reply('LST', c.head.email, c.status.name, c.lists, ','.join(c.groups) or '0')
		else:
			self._reply('SYN', trid, TIMESTAMP, TIMESTAMP, len(contacts), len(groups))
			self._reply('GTC', settings.get('GTC', 'A'))
			self._reply('BLP', settings.get('BLP', 'AL'))
			self._reply('PRP', 'MFN', user.status.name)
			
			for g in groups.values():
				self._reply('LSG', g.name, g.id)
			for c in contacts.values():
				self._reply('LST', 'N={}'.format(c.head.email), 'F={}'.format(c.status.name), 'C={}'.format(c.head.uuid),
					c.lists, (None if dialect < 12 else 1), ','.join(c.groups)
				)
	
	def _l_gcf(self, trid, filename):
		self._reply('GCF', trid, filename, SHIELDS)
	
	def _l_png(self):
		self._reply('QNG', (60 if self.sess.state.dialect >= 9 else None))
	
	def _l_uux(self, trid, data):
		elm = parse_xml(data.decode('utf-8'))
		self.ns.me_update(self.sess, {
			'message': str(elm.find('PSM')),
			'media': str(elm.find('CurrentMedia')),
		})
		self._reply('UUX', trid, 0)
	
	def _l_url(self, trid, *ignored):
		self._reply('URL', trid, '/unused1', '/unused2', 1)
	
	def _l_adg(self, trid, name, ignored = None):
		#>>> ADG 276 New Group
		try:
			group = self.ns.me_group_add(self.sess, name)
		except ex:
			self._reply(Err.GetCodeForException(ex), trid)
			return
		self._reply('ADG', trid, self._ser(), name, group.id, 0)
	
	def _l_rmg(self, trid, group_id):
		#>>> RMG 250 00000000-0000-0000-0001-000000000001
		if group_id == 'New%20Group':
			# Bug: MSN 7.0 sends name instead of id in a particular scenario
			for g in self.sess.user.detail.groups.values():
				if g.name != 'New Group': continue
				group_id = g.id
				break
		
		try:
			self.ns.me_group_remove(self.sess, group_id)
		except ex:
			self._reply(Err.GetCodeForException(ex), trid)
			return
		
		self._reply('RMG', trid, self._ser() or 1, group_id)
	
	def _l_reg(self, trid, group_id, name, ignored = None):
		#>>> REG 275 00000000-0000-0000-0001-000000000001 newname
		try:
			self.ns.me_group_edit(self.sess, group_id, name)
		except ex:
			self._reply(Err.GetCodeForException(ex), trid)
			return
		if self.sess.state.dialect < 10:
			self._reply('REG', trid, self._ser(), group_id, name, 0)
		else:
			self._reply('REG', trid, 1, name, group_id, 0)
	
	def _l_adc(self, trid, lst_name, arg1, arg2 = None):
		if arg1.startswith('N='):
			#>>> ADC 249 BL N=bob1@hotmail.com
			#>>> ADC 278 AL N=foo@hotmail.com
			#>>> ADC 277 FL N=foo@hotmail.com F=foo@hotmail.com
			contact_uuid = self.ns.util_get_uuid_from_email(arg1[2:])
			group_id = None
			name = (arg2[2:] if arg2 else None)
		else:
			# Add C= to group
			#>>> ADC 246 FL C=00000000-0000-0000-0002-000000000002 00000000-0000-0000-0001-000000000003
			contact_uuid = arg1[2:]
			group_id = arg2
			name = None
		
		self._add_common(trid, lst_name, contact_uuid, name, group_id)
	
	def _l_add(self, trid, lst_name, email, name = None, group_id = None):
		#>>> ADD 122 FL email name group
		contact_uuid = self.ns.util_get_uuid_from_email(email)
		self._add_common(trid, lst_name, contact_uuid, name, group_id)
	
	def _add_common(self, trid, lst_name, contact_uuid, name = None, group_id = None):
		lst = getattr(Lst, lst_name)
		
		try:
			ctc, ctc_head = self.ns.me_contact_add(self.sess, contact_uuid, lst, name)
			if group_id:
				self.ns.me_group_contact_add(self.sess, group_id, contact_uuid)
		except ex:
			self._reply(Err.GetCodeForException(ex), trid)
			return
		
		self.nb.generic_notify(self)
		
		if self.sess.state.dialect >= 10:
			if lst == Lst.FL:
				if group_id:
					self._reply('ADC', trid, lst_name, 'C={}'.format(ctc_head.uuid), group_id)
				else:
					self._reply('ADC', trid, lst_name, 'N={}'.format(ctc_head.email), 'C={}'.format(ctc_head.uuid))
			else:
				self._reply('ADC', trid, lst_name, 'N={}'.format(ctc_head.email))
		else:
			self._reply('ADD', trid, lst_name, self._ser(), ctc_head.email, name, group_id)
	
	def _l_rem(self, trid, lst_name, usr, group_id = None):
		lst = getattr(Lst, lst_name)
		if lst is Lst.RL:
			self.sess.close()
			return
		if lst is Lst.FL:
			#>>> REM 279 FL 00000000-0000-0000-0002-000000000001
			#>>> REM 247 FL 00000000-0000-0000-0002-000000000002 00000000-0000-0000-0001-000000000002
			if self.dialect < 10:
				contact_uuid = self.ns.util_get_uuid_from_email(usr)
			else:
				contact_uuid = usr
		else:
			#>>> REM 248 AL bob1@hotmail.com
			contact_uuid = self.ns.util_get_uuid_from_email(usr)
		try:
			if group_id:
				self.ns.me_group_contact_remove(self.sess, group_id, contact_uuid)
			else:
				self.ns.me_contact_remove(self.sess, contact_uuid, lst)
		except ex:
			self._reply(Err.GetCodeForException(ex), trid)
			return
		self._reply('REM', trid, lst_name, self._ser(), usr, group_id)
	
	def _l_gtc(self, trid, value):
		if self.sess.state.dialect >= 13:
			self._reply(Err.CommandDisabled, trid)
			return
		# "Alert me when other people add me ..." Y/N
		#>>> GTC 152 N
		self.ns.me_update(self.sess, { 'gtc': value })
		self._reply('GTC', trid, self._ser(), value)
	
	def _l_blp(self, trid, value):
		# Check "Only people on my Allow List ..." AL/BL
		#>>> BLP 143 BL
		self.ns.me_update(self.sess, { 'blp': value })
		self._reply('BLP', trid, self._ser(), value)
	
	def _l_chg(self, trid, sts_name, capabilities = None, msnobj = None):
		#>>> CHG 120 BSY 1073791020 <msnobj .../>
		capabilities = capabilities or 0
		self.ns.me_update(self.sess, {
			'substatus': getattr(Substatus, sts_name),
			'capabilities': capabilities,
			'msnobj': msnobj,
		})
		self._reply('CHG', trid, sts_name, capabilities, _encode_msnobj(msnobj))
		
		# Send ILNs
		sess = self.sess
		state = sess.state
		if state.iln_sent:
			return
		state.iln_sent = True
		user = sess.user
		dialect = state.dialect
		for ctc in user.detail.contacts.values():
			for m in _build_msnp_presence_notif(trid, ctc, dialect):
				self._reply(*m)
	
	def _l_rea(self, trid, email, name):
		sess = self.sess
		if sess.state.dialect >= 10:
			self._reply(Err.CommandDisabled, trid)
			return
		if email == sess.user.email:
			self.ns.me_update(self.sess, { 'name': name })
		self._reply('REA', trid, self._ser(), email, name)
	
	def _l_snd(self, trid, email):
		# Send email about how to use MSN. Ignore it for now.
		self._reply('SND', trid, email)
	
	def _l_prp(self, trid, key, value):
		#>>> PRP 115 MFN ~~woot~~
		if key == 'MFN':
			self.ns.me_update(self.sess, { 'name': value })
		# TODO: Save other settings?
		self._reply('PRP', trid, key, value)
	
	def _l_sbp(self, trid, uuid, key, value):
		#>>> SBP 153 00000000-0000-0000-0002-000000000002 MFN Bob%201%20New
		# Can be ignored: core handles syncing contact names
		self._reply('SBP', trid, uuid, key, value)
	
	def _l_xfr(self, trid, dest):
		if dest != 'SB':
			self._reply(Err.InvalidParameter, trid)
			return
		dialect = self.sess.state.dialect
		token, sb = self.ns.sb_token_create(self.sess, extra_data = { 'dialect': dialect })
		extra = ()
		if dialect >= 13:
			extra = ('U', 'messenger.msn.com')
		if dialect >= 14:
			extra += (1,)
		self._reply('XFR', trid, dest, '{}:{}'.format(sb.host, sb.port), 'CKI', token, *extra)
	
	# These four commands appear to be useless:
	def _l_adl(self, trid, data):
		self._reply('ADL', trid, 'OK')
	def _l_rml(self, trid, data):
		self._reply('RML', trid, 'OK')
	def _l_fqy(self, trid, data):
		self._reply('FQY', trid, b'')
	def _l_uun(self, trid, email, arg0, data):
		self._reply('UUN', trid, 'OK')
	
	# Utils
	
	def _ser(self):
		state = self.sess.state
		if state.dialect >= 10:
			return None
		state.syn_ser += 1
		return state.syn_ser
	
	def _reply(self, *m):
		self.sess.send_event(MSNPOutgoingEvent(m))

def _build_msnp_presence_notif(trid, ctc, dialect):
	status = ctc.status
	is_offlineish = status.is_offlineish()
	if is_offlineish and trid is not None: return []
	head = ctc.head
	
	if dialect >= 14:
		networkid = 1
	else:
		networkid = None
	
	if is_offlineish:
		return [('FLN', head.email, networkid)]
	
	if trid: frst = ('ILN', trid)
	else: frst = ('NLN',)
	rst = []
	if dialect >= 8:
		rst.append(head.detail.capabilities)
	if dialect >= 9:
		rst.append(_encode_msnobj(head.detail.msnobj or '<msnobj/>'))
	
	msgs = [(*frst, status.substatus.name, head.email, networkid, status.name, *rst)]
	
	if dialect >= 11:
		msgs.append(('UBX', head.email, networkid, '<Data><PSM>{}</PSM><CurrentMedia>{}</CurrentMedia></Data>'.format(
			status.message or '', status.media or ''
		).encode('utf-8')))
	
	return msgs

class MSNPOutgoingEvent:
	def __init__(self, m):
		self.m = m

PAYLOAD_MSG_1 = '''MIME-Version: 1.0
Content-Type: text/x-msmsgsprofile; charset=UTF-8
LoginTime: {time}
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
sid: 507
MSPAuth: t={token}Y6+H31sTUOFkqjNTDYqAAFLr5Ote7BMrMnUIzpg860jh084QMgs5djRQLLQP0TVOFkKdWDwAJdEWcfsI9YL8otN9kSfhTaPHR1njHmG0H98O2NE/Ck6zrog3UJFmYlCnHidZk1g3AzUNVXmjZoyMSyVvoHLjQSzoGRpgHg3hHdi7zrFhcYKWD8XeNYdoz9wfA2YAAAgZIgF9kFvsy2AC0Fl/ezc/fSo6YgB9TwmXyoK0wm0F9nz5EfhHQLu2xxgsvMOiXUSFSpN1cZaNzEk/KGVa3Z33Mcu0qJqvXoLyv2VjQyI0VLH6YlW5E+GMwWcQurXB9hT/DnddM5Ggzk3nX8uMSV4kV+AgF1EWpiCdLViRI6DmwwYDtUJU6W6wQXsfyTm6CNMv0eE0wFXmZvoKaL24fggkp99dX+m1vgMQJ39JblVH9cmnnkBQcKkV8lnQJ003fd6iIFzGpgPBW5Z3T1Bp7uzSGMWnHmrEw8eOpKC5ny4x8uoViXDmA2UId23xYSoJ/GQrMjqB+NslqnuVsOBE1oWpNrmfSKhGU1X0kR4Eves56t5i5n3XU+7ne0MkcUzlrMi89n2j8aouf0zeuD7o+ngqvfRCsOqjaU71XWtuD4ogu2X7/Ajtwkxg/UJDFGAnCxFTTd4dqrrEpKyMK8eWBMaartFxwwrH39HMpx1T9JgknJ1hFWELzG8b302sKy64nCseOTGaZrdH63pjGkT7vzyIxVH/b+yJwDRmy/PlLz7fmUj6zpTBNmCtl1EGFOEFdtI2R04EprIkLXbtpoIPA7m0TPZURpnWufCSsDtD91ChxR8j/FnQ/gOOyKg/EJrTcHvM1e50PMRmoRZGlltBRRwBV+ArPO64On6zygr5zud5o/aADF1laBjkuYkjvUVsXwgnaIKbTLN2+sr/WjogxT1Yins79jPa1+3dDenxZtE/rHA/6qsdJmo5BJZqNYQUFrnpkU428LryMnBaNp2BW51JRsWXPAA7yCi0wDlHzEDxpqaOnhI4Ol87ra+VAg==
ClientIP: {ip}
ClientPort: {port}
ABCHMigrated: 1
MPOPEnabled: 0

'''

PAYLOAD_MSG_2 = '''MIME-Version: 1.0
Content-Type: text/x-msmsgsinitialmdatanotification; charset=UTF-8
Mail-Data: <MD><E><I>0</I><IU>0</IU><O>0</O><OU>0</OU></E><Q><QTM>409600</QTM><QNM>204800</QNM></Q></MD>
Inbox-URL: /cgi-bin/HoTMaiL
Folders-URL: /cgi-bin/folders
Post-URL: http://www.hotmail.com

'''

SHIELDS = '''<?xml version="1.0" encoding="utf-8" ?>
<config>
	<shield><cli maj="7" min="0" minbld="0" maxbld="9999" deny=" " /></shield>
	<block></block>
</config>'''.encode('utf-8')
TIMESTAMP = '2000-01-01T00:00:00.0-00:00'

def _uuid_to_high_low(u):
	import uuid
	u = uuid.UUID(u)
	high = u.time_low % (1<<32)
	low = u.node % (1<<32)
	return (high, low)

class MSNP_NS_SessState:
	STATE_AUTH = 'a'
	STATE_LIVE = 'l'
	
	def __init__(self):
		self.state = MSNP_NS_SessState.STATE_AUTH
		self.dialect = None
		self.usr_email = None
		self.token = None
		self.syn_ser = None
		self.iln_sent = False

class Err:
	InvalidParameter = 201
	InvalidPrincipal = 205
	PrincipalOnList = 215
	PrincipalNotOnList = 216
	PrincipalNotOnline = 217
	GroupInvalid = 224
	PrincipalNotInGroup = 225
	GroupNameTooLong = 229
	GroupZeroUnremovable = 230
	InternalServerError = 500
	CommandDisabled = 502
	AuthFail = 911
	
	@classmethod
	def GetCodeForException(cls, exc):
		# TODO
		raise NotImplementedError
