import asyncio
import time
from collections import defaultdict
from uuid import UUID

from util.msnp import Err, MSNPException
from util.contacts import ContactsService
from models import Contact, Group, UserStatus, Substatus, Lst

class NB:
	def __init__(self, user_service, auth_service, sbservices):
		self._user_service = user_service
		self._auth_service = auth_service
		self._sbservices = sbservices
		self._ncs = NBConnCollection()
		# Dict[User.uuid, User]
		self._user_by_uuid = {}
		# Dict[User, UserDetail]
		self._unsynced_db = {}
		
		# TODO: NB isn't guaranteed to be run in a loop (e.g. testing).
		# Need to figure out a better way to do this.
		asyncio.get_event_loop().create_task(self._sync_db())
	
	def pre_login(self, email, pwd):
		uuid = self._user_service.login(email, pwd)
		if uuid is None: return None
		return self._auth_service.create_token('nb/login', uuid)
	
	def login(self, nc, email, token):
		uuid = self._auth_service.pop_token('nb/login', token)
		return self._login_common(nc, uuid, email, token)
	
	def login_md5(self, nc, email, md5_hash):
		uuid = self._user_service.login_md5(email, md5_hash)
		return self._login_common(nc, uuid, email, None)
	
	def get_nbconn(self, token):
		return self._ncs.get_nc_by_token(token)
	
	def _login_common(self, nc, uuid, email, token):
		if uuid is None: return None
		self._user_service.update_date_login(uuid)
		user = self.load_user_record(uuid)
		nc.user = user
		nc.token = token
		self._ncs.add_nc(nc)
		user.detail = self.load_detail(user)
		return user
	
	def generic_notify(self, nc):
		# Notify relevant `NBConn`s of status, name, message, media
		user = nc.user
		if user is None: return
		for nc1 in self._ncs.get_ncs():
			if nc1 == nc: continue
			user1 = nc1.user
			if user1.detail is None: continue
			ctc = user1.detail.contacts.get(user.uuid)
			if ctc is None: continue
			nc1.notify_presence(ctc)
	
	def sync_contact_statuses(self):
		# Recompute all `Contact.status`'s
		for user in self._user_by_uuid.values():
			detail = user.detail
			if detail is None: continue
			for ctc in detail.contacts.values():
				ctc.compute_visible_status(user)
	
	def load_user_record(self, uuid):
		if uuid not in self._user_by_uuid:
			user = self._user_service.get(uuid)
			if user is None: return None
			self._user_by_uuid[uuid] = user
		return self._user_by_uuid[uuid]
	
	def load_detail(self, user):
		if user.detail: return user.detail
		return self._user_service.get_detail(user.uuid)
	
	def mark_modified(self, user, *, detail = None):
		ud = user.detail or detail
		if detail: assert ud is detail
		assert ud is not None
		self._unsynced_db[user] = ud
	
	def notify_call(self, caller_uuid, callee_email, sbsess_id):
		caller = self._user_by_uuid.get(caller_uuid)
		if caller is None: return Err.InternalServerError
		if caller.detail is None: return Err.InternalServerError
		callee_uuid = self._user_service.get_uuid(callee_email)
		if callee_uuid is None: return Err.InternalServerError
		ctc = caller.detail.contacts.get(callee_uuid)
		if ctc is None: return Err.InternalServerError
		if ctc.status.is_offlineish(): return Err.PrincipalNotOnline
		ctc_ncs = self._ncs.get_ncs_by_user(ctc.head)
		if not ctc_ncs: return Err.PrincipalNotOnline
		for ctc_nc in ctc_ncs:
			token = self._auth_service.create_token('sb/cal', { 'uuid': ctc.head.uuid, 'dialect': ctc_nc.dialect })
			ctc_nc.notify_ring(sbsess_id, token, caller)
	
	def get_sbservice(self):
		return self._sbservices[0]
	
	def on_leave(self, nc):
		user = nc.user
		if user is None: return
		self._ncs.remove_nc(nc)
		if self._ncs.get_ncs_by_user(user):
			# There are still other people logged in as this user,
			# so don't send offline notifications.
			return
		# User is offline, send notifications
		user.detail = None
		self.sync_contact_statuses()
		self.generic_notify(nc)
	
	def notify_reverse_add(self, user1, user2):
		# `user2` was added to `user1`'s RL
		if user1 == user2: return
		for nc in self._ncs.get_ncs_by_user(user1):
			nc.notify_add_rl(user2)
	
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

class NBConnCollection:
	def __init__(self):
		# Dict[User, Set[NBConn]]
		self._ncs_by_user = defaultdict(set)
		# Dict[NBConn.token, NBConn]
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

class NBConn:
	STATE_QUIT = 'q'
	STATE_AUTH = 'a'
	STATE_LIVE = 'l'
	
	DIALECTS = ['MSNP{}'.format(d) for d in (
		# Not actually supported
		21, 20, 19, 18, 17, 16, 15, 14, 13,
		# Actually supported
		12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2
	)]
	
	def __init__(self, nb, writer):
		self.nb = nb
		self.writer = writer
		self._user_service = nb._user_service
		self._contacts = None
		self.state = NBConn.STATE_AUTH
		self.dialect = None
		self.iln_sent = False
		self.user = None
		self.token = None
		self.syn_ser = None
		self.usr_email = None
	
	def connection_lost(self):
		self.nb.on_leave(self)
	
	# Hooks for NB
	
	def notify_ring(self, sbsess_id, token, caller):
		sb = self.nb.get_sbservice()
		extra = ()
		if self.dialect >= 13:
			extra = ('U', 'messenger.hotmail.com')
		if self.dialect >= 14:
			extra += (1,)
		self.writer.write('RNG', sbsess_id, '{}:{}'.format(sb.host, sb.port), 'CKI', token, caller.email, caller.status.name, *extra)
	
	def notify_presence(self, ctc):
		self._send_presence_notif(None, ctc)
	
	def notify_add_rl(self, user):
		name = (user.status.name or user.email)
		if self.dialect < 10:
			self.writer.write('ADD', 0, Lst.RL.name, user.email, name)
		else:
			self.writer.write('ADC', 0, Lst.RL.name, 'N={}'.format(user.email), 'F={}'.format(name))
	
	# State = Auth
	
	def _a_ver(self, trid, *args):
		dialects = [a.upper() for a in args]
		d = None
		for d in NBConn.DIALECTS:
			if d in dialects: break
		if d not in dialects:
			self.writer.write('VER', trid, 0, *NBConn.DIALECTS)
			return
		self.writer.write('VER', trid, d)
		self.dialect = int(d[4:])
	
	def _a_cvr(self, trid, *args):
		v = args[5]
		self.writer.write('CVR', trid, v, v, v, 'http://escargot.log1p.xyz/', 'http://escargot.log1p.xyz/')
	
	def _a_inf(self, trid):
		if self.dialect < 8:
			self.writer.write('INF', trid, 'MD5')
		else:
			self.writer.write(Err.CommandDisabled, trid)
	
	def _a_usr(self, trid, authtype, stage, *args):
		if authtype == 'MD5':
			if self.dialect >= 8:
				self.writer.write(Err.CommandDisabled, trid)
				return
			if stage == 'I':
				email = args[0]
				salt = self._user_service.get_md5_salt(email)
				if salt is None:
					# Account is not enabled for login via MD5
					# TODO: Can we pass an informative message to user?
					self.writer.write(Err.AuthFail, trid)
					return
				self.usr_email = email
				self.writer.write('USR', trid, authtype, 'S', salt)
				return
			if stage == 'S':
				token = args[0]
				self.nb.login_md5(self, self.usr_email, token)
				self._util_usr_final(trid)
				return
		
		if authtype in ('TWN', 'SSO'):
			if stage == 'I':
				#>>> USR trid TWN/SSO I email@example.com
				self.usr_email = args[0]
				if authtype == 'TWN':
					#token = ('ct={},rver=5.5.4177.0,wp=FS_40SEC_0_COMPACT,lc=1033,id=507,ru=http:%2F%2Fmessenger.msn.com,tw=0,kpp=1,kv=4,ver=2.1.6000.1,rn=1lgjBfIL,tpf=b0735e3a873dfb5e75054465196398e0'.format(int(time())),)
					# This seems to work too:
					token = ('ct=1,rver=1,wp=FS_40SEC_0_COMPACT,lc=1,id=1',)
				else:
					# The second value is used by WLM >= 9. Currently unknown what it does.
					token = ('MBI_KEY_OLD', '8CLhG/xfgYZ7TyRQ/jIAWyDmd/w4R4GF2yKLS6tYrnjzi4cFag/Nr+hxsfg5zlCf')
				self.writer.write('USR', trid, authtype, 'S', *token)
				return
			if stage == 'S':
				#>>> USR trid TWN S auth_token
				#>>> USR trid SSO S auth_token b64_response
				token = args[0]
				if (token[0:2] == 't='):
					token = token[2:22]
				self.nb.login(self, self.usr_email, token)
				self._util_usr_final(trid)
				return
		
		self.writer.write(Err.AuthFail, trid)
	
	def _util_usr_final(self, trid):
		if self.user is None:
			self.writer.write(Err.AuthFail, trid)
			return
		if self.dialect < 10:
			args = (self.user.status.name,)
		else:
			args = ()
		#verified = self.user.verified
		verified = True
		if self.dialect >= 8:
			args += ((1 if verified else 0), 0)
		self.writer.write('USR', trid, 'OK', self.user.email, *args)
		self.state = NBConn.STATE_LIVE
		self._contacts = ContactsService(self.nb, self.user)
		
		if self.dialect < 13:
			return
		
		# calculate member ID
		(high, low) = self._splituuid(self.user.uuid)
		
		if self.transport:
			(ip, port) = self.transport.get_extra_info('peername')
		else:
			# TODO: Need to handle this when implementing persistence-less chat (HTTP gateway/XMPP)
			(ip, port) = ('', '')
		
		self.writer.write('SBS', 0, 'null')
		if 18 <= self.dialect < 21:
			# MSNP21 doesn't use this; unsure if 19/20 use it
			self.writer.write('UBX', '1:' + self.user.email, '0')
		self.writer.write('PRP', 'MFN', self.user.status.name)
		
		# build MSG Hotmail payload
		msg1 = '''MIME-Version: 1.0
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

'''.format(
			time = time.time(),
			high = high,
			low = low,
			token = self.token,
			ip = ip,
			port = port
		)
		self.writer.write('MSG', 'Hotmail', 'Hotmail', msg1.replace('\n', '\r\n').encode('ascii'))
		
		msg2 = '''MIME-Version: 1.0
Content-Type: text/x-msmsgsinitialmdatanotification; charset=UTF-8

Mail-Data: <MD><E><I>0</I><IU>0</IU><O>0</O><OU>0</OU></E><Q><QTM>409600</QTM><QNM>204800</QNM></Q></MD>
Inbox-URL: /cgi-bin/HoTMaiL
Folders-URL: /cgi-bin/folders
Post-URL: http://www.hotmail.com
'''.format(
			time = time.time(),
			high = high,
			low = low,
			token = self.token,
			ip = ip,
			port = port
		)
		self.writer.write('MSG', 'Hotmail', 'Hotmail', msg2.replace('\n', '\r\n').encode('ascii'))
	
	# State = Live
	
	def _l_syn(self, trid, *extra):
		writer = self.writer
		user = self.user
		detail = user.detail
		contacts = detail.contacts
		groups = detail.groups
		settings = detail.settings
		
		if self.dialect < 10:
			self.syn_ser = int(extra[0])
			ser = self._ser()
			if self.dialect < 6:
				writer.write('SYN', trid, ser)
				for lst in (Lst.FL, Lst.AL, Lst.BL, Lst.RL):
					cs = [c for c in contacts.values() if c.lists & lst]
					if cs:
						for i, c in enumerate(cs):
							writer.write('LST', trid, lst.name, ser, len(cs), i + 1, c.head.email, c.status.name)
					else:
						writer.write('LST', trid, lst.name, ser, 0, 0)
				writer.write('GTC', trid, ser, settings.get('GTC', 'A'))
				writer.write('BLP', trid, ser, settings.get('BLP', 'AL'))
			elif self.dialect < 8:
				writer.write('SYN', trid, ser)
				num_groups = len(groups) + 1
				writer.write('LSG', trid, ser, 1, num_groups, '0', "Other Contacts", 0)
				for i, g in enumerate(groups.values()):
					writer.write('LSG', trid, ser, i + 2, num_groups, g.id, g.name, 0)
				for lst in (Lst.FL, Lst.AL, Lst.BL, Lst.RL):
					cs = [c for c in contacts.values() if c.lists & lst]
					if cs:
						for i, c in enumerate(cs):
							gs = ((','.join(c.groups) or '0') if lst == Lst.FL else None)
							writer.write('LST', trid, lst.name, ser, i + 1, len(cs), c.head.email, c.status.name, gs)
					else:
						writer.write('LST', trid, lst.name, ser, 0, 0)
				writer.write('GTC', trid, ser, settings.get('GTC', 'A'))
				writer.write('BLP', trid, ser, settings.get('BLP', 'AL'))
			else:
				num_groups = len(groups) + 1
				writer.write('SYN', trid, ser, len(contacts), num_groups)
				writer.write('GTC', settings.get('GTC', 'A'))
				writer.write('BLP', settings.get('BLP', 'AL'))
				writer.write('LSG', '0', "Other Contacts", 0)
				for g in groups.values():
					writer.write('LSG', g.id, g.name, 0)
				for c in contacts.values():
					writer.write('LST', c.head.email, c.status.name, c.lists, ','.join(c.groups) or '0')
		else:
			writer.write('SYN', trid, TIMESTAMP, TIMESTAMP, len(contacts), len(groups))
			writer.write('GTC', settings.get('GTC', 'A'))
			writer.write('BLP', settings.get('BLP', 'AL'))
			writer.write('PRP', 'MFN', user.status.name)
			
			for g in groups.values():
				writer.write('LSG', g.name, g.id)
			for c in contacts.values():
				writer.write('LST', 'N={}'.format(c.head.email), 'F={}'.format(c.status.name), 'C={}'.format(c.head.uuid),
					c.lists, (None if self.dialect < 12 else 1), ','.join(c.groups)
				)
		self.state = NBConn.STATE_LIVE
	
	def _l_gcf(self, trid, filename):
		self.writer.write('GCF', trid, filename, SHIELDS)
	
	def _l_png(self):
		self.writer.write('QNG', (60 if self.dialect >= 9 else None))
	
	def _l_uux(self, trid, data):
		user = self.user
		user.status.message = data['PSM']
		user.status.media = data['CurrentMedia']
		self.nb.mark_modified(user)
		self.nb.sync_contact_statuses()
		self.nb.generic_notify(self)
		self.writer.write('UUX', trid, 0)
	
	def _l_url(self, trid, *ignored):
		self.writer.write('URL', trid, '/unused1', '/unused2', 1)
	
	def _l_adg(self, trid, name, ignored = None):
		#>>> ADG 276 New Group
		try:
			group = self._contacts.add_group(name)
		except MSNPException as ex:
			self.writer.write(ex.id, trid)
			return
		self.writer.write('ADG', trid, self._ser(), name, group.id, 0)
	
	def _l_rmg(self, trid, group_id):
		#>>> RMG 250 00000000-0000-0000-0001-000000000001
		if group_id == 'New%20Group':
			# Bug: MSN 7.0 sends name instead of id in a particular scenario
			for g in self.user.detail.groups.values():
				if g.name != 'New Group': continue
				group_id = g.id
				break
		
		try:
			self._contacts.remove_group(group_id)
		except MSNPException as ex:
			self.writer.write(ex.id, trid)
			return
		
		self.writer.write('RMG', trid, self._ser() or 1, group_id)
	
	def _l_reg(self, trid, group_id, name, ignored = None):
		#>>> REG 275 00000000-0000-0000-0001-000000000001 newname
		try:
			self._contacts.edit_group(group_id, name)
		except MSNPException as ex:
			self.writer.write(ex.id, trid)
			return
		if self.dialect < 10:
			self.writer.write('REG', trid, self._ser(), group_id, name, 0)
		else:
			self.writer.write('REG', trid, 1, name, group_id, 0)
	
	def _l_adc(self, trid, lst_name, arg1, arg2 = None):
		if arg1.startswith('N='):
			#>>> ADC 249 BL N=bob1@hotmail.com
			#>>> ADC 278 AL N=foo@hotmail.com
			#>>> ADC 277 FL N=foo@hotmail.com F=foo@hotmail.com
			contact_uuid = self._user_service.get_uuid(arg1[2:])
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
		contact_uuid = self._user_service.get_uuid(email)
		self._add_common(trid, lst_name, contact_uuid, name, group_id)
	
	def _add_common(self, trid, lst_name, contact_uuid, name = None, group_id = None):
		lst = getattr(Lst, lst_name)
		
		try:
			ctc, ctc_head = self._contacts.add_contact(contact_uuid, lst, name)
			if group_id:
				self._contacts.add_group_contact(group_id, contact_uuid)
		except MSNPException as ex:
			self.writer.write(ex.id, trid)
			return
		
		self.nb.generic_notify(self)
		
		if self.dialect >= 10:
			if lst == Lst.FL:
				if group_id:
					self.writer.write('ADC', trid, lst_name, 'C={}'.format(ctc_head.uuid), group_id)
				else:
					self.writer.write('ADC', trid, lst_name, 'N={}'.format(ctc_head.email), 'C={}'.format(ctc_head.uuid))
			else:
				self.writer.write('ADC', trid, lst_name, 'N={}'.format(ctc_head.email))
		else:
			self.writer.write('ADD', trid, lst_name, self._ser(), ctc_head.email, name, group_id)
		
		# TODO: This should be done in add_contact
		if lst is Lst.FL:
			self._send_presence_notif(trid, ctc)
	
	def _l_rem(self, trid, lst_name, usr, group_id = None):
		lst = getattr(Lst, lst_name)
		if lst is Lst.RL:
			self.state = NBConn.STATE_QUIT
			return
		if lst is Lst.FL:
			#>>> REM 279 FL 00000000-0000-0000-0002-000000000001
			#>>> REM 247 FL 00000000-0000-0000-0002-000000000002 00000000-0000-0000-0001-000000000002
			if self.dialect < 10:
				contact_uuid = self._user_service.get_uuid(usr)
			else:
				contact_uuid = usr
		else:
			#>>> REM 248 AL bob1@hotmail.com
			email = usr
			contact_uuid = None
			for ctc in self.user.detail.contacts.values():
				if ctc.head.email != email: continue
				contact_uuid = ctc.head.uuid
				break
		try:
			if group_id:
				self._contacts.remove_group_contact(group_id, contact_uuid)
			else:
				self._contacts.remove_contact(contact_uuid, lst)
		except MSNPException as ex:
			self.writer.write(ex.id, trid)
			return
		self.writer.write('REM', trid, lst_name, self._ser(), usr, group_id)
	
	def _l_gtc(self, trid, value):
		if self.dialect >= 13:
			self.writer.write(Err.CommandDisabled, trid)
			return
		# "Alert me when other people add me ..." Y/N
		#>>> GTC 152 N
		self._setting_change('GTC', trid, value)
	
	def _l_blp(self, trid, value):
		# Check "Only people on my Allow List ..." AL/BL
		#>>> BLP 143 BL
		self._setting_change('BLP', trid, value)
		self.nb.sync_contact_statuses()
		self.nb.generic_notify(self)
	
	def _l_chg(self, trid, sts_name, capabilities = None, msnobj = None):
		#>>> CHG 120 BSY 1073791020 <msnobj .../>
		capabilities = capabilities or 0
		user = self.user
		user.status.substatus = getattr(Substatus, sts_name)
		user.detail.capabilities = capabilities
		user.detail.msnobj = msnobj
		self.nb.mark_modified(user)
		self.nb.sync_contact_statuses()
		self.nb.generic_notify(self)
		self.writer.write('CHG', trid, sts_name, capabilities, msnobj)
		self._send_iln(trid)
	
	def _l_rea(self, trid, email, name):
		if self.dialect >= 10:
			self.writer.write(Err.CommandDisabled, trid)
			return
		if email == self.user.email:
			self._change_display_name(name)
		self.writer.write('REA', trid, self._ser(), email, name)
	
	def _l_snd(self, trid, email):
		# Send email about how to use MSN. Ignore it for now.
		self.writer.write('SND', trid, email)
	
	def _l_prp(self, trid, key, value):
		#>>> PRP 115 MFN ~~woot~~
		if key == 'MFN':
			self._change_display_name(value)
		# TODO: Save other settings?
		self.writer.write('PRP', trid, key, value)
	
	def _change_display_name(self, name):
		user = self.user
		user.status.name = name
		self.nb.mark_modified(user)
		self.nb.sync_contact_statuses()
		self.nb.generic_notify(self)
	
	def _l_sbp(self, trid, uuid, key, value):
		#>>> SBP 153 00000000-0000-0000-0002-000000000002 MFN Bob%201%20New
		# Can be ignored: controller handles syncing contact names.
		self.writer.write('SBP', trid, uuid, key, value)
	
	def _l_xfr(self, trid, dest):
		if dest != 'SB':
			self.writer.write(Err.InvalidParameter, trid)
			return
		token = self.nb._auth_service.create_token('sb/xfr', { 'uuid': self.user.uuid, 'dialect': self.dialect })
		sb = self.nb.get_sbservice()
		extra = ()
		if self.dialect >= 13:
			extra = ('U', 'messenger.msn.com')
		if self.dialect >= 14:
			extra += (1,)
		self.writer.write('XFR', trid, dest, '{}:{}'.format(sb.host, sb.port), 'CKI', token, *extra)
	
	def _l_adl(self, trid, data):
		# TODO
		self.writer.write('ADL', trid, 'OK')
	
	def _l_rml(self, trid, data):
		# TODO
		self.writer.write('RML', trid, 'OK')
	
	def _l_fqy(self, trid, data):
		# TODO
		self.writer.write('FQY', trid, b'')
	
	def _l_uun(self, trid, email, arg0, data):
		# TODO
		self.writer.write('UUN', trid, 'OK')
	
	# Utils
	
	def _setting_change(self, name, trid, value):
		user = self.user
		user.detail.settings[name] = value
		self.nb.mark_modified(user)
		self.writer.write(name, trid, self._ser(), value)
	
	def _send_iln(self, trid):
		if self.iln_sent: return
		user = self.user
		for ctc in user.detail.contacts.values():
			self._send_presence_notif(trid, ctc)
		self.iln_sent = True
	
	def _send_presence_notif(self, trid, ctc):
		status = ctc.status
		is_offlineish = status.is_offlineish()
		if is_offlineish and trid is not None: return
		head = ctc.head
		
		if self.dialect >= 14:
			networkid = 1
		else:
			networkid = None
		
		if is_offlineish:
			self.writer.write('FLN', head.email, networkid)
		else:
			if trid: frst = ('ILN', trid)
			else: frst = ('NLN',)
			rst = []
			if self.dialect >= 8:
				rst.append(head.detail.capabilities)
			if self.dialect >= 9:
				rst.append(head.detail.msnobj or '<msnobj/>')
			self.writer.write(*frst, status.substatus.name, head.email, networkid, status.name, *rst)
			
			if self.dialect >= 11:
				self.writer.write('UBX', head.email, networkid, { 'PSM': status.message, 'CurrentMedia': status.media })
	
	def _ser(self):
		if self.dialect >= 10:
			return None
		self.syn_ser += 1
		return self.syn_ser

	def _memberid(self, email):
		email = email.lower()
		x = 0
		i = 0
		while i < len(email):
			x = (x * 101 + ord(email[i])) % 4294967296
			i += 1
		return x

	def _splituuid(self, uuid):
		uuid = UUID(uuid)
		high = str(uuid.time_low % (1<<32))
		low = str(uuid.node % (1<<32))
		return (high, low)

SHIELDS = '''<?xml version="1.0" encoding="utf-8" ?>
<config>
	<shield><cli maj="7" min="0" minbld="0" maxbld="9999" deny=" " /></shield>
	<block></block>
</config>'''.encode('utf-8')
TIMESTAMP = '2000-01-01T00:00:00.0-00:00'
