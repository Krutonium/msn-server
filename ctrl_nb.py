import asyncio
from collections import defaultdict

from util.msnp import Err
from models import Contact, Group, UserStatus, Substatus, Lst

class NB:
	def __init__(self, user_service, auth_service, sbservices):
		self._user = user_service
		self._auth = auth_service
		self._sbservices = sbservices
		# Dict[User.uuid, User]
		self._user_by_uuid = {}
		# Dict[NBConn, User]
		self._user_by_nc = {}
		# Dict[User, Set[NBConn]]
		self._ncs_by_user = defaultdict(set)
		# Dict[User, UserDetail]
		self._unsynced_db = {}
		
		# TODO: NB isn't guaranteed to be run in a loop (e.g. testing).
		# Need to figure out a better way to do this.
		asyncio.get_event_loop().create_task(self._sync_db())
	
	def login(self, nc, email, token):
		uuid = self._auth.pop_token('nb/login', token)
		return self._login_common(nc, uuid, email)
	
	def login_md5(self, nc, email, md5_hash):
		uuid = self._user.login_md5(email, md5_hash)
		return self._login_common(nc, uuid, email)
	
	def _login_common(self, nc, uuid, email):
		if uuid is None: return None
		self._user.update_date_login(uuid)
		user = self._load_user_record(uuid)
		self._user_by_nc[nc] = user
		self._ncs_by_user[user].add(nc)
		user.detail = self._load_detail(user)
		return user
	
	def _generic_notify(self, nc):
		user = self._user_by_nc.get(nc)
		if user is None: return
		# Notify relevant `NBConn`s of status, name, message, media
		for nc1, user1 in self._user_by_nc.items():
			if nc1 == nc: continue
			if user1.detail is None: continue
			ctc = user1.detail.contacts.get(user.uuid)
			if ctc is None: continue
			nc1.notify_presence(ctc)
	
	def _sync_contact_statuses(self):
		for user in self._user_by_uuid.values():
			detail = user.detail
			if detail is None: continue
			for ctc in detail.contacts.values():
				ctc.compute_visible_status(user)
	
	def _load_user_record(self, uuid):
		if uuid not in self._user_by_uuid:
			user = self._user.get(uuid)
			if user is None: return None
			self._user_by_uuid[uuid] = user
		return self._user_by_uuid[uuid]
	
	def _load_detail(self, user):
		if user.detail: return user.detail
		return self._user.get_detail(user.uuid)
	
	def _mark_modified(self, user, *, detail = None):
		ud = user.detail or detail
		if detail: assert ud is detail
		self._unsynced_db[user] = ud
	
	def notify_call(self, caller_uuid, callee_email, sbsess_id):
		caller = self._user_by_uuid.get(caller_uuid)
		if caller is None: return Err.InternalServerError
		if caller.detail is None: return Err.InternalServerError
		callee_uuid = self._user.get_uuid(callee_email)
		if callee_uuid is None: return Err.InternalServerError
		ctc = caller.detail.contacts.get(callee_uuid)
		if ctc is None: return Err.InternalServerError
		if ctc.status.is_offlineish(): return Err.PrincipalNotOnline
		ctc_ncs = self._ncs_by_user[ctc.head]
		if not ctc_ncs: return Err.PrincipalNotOnline
		for ctc_nc in ctc_ncs:
			token = self._auth.create_token('sb/cal', ctc.head.uuid)
			ctc_nc.notify_ring(sbsess_id, token, caller)
	
	def get_sbservice(self):
		return self._sbservices[0]
	
	def on_leave(self, nc):
		user = self._user_by_nc.get(nc)
		if user is None: return
		self._ncs_by_user[user].discard(nc)
		if self._ncs_by_user[user]:
			# There are still other people logged in as this user,
			# so don't send offline notifications.
			self._user_by_nc.pop(nc)
			return
		# User is offline, send notifications
		user.detail = None
		self._sync_contact_statuses()
		self._generic_notify(nc)
		self._user_by_nc.pop(nc)
	
	def notify_reverse_add(self, user1, user2):
		# `user2` was added to `user1`'s RL
		if user1 == user2: return
		if user1 not in self._ncs_by_user: return
		for nc in self._ncs_by_user[user1]:
			nc.notify_add_rl(user2)
	
	async def _sync_db(self):
		unsynced = self._unsynced_db
		user_service = self._user
		
		while True:
			await asyncio.sleep(1)
			if not unsynced: continue
			user_service.save_batch([
				(user, unsynced.pop(user))
				for user in list(unsynced.keys())[:100]
			])

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
		self._user = nb._user
		self.state = NBConn.STATE_AUTH
		self.dialect = None
		self.iln_sent = False
		self.user = None
		self.syn_ser = None
		self.usr_email = None
	
	def connection_lost(self):
		self.nb.on_leave(self)
	
	# Hooks for NB
	
	def notify_ring(self, sbsess_id, token, caller):
		sb = self.nb.get_sbservice()
		self.writer.write('RNG', sbsess_id, '{}:{}'.format(sb.host, sb.port), 'CKI', token, caller.email, caller.status.name)
	
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
		self.writer.write('CVR', trid, v, v, v, 'http://url.com', 'http://url.com')
	
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
				salt = self._user.get_md5_salt(email)
				if salt is None:
					# Account is not enabled for login via MD5
					# TODO: Can we pass an informative message to user?
					self.writer.write(Err.AuthFail, trid)
					return
				self.usr_email = email
				self.writer.write('USR', trid, authtype, 'S', salt)
				return
			if stage == 'S':
				self.user = self.nb.login_md5(self, self.usr_email, args[0])
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
					token = ('MBI_KEY_OLD', 'Unused_USR_I_SSO')
				self.writer.write('USR', trid, authtype, 'S', *token)
				#if self.dialect >= 13:
				#	self.writer.write('GCF', 0, None, SHIELDS)
				return
			if stage == 'S':
				#>>> USR trid TWN S auth_token
				#>>> USR trid SSO S auth_token b64_response
				self.user = self.nb.login(self, self.usr_email, args[0])
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
		
		if self.dialect < 13:
			return
		
		# Why won't you work!!!
		msg = '''MIME-Version: 1.0
Content-Type: text/x-msmsgsprofile; charset=UTF-8
MSPAuth: banana-mspauth-potato

'''
		self.writer.write('SBS', 0, 'null')
		self.writer.write('PRP', 'MFN', 'Test')
		self.writer.write('MSG', 'Hotmail', 'Hotmail', msg.replace('\n', '\r\n').encode('ascii'))
	
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
		self.nb._mark_modified(user)
		self.nb._sync_contact_statuses()
		self.nb._generic_notify(self)
		self.writer.write('UUX', trid, 0)
	
	def _l_url(self, trid, *ignored):
		self.writer.write('URL', trid, '/unused1', '/unused2', 1)
	
	def _l_adg(self, trid, name, ignored = None):
		#>>> ADG 276 New Group
		if len(name) > 61:
			self.writer.write(Err.GroupNameTooLong, trid)
			return
		user = self.user
		group_id = _gen_group_id(user.detail)
		user.detail.groups[group_id] = Group(group_id, name)
		self.nb._mark_modified(user)
		self.writer.write('ADG', trid, self._ser(), name, group_id, 0)
	
	def _l_rmg(self, trid, group_id):
		#>>> RMG 250 00000000-0000-0000-0001-000000000001
		if group_id == '0':
			self.writer.write(Err.GroupZeroUnremovable, trid)
			return
		user = self.user
		groups = user.detail.groups
		if group_id == 'New%20Group':
			# Bug: MSN 7.0 sends name instead of id in a particular scenario
			for g in groups.values():
				if g.name != 'New Group': continue
				group_id = g.id
				break
		try:
			del groups[group_id]
		except KeyError:
			self.writer.write(Err.GroupInvalid, trid)
		else:
			for ctc in user.detail.contacts.values():
				ctc.groups.discard(group_id)
			self.nb._mark_modified(user)
			if self.dialect < 10:
				self.writer.write('RMG', trid, self._ser(), group_id)
			else:
				self.writer.write('RMG', trid, 1, group_id)
	
	def _l_reg(self, trid, group_id, name, ignored = None):
		#>>> REG 275 00000000-0000-0000-0001-000000000001 newname
		user = self.user
		g = user.detail.groups.get(group_id)
		if g is None:
			self.writer.write(Err.GroupInvalid, trid)
			return
		if len(name) > 61:
			self.writer.write(Err.GroupNameTooLong, trid)
			return
		g.name = name
		self.nb._mark_modified(user)
		if self.dialect < 10:
			self.writer.write('REG', trid, self._ser(), group_id, name, 0)
		else:
			self.writer.write('REG', trid, 1, name, group_id, 0)
	
	def _l_adc(self, trid, lst_name, usr, arg2 = None):
		if usr.startswith('N='):
			#>>> ADC 249 BL N=bob1@hotmail.com
			#>>> ADC 278 AL N=foo@hotmail.com
			#>>> ADC 277 FL N=foo@hotmail.com F=foo@hotmail.com
			email = usr[2:]
			contact_uuid = self._user.get_uuid(email)
			if contact_uuid is None:
				self.writer.write(Err.InvalidPrincipal, trid)
				return
			group_id = None
			name = (arg2[2:] if arg2 else None)
		else:
			# Add C= to group
			#>>> ADC 246 FL C=00000000-0000-0000-0002-000000000002 00000000-0000-0000-0001-000000000003
			contact_uuid = usr[2:]
			group_id = arg2
			name = None
		self._add_to_list_bidi(trid, lst_name, contact_uuid, name, group_id)

	def _l_add(self, trid, lst_name, email, name = None, group_id = None):
		#>>> ADD 122 FL email name group
		contact_uuid = self._user.get_uuid(email)
		if contact_uuid is None:
			self.writer.write(Err.InvalidPrincipal, trid)
			return
		self._add_to_list_bidi(trid, lst_name, contact_uuid, name, group_id)

	def _add_to_list_bidi(self, trid, lst_name, contact_uuid, name = None, group_id = None):
		ctc_head = self.nb._load_user_record(contact_uuid)
		assert ctc_head is not None
		lst = getattr(Lst, lst_name)
		
		user = self.user
		ctc = self._add_to_list(user, ctc_head, lst, name)
		if lst == Lst.FL:
			if group_id is not None and group_id != '0':
				if group_id not in user.detail.groups:
					self.writer.write(Err.GroupInvalid, trid)
					return
				if group_id in ctc.groups:
					self.writer.write(Err.PrincipalOnList, trid)
					return
				ctc.groups.add(group_id)
				self.nb._mark_modified(user)
			# FL needs a matching RL on the contact
			self._add_to_list(ctc_head, user, Lst.RL, user.status.name)
			self.nb.notify_reverse_add(ctc_head, user)
		
		self.nb._sync_contact_statuses()
		self.nb._generic_notify(self)
		
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
		
		if lst == Lst.FL:
			self._send_iln_add(trid, ctc_head.uuid)
	
	def _l_rem(self, trid, lst_name, usr, group_id = None):
		if lst_name == 'RL':
			self.state = NBConn.STATE_QUIT
			return
		user = self.user
		if lst_name == 'FL':
			if self.dialect < 10:
				contact_uuid = self._user.get_uuid(usr)
			else:
				contact_uuid = usr
			ctc = user.detail.contacts.get(contact_uuid)
			if ctc is None:
				self.writer.write(Err.PrincipalNotOnList, trid)
				return
			
			if group_id is None:
				#>>> ['REM', '279', 'FL', '00000000-0000-0000-0002-000000000001']
				# Remove from FL
				self._remove_from_list(user, ctc.head, Lst.FL)
				# Remove matching RL
				self._remove_from_list(ctc.head, user, Lst.RL)
			else:
				#>>> ['REM', '247', 'FL', '00000000-0000-0000-0002-000000000002', '00000000-0000-0000-0001-000000000002']
				# Only remove group
				if group_id not in user.detail.groups and group_id != '0':
					self.writer.write(Err.GroupInvalid, trid)
					return
				try:
					ctc.groups.remove(group_id)
				except KeyError:
					if group_id == '0':
						self.writer.write(Err.PrincipalNotInGroup, trid)
						return
				self.nb._mark_modified(user)
		else:
			#>>> ['REM', '248', 'AL', 'bob1@hotmail.com']
			email = usr
			lb = getattr(Lst, lst_name)
			found = False
			for ctc in user.detail.contacts.values():
				if ctc.head.email != email: continue
				ctc.lists &= ~lb
				found = True
			if not found:
				self.writer.write(Err.PrincipalNotOnList, trid)
				return
			self.nb._mark_modified(user)
		self.writer.write('REM', trid, lst_name, self._ser(), usr, group_id)
	
	def _l_gtc(self, trid, value):
		if self.dialect >= 13:
			self.writer.write(Err.CommandDisabled, trid)
			return
		# "Alert me when other people add me ..." Y/N
		#>>> ['GTC', '152', 'N']
		self._setting_change('GTC', trid, value)
	
	def _l_blp(self, trid, value):
		# Check "Only people on my Allow List ..." AL/BL
		#>>> ['BLP', '143', 'BL']
		self._setting_change('BLP', trid, value)
		self.nb._sync_contact_statuses()
		self.nb._generic_notify(self)
	
	def _l_chg(self, trid, sts_name, capabilities = None, msnobj = None):
		#>>> ['CHG', '120', 'BSY', '1073791020', '<msnobj .../>']
		capabilities = capabilities or 0
		user = self.user
		user.status.substatus = getattr(Substatus, sts_name)
		user.detail.capabilities = capabilities
		user.detail.msnobj = msnobj
		self.nb._mark_modified(user)
		self.nb._sync_contact_statuses()
		self.nb._generic_notify(self)
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
		#>>> ['PRP', '115', 'MFN', '~~woot~~']
		if key == 'MFN':
			self._change_display_name(value)
		# TODO: Save other settings?
		self.writer.write('PRP', trid, key, value)
	
	def _change_display_name(self, name):
		user = self.user
		user.status.name = name
		self.nb._mark_modified(user)
		self.nb._sync_contact_statuses()
		self.nb._generic_notify(self)
	
	def _l_sbp(self, trid, uuid, key, value):
		#>>> ['SBP', '153', '00000000-0000-0000-0002-000000000002', 'MFN', 'Bob 1 New']
		# Can be ignored: controller handles syncing contact names.
		self.writer.write('SBP', trid, uuid, key, value)
	
	def _l_xfr(self, trid, dest):
		if dest != 'SB':
			self.writer.write(Err.InvalidParameter, trid)
			return
		token = self.nb._auth.create_token('sb/xfr', self.user.uuid)
		sb = self.nb.get_sbservice()
		self.writer.write('XFR', trid, dest, '{}:{}'.format(sb.host, sb.port), 'CKI', token)
	
	def _l_adl(self, trid, data):
		# TODO
		self.writer.write('ADL', trid, 'OK')
	
	def _l_rml(self, trid, data):
		# TODO
		self.writer.write('RML', trid, 'OK')
	
	def _l_fqy(self, trid, data):
		# TODO
		self.writer.write('FQY', trid, b'')
	
	def _l_uun(self, trid, email, arg0, arg1, data):
		# TODO
		self.writer.write('UUN', trid, 'OK')
	
	# Utils
	
	def _setting_change(self, name, trid, value):
		user = self.user
		user.detail.settings[name] = value
		self.nb._mark_modified(user)
		self.writer.write(name, trid, self._ser(), value)
	
	def _send_iln(self, trid):
		if self.iln_sent: return
		user = self.user
		for ctc in user.detail.contacts.values():
			self._send_presence_notif(trid, ctc)
		self.iln_sent = True
	
	def _send_iln_add(self, trid, ctc_uuid):
		user = self.user
		ctc = user.detail.contacts.get(ctc_uuid)
		if ctc is None: return
		self._send_presence_notif(trid, ctc)
	
	def _send_presence_notif(self, trid, ctc):
		status = ctc.status
		is_offlineish = status.is_offlineish()
		if is_offlineish and trid is not None: return
		head = ctc.head
		if is_offlineish:
			self.writer.write('FLN', head.email)
		else:
			if trid: frst = ('ILN', trid)
			else: frst = ('NLN',)
			rst = []
			if self.dialect >= 8:
				rst.append(head.detail.capabilities)
			if self.dialect >= 9:
				rst.append(head.detail.msnobj)
			else:
				rst.append(None)
			self.writer.write(*frst, status.substatus.name, head.email, status.name, *rst)
			if self.dialect >= 11:
				self.writer.write('UBX', head.email, { 'PSM': status.message, 'CurrentMedia': status.media })
	
	def _add_to_list(self, user, ctc_head, lst, name):
		# Add `ctc_head` to `user`'s `lst`
		detail = self.nb._load_detail(user)
		contacts = detail.contacts
		if ctc_head.uuid not in contacts:
			contacts[ctc_head.uuid] = Contact(ctc_head, set(), 0, UserStatus(name))
		ctc = contacts.get(ctc_head.uuid)
		if ctc.status.name is None:
			ctc.status.name = name
		ctc.lists |= lst
		self.nb._mark_modified(user, detail = detail)
		return ctc
	
	def _remove_from_list(self, user, ctc_head, lst):
		# Remove `ctc_head` from `user`'s `lst`
		detail = self.nb._load_detail(user)
		contacts = detail.contacts
		ctc = contacts.get(ctc_head.uuid)
		if ctc is None: return
		ctc.lists &= ~lst
		if not ctc.lists:
			del contacts[ctc_head.uuid]
		self.nb._mark_modified(user, detail = detail)
	
	def _ser(self):
		if self.dialect >= 10:
			return None
		self.syn_ser += 1
		return self.syn_ser

def _gen_group_id(detail):
	id = 1
	s = str(id)
	while s in detail.groups:
		id += 1
		s = str(id)
	return s

SHIELDS = '''<?xml version="1.0" encoding="utf-8" ?>
<config>
	<shield><cli maj="7" min="0" minbld="0" maxbld="9999" deny=" " /></shield>
	<block></block>
</config>'''.encode('utf-8')
TIMESTAMP = '2000-01-01T00:00:00.0-00:00'
