import asyncio
from collections import defaultdict
from enum import Enum, IntFlag
from contextlib import contextmanager

from db import Session, User, Auth
from util.hash import hasher
from msnp import Logger, MSNPWriter, MSNPReader, decode_email

import settings

class NB:
	def __init__(self, loop, sbservices):
		self.loop = loop
		self.sbservices = sbservices
		# Dict[User.uuid, NBUser]
		self.records = {}
		# Dict[NBConn, NBUser]
		self._records_by_nc = {}
		# Dict[NBUser, Set[NBConn]]
		self._nc_from_nu = defaultdict(set)
		# Dict[NBUser, UserDetail]
		self._unsynced_db = {}
		
		self.loop.create_task(_sync_db(self._unsynced_db))
	
	def login(self, nc, token, email = None):
		with Session() as sess:
			if not settings.DEV_ACCEPT_ALL_LOGIN_TOKENS:
				email = Auth.PopToken(token)
			if email is None: return None
			uuid = _get_user_uuid(email)
			if uuid is None: return None
			nu = self._load_user_record(uuid)
		self._records_by_nc[nc] = nu
		self._nc_from_nu[nu].add(nc)
		self._load_detail(nu)
		return nu
	
	def do_auth_mock_md5(self, email, pw):
		if not (email and pw): return None
		with Session() as sess:
			user = sess.query(User).filter(User.email == email).one_or_none()
			if user is None: return None
			if not hasher.verify(pw, user.password): return None
		return Auth.CreateToken(email)
	
	def _generic_notify(self, nc):
		nu = self._records_by_nc.get(nc)
		if nu is None: return
		# Notify relevant `NBConn`s of status, name, message, media
		for nc1, nu1 in self._records_by_nc.items():
			if nc1 == nc: continue
			if nu1.detail is None: continue
			ctc = nu1.detail.contacts.get(nu.uuid)
			if ctc is None: continue
			nc1.notify_presence(ctc)
	
	def _sync_contact_statuses(self):
		for user_head in self.records.values():
			detail = user_head.detail
			if detail is None: continue
			for ctc in detail.contacts.values():
				_compute_visible_status(user_head, ctc)
	
	def _load_user_record(self, uuid):
		if uuid not in self.records:
			with Session() as sess:
				user = sess.query(User).filter(User.uuid == uuid).one_or_none()
				if user is None: return None
				user_head = NBUser(user)
			self.records[uuid] = user_head
		return self.records[uuid]
	
	def _load_detail(self, nu):
		if nu.detail: return
		with Session() as sess:
			user = sess.query(User).filter(User.uuid == nu.uuid).one()
			status = UserStatus(Substatus.FLN, user.name, user.message, None)
			detail = UserDetail(status, user.settings)
			for g in user.groups:
				grp = Group(**g)
				detail.groups[grp.id] = grp
			for c in user.contacts:
				ctc_head = self._load_user_record(c['uuid'])
				if ctc_head is None: continue
				status = UserStatus(Substatus.FLN, c['name'], c['message'], None)
				ctc = Contact(ctc_head, set(c['groups']), c['lists'], status)
				detail.contacts[ctc.head.uuid] = ctc
		nu.detail = detail
	
	# TODO: Hacky workaround for design defect
	@contextmanager
	def _hacky_scoped_detail(self, nu):
		needs_load = (nu.detail is None)
		if needs_load: self._load_detail(nu)
		try: yield
		finally:
			if needs_load: nu.detail = None

	def _mark_modified(self, user_head):
		self._unsynced_db[user_head] = user_head.detail
	
	def sb_auth(self, nu):
		return Auth.CreateToken(nu.email), self.sbservices[0]
	
	def sb_call(self, caller_uuid, callee_email, sbsess):
		caller = self.records.get(caller_uuid)
		if caller is None: return 500
		if caller.detail is None: return 500
		ctc = None
		for ctc in caller.detail.contacts.values():
			if ctc.head.email == callee_email: break
		if ctc.status.is_offlineish(): return 217
		ctc_ncs = self._nc_from_nu[ctc.head]
		if not ctc_ncs: return 217
		for ctc_nc in ctc_ncs:
			token = Auth.CreateToken(ctc.head.email)
			ctc_nc.notify_ring(sbsess, token, caller)
	
	def on_leave(self, nc):
		nu = self._records_by_nc.get(nc)
		if nu is None: return
		self._nc_from_nu[nu].discard(nc)
		if self._nc_from_nu[nu]:
			# There are still other people logged in as this user,
			# so don't send offline notifications.
			self._records_by_nc.pop(nc)
			return
		# User is offline, send notifications
		nu.detail = None
		self._sync_contact_statuses()
		self._generic_notify(nc)
		self._records_by_nc.pop(nc)
	
	def notify_reverse_add(self, nu1, nu2):
		# `nu2` was added to `nu1`'s RL
		if nu1 == nu2: return
		if nu1 not in self._nc_from_nu: return
		for nc in self._nc_from_nu[nu1]:
			nc.notify_add_rl(nu2)

async def _sync_db(unsynced):
	import traceback
	while True:
		await asyncio.sleep(1)
		if not unsynced: continue
		nus = list(unsynced.keys())[:100]
		with Session():
			for nu in nus:
				detail = unsynced[nu]
				try:
					_save_detail(nu, detail)
				except Exception:
					# TODO: Some exceptions should probably stop server
					traceback.print_exc()
				finally:
					# Remove from unsynced regardless of whether it got sync'd
					# or not, otherwise the list will grow unbounded and
					# sync for users past first 100 will never be attempted.
					del unsynced[nu]

class NBConn(asyncio.Protocol):
	STATE_QUIT = 'q'
	STATE_VERS = 'v'
	STATE_AUTH = 'a'
	STATE_SYNC = 's'
	STATE_LIVE = 'l'
	
	DIALECTS = ['MSNP{}'.format(d) for d in (12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2)]
	
	def __init__(self, nb):
		self.nb = nb
		self.logger = Logger('NB')
	
	def connection_made(self, transport):
		self.transport = transport
		self.logger.log_connect(transport)
		self.writer = MSNPWriter(self.logger, transport)
		self.reader = MSNPReader(self.logger)
		self.state = NBConn.STATE_VERS
		self.dialect = None
		self.iln_sent = False
		self.nbuser = None
		self.auth_token_md5 = None
		self.syn_ser = None
		self.usr_email = None
		self.usr_emailpw = None
	
	def connection_lost(self, exc):
		self.nb.on_leave(self)
		self.logger.log_disconnect()
	
	def data_received(self, data):
		with self.writer:
			for m in self.reader.data_received(data):
				cmd = m[0].lower()
				if cmd == 'out':
					self.state = NBConn.STATE_QUIT
					break
				handler = getattr(self, '_{}_{}'.format(self.state, cmd), None)
				if handler is None:
					self._unknown_cmd(m)
				else:
					handler(*m[1:])
				if self.state == NBConn.STATE_QUIT:
					break
		if self.state == NBConn.STATE_QUIT:
			self.transport.close()
	
	# Hooks for NB
	
	def notify_ring(self, sbsess, token, nu_ringer):
		sb = self.nb.sbservices[0]
		self.writer.write('RNG', sbsess.id, '{}:{}'.format(sb.host, sb.port), 'CKI', token, nu_ringer.email, nu_ringer.detail.status.name)
	
	def notify_presence(self, ctc):
		self._send_presence_notif(None, ctc)
	
	def notify_add_rl(self, nu):
		name = (nu.detail and nu.detail.status.name) or nu.email
		if self.dialect < 10:
			self.writer.write('ADD', 0, Lst.RL.name, nu.email, name)
		else:
			self.writer.write('ADC', 0, Lst.RL.name, 'N={}'.format(nu.email), 'F={}'.format(name))
	
	# State = Version
	
	def _v_ver(self, trid, *args):
		dialects = [a.upper() for a in args]
		d = None
		for d in NBConn.DIALECTS:
			if d in dialects: break
		if d not in dialects:
			self.writer.write('VER', trid, 0, *NBConn.DIALECTS)
			return
		self.writer.write('VER', trid, d)
		self.dialect = int(d[4:])
		self.state = NBConn.STATE_AUTH
	
	# State = Auth
	
	def _a_inf(self, trid):
		if self.dialect < 8:
			self.writer.write('INF', trid, 'MD5')
		else:
			self.writer.write(502, trid)
	
	def _a_usr(self, trid, authtype, stage, *args):
		if authtype == 'TWN':
			if stage == 'I':
				#>>> USR trid TWN I email|password@example.com
				self.usr_emailpw = args[0]
				(email, pw) = self._decode_email(email_pw)
				self.usr_email = email
				if pw is None:
					self.writer.write('USR', trid, authtype, 'S', 'Unused_USR_I')
				else:
					self.auth_token_md5 = self.nb.do_auth_mock_md5(email, pw)
					if self.auth_token_md5 is None:
						self.writer.write(911, trid)
						return
					self.nbuser = self.nb.login(self, self.auth_token_md5, email)
					if self.nbuser is None:
						self.writer.write(911, trid)
						return
					self.writer.write('USR', trid, 'OK', self.usr_emailpw, self.nbuser.detail.status.name)
					self.state = NBConn.STATE_SYNC
			elif stage == 'S':
				#>>> USR trid TWN S auth_token
				self.nbuser = self.nb.login(self, args[0], self.usr_email)
				if self.nbuser is None:
					self.writer.write(911, trid)
					return
				#verified = self.nbuser.verified
				verified = True
				if self.dialect < 10:
					self.writer.write('USR', trid, 'OK', self.nbuser.email, self.nbuser.detail.status.name, (1 if verified else 0), 0)
				else:
					self.writer.write('USR', trid, 'OK', self.nbuser.email, (1 if verified else 0), 0)
				self.state = NBConn.STATE_SYNC
			else:
				self.writer.write(911, trid)
		elif authtype == 'MD5':
			if stage == 'I':
				#>>> USR trid MD5 I email|password@example.com
				self.usr_emailpw = args[0]
				(email, pw) = self._decode_email(self.usr_emailpw)
				self.usr_email = email
				self.auth_token_md5 = self.nb.do_auth_mock_md5(email, pw)
				if self.auth_token_md5 is None:
					self.writer.write(911, trid)
					return
				self.writer.write('USR', trid, authtype, 'S', self.auth_token_md5)
			elif stage == 'S':
				#>>> USR trid MD5 S response
				# `response` is ignored; auth done in do_auth_mock_md5.
				self.nbuser = self.nb.login(self, self.auth_token_md5, self.usr_email)
				if self.nbuser is None:
					self.writer.write(911, trid)
					return
				self.writer.write('USR', trid, 'OK', self.usr_emailpw, self.nbuser.detail.status.name)
				self.state = NBConn.STATE_SYNC
			else:
				self.writer.write(911, trid)
		else:
			self.writer.write(911, trid)
	
	# State = Sync
	
	def _s_syn(self, trid, *extra):
		writer = self.writer
		nu = self.nbuser
		detail = nu.detail
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
			writer.write('PRP', 'MFN', detail.status.name)
			
			for g in groups.values():
				writer.write('LSG', g.name, g.id)
			for c in contacts.values():
				writer.write('LST', 'N={}'.format(c.head.email), 'F={}'.format(c.status.name), 'C={}'.format(c.head.uuid),
					c.lists, ','.join(c.groups)
				)
		self.state = NBConn.STATE_LIVE
	
	# State = Live
	
	def _l_gcf(self, trid, filename):
		self.writer.write('GCF', trid, filename, SHIELDS)
	
	def _l_png(self):
		self.writer.write('QNG', (60 if self.dialect >= 9 else None))
	
	def _l_uux(self, trid, data):
		nu = self.nbuser
		nu.detail.status.message = data['PSM']
		nu.detail.status.media = data['CurrentMedia']
		self.nb._mark_modified(nu)
		self.nb._sync_contact_statuses()
		self.nb._generic_notify(self)
		self.writer.write('UUX', trid, 0)
	
	def _l_url(self, trid, *ignored):
		self.writer.write('URL', trid, '/unused1', '/unused2', 1)
	
	def _l_adg(self, trid, name, ignored = None):
		#>>> ADG 276 New Group
		if len(name) > 61:
			self.writer.write(229, trid)
			return
		nu = self.nbuser
		id = _gen_group_id(nu.detail)
		nu.detail.groups[id] = Group(id, name)
		self.nb._mark_modified(nu)
		self.writer.write('ADG', trid, self._ser(), name, id, 0)
	
	def _l_rmg(self, trid, id):
		#>>> RMG 250 00000000-0000-0000-0001-000000000001
		if id == '0':
			self.writer.write(230, trid)
			return
		nu = self.nbuser
		groups = nu.detail.groups
		if id == 'New%20Group':
			# Bug: MSN 7.0 sends name instead of id in a particular scenario
			for g in groups.values():
				if g.name != 'New Group': continue
				id = g.id
				break
		try:
			del groups[id]
		except KeyError:
			self.writer.write(224, trid)
		else:
			for ctc in nu.detail.contacts.values():
				ctc.groups.discard(id)
			self.nb._mark_modified(nu)
			if self.dialect < 10:
				self.writer.write('RMG', trid, self._ser(), id)
			else:
				self.writer.write('RMG', trid, 1, id)
	
	def _l_reg(self, trid, id, name, ignored = None):
		#>>> REG 275 00000000-0000-0000-0001-000000000001 newname
		nu = self.nbuser
		g = nu.detail.groups.get(id)
		if g is None:
			self.writer.write(224, trid)
			return
		if len(name) > 61:
			self.writer.write(229, trid)
			return
		g.name = name
		self.nb._mark_modified(nu)
		if self.dialect < 10:
			self.writer.write('REG', trid, self._ser(), id, name, 0)
		else:
			self.writer.write('REG', trid, 1, name, id, 0)
	
	def _l_adc(self, trid, lst_name, usr, arg2 = None):
		if usr.startswith('N='):
			#>>> ADC 249 BL N=bob1@hotmail.com
			#>>> ADC 278 AL N=foo@hotmail.com
			#>>> ADC 277 FL N=foo@hotmail.com F=foo@hotmail.com
			email = usr[2:]
			contact_uuid = _get_user_uuid(email)
			if contact_uuid is None:
				self.writer.write(205, trid)
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
		contact_uuid = _get_user_uuid(email)
		if contact_uuid is None:
			self.writer.write(205, trid)
			return
		self._add_to_list_bidi(trid, lst_name, contact_uuid, name, group_id)

	def _add_to_list_bidi(self, trid, lst_name, contact_uuid, name = None, group_id = None):
		ctc_head = self.nb._load_user_record(contact_uuid)
		assert ctc_head is not None
		lst = getattr(Lst, lst_name)
		
		nu = self.nbuser
		ctc = self._add_to_list(nu, ctc_head, lst, name)
		if lst == Lst.FL:
			if group_id is not None and group_id != '0':
				if group_id not in nu.detail.groups:
					self.writer.write(224, trid)
					return
				if group_id in ctc.groups:
					self.writer.write(215, trid)
					return
				ctc.groups.add(group_id)
				self.nb._mark_modified(nu)
			# FL needs a matching RL on the contact
			with self.nb._hacky_scoped_detail(ctc_head):
				self._add_to_list(ctc_head, nu, Lst.RL, nu.detail.status.name)
			self.nb.notify_reverse_add(ctc_head, nu)
		
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
		nu = self.nbuser
		if lst_name == 'FL':
			if self.dialect < 10:
				contact_uuid = _get_user_uuid(usr)
			else:
				contact_uuid = usr
			ctc = nu.detail.contacts.get(contact_uuid)
			if ctc is None:
				self.writer.write(216, trid)
				return
			
			if group_id is None:
				#>>> ['REM', '279', 'FL', '00000000-0000-0000-0002-000000000001']
				# Remove from FL
				self._remove_from_list(nu, ctc.head, Lst.FL)
				# Remove matching RL
				with self.nb._hacky_scoped_detail(ctc.head):
					self._remove_from_list(ctc.head, nu, Lst.RL)
			else:
				#>>> ['REM', '247', 'FL', '00000000-0000-0000-0002-000000000002', '00000000-0000-0000-0001-000000000002']
				# Only remove group
				if group_id not in nu.detail.groups and group_id != '0':
					self.writer.write(224, trid)
					return
				try:
					ctc.groups.remove(group_id)
				except KeyError:
					if group_id == '0':
						self.writer.write(225, trid)
						return
				self.nb._mark_modified(nu)
		else:
			#>>> ['REM', '248', 'AL', 'bob1@hotmail.com']
			email = usr
			lb = getattr(Lst, lst_name)
			found = False
			for ctc in nu.detail.contacts.values():
				if ctc.head.email != email: continue
				ctc.lists &= ~lb
				found = True
			if not found:
				self.writer.write(216, trid)
				return
			self.nb._mark_modified(nu)
		self.writer.write('REM', trid, lst_name, self._ser(), usr, group_id)
	
	def _l_gtc(self, trid, value):
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
		nu = self.nbuser
		nu.detail.status.substatus = getattr(Substatus, sts_name)
		nu.detail.capabilities = capabilities
		nu.detail.msnobj = msnobj
		self.nb._mark_modified(nu)
		self.nb._sync_contact_statuses()
		self.nb._generic_notify(self)
		self.writer.write('CHG', trid, sts_name, capabilities, msnobj)
		self._send_iln(trid)
	
	def _l_rea(self, trid, email_pw, name):
		if self.dialect >= 10:
			self.writer.write(502, trid)
			return
		(email, _) = decode_email(email_pw)
		if email == self.nbuser.email:
			self._change_display_name(name)
		self.writer.write('REA', trid, self._ser(), email_pw, name)
	
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
		nu = self.nbuser
		nu.detail.status.name = name
		self.nb._mark_modified(nu)
		self.nb._sync_contact_statuses()
		self.nb._generic_notify(self)
	
	def _l_sbp(self, trid, uuid, key, value):
		#>>> ['SBP', '153', '00000000-0000-0000-0002-000000000002', 'MFN', 'Bob 1 New']
		# Can be ignored: controller handles syncing contact names.
		self.writer.write('SBP', trid, uuid, key, value)
	
	def _l_xfr(self, trid, dest):
		assert dest == 'SB'
		token, sb = self.nb.sb_auth(self.nbuser)
		self.writer.write('XFR', trid, dest, '{}:{}'.format(sb.host, sb.port), 'CKI', token)
	
	# Utils
	
	def _setting_change(self, name, trid, value):
		nu = self.nbuser
		nu.detail.settings[name] = value
		self._mark_modified(nu)
		self.writer.write(name, trid, self._ser(), value)
	
	def _send_iln(self, trid):
		if self.iln_sent: return
		nu = self.nbuser
		for ctc in nu.detail.contacts.values():
			self._send_presence_notif(trid, ctc)
		self.iln_sent = True
	
	def _send_iln_add(self, trid, ctc_uuid):
		nu = self.nbuser
		ctc = nu.detail.contacts.get(ctc_uuid)
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
	
	def _add_to_list(self, nu, ctc_head, lst, name):
		# Add `ctc_head` to `nu`'s `lst`
		contacts = nu.detail.contacts
		if ctc_head.uuid not in contacts:
			contacts[ctc_head.uuid] = Contact(ctc_head, set(), 0, UserStatus(Substatus.FLN, name, None, None))
		ctc = contacts.get(ctc_head.uuid)
		if ctc.status.name is None:
			ctc.status.name = name
		ctc.lists |= lst
		self.nb._mark_modified(nu)
		return ctc
	
	def _remove_from_list(self, nu, ctc_head, lst):
		# Remove `ctc_head` from `nu`'s `lst`
		contacts = nu.detail.contacts
		ctc = contacts.get(ctc_head.uuid)
		if ctc is None: return
		ctc.lists &= ~lst
		if not ctc.lists:
			del contacts[ctc_head.uuid]
		self.nb._mark_modified(nu)
	
	def _unknown_cmd(self, m):
		cmd = m[0]
		if cmd == 'CVR':
			v = m[7]
			self.writer.write(cmd, m[1], v, v, v, 'http://url.com', 'http://url.com')
			return
		self.logger.info("unknown (state = {}): {}".format(self.state, m))
	
	def _ser(self):
		# TODO: Find in which dialect SER# aren't used anymore
		if self.dialect >= 10:
			return None
		self.syn_ser += 1
		return self.syn_ser
	
	def _decode_email(self, email_pw):
		if self.dialect >= 8:
			return (email_pw, None)
		return decode_email(email_pw)

def _get_user_uuid(email):
	with Session() as sess:
		user = sess.query(User).filter(User.email == email).one_or_none()
		return user and user.uuid

def _save_detail(nu, detail):
	with Session() as sess:
		user = sess.query(User).filter(User.uuid == nu.uuid).one()
		user.name = detail.status.name
		user.message = detail.status.message
		user.settings = detail.settings
		user.groups = [{ 'id': g.id, 'name': g.name } for g in detail.groups.values()]
		user.contacts = [{
			'uuid': c.head.uuid, 'name': c.status.name, 'message': c.status.message,
			'lists': c.lists, 'groups': list(c.groups),
		} for c in detail.contacts.values()]
		sess.add(user)

def _compute_visible_status(nu, ctc):
	# Set Contact.status based on BLP and Contact.lists
	# If not blocked, Contact.status == Contact.head.detail.status
	ctc_status = (ctc.head.detail and ctc.head.detail.status)
	if ctc_status is None or _is_blocking(ctc.head, nu):
		ctc.status.substatus = Substatus.FLN
	else:
		ctc.status.substatus = ctc_status.substatus
		ctc.status.name = ctc_status.name
		ctc.status.message = ctc_status.message
		ctc.status.media = ctc_status.media

def _is_blocking(blocker, blockee):
	detail = blocker.detail
	contact = detail.contacts.get(blockee.uuid)
	lists = (contact and contact.lists or 0)
	if lists & Lst.BL: return True
	if lists & Lst.AL: return False
	return (detail.settings.get('BLP', 'AL') == 'BL')

class NBUser:
	def __init__(self, user):
		self.uuid = user.uuid
		self.email = user.email
		self.verified = user.verified
		self.detail = None

class Contact:
	def __init__(self, head, groups, lists, status):
		self.head = head
		self.groups = groups
		self.lists = lists
		self.status = status

class UserStatus:
	__slots__ = ('substatus', 'name', 'message', 'media')
	def __init__(self, substatus, name, message, media):
		self.substatus = substatus
		self.name = name
		self.message = message
		self.media = media
	
	def is_offlineish(self):
		ss = self.substatus
		return ss == Substatus.FLN or ss == Substatus.HDN

class UserDetail:
	def __init__(self, status, settings):
		self.status = status
		self.settings = settings
		self.groups = {}
		self.contacts = {}
		self.capabilities = 0
		self.msnobj = None

def _gen_group_id(detail):
	id = 1
	s = str(id)
	while s in detail.groups:
		id += 1
		s = str(id)
	return s

class Group:
	def __init__(self, id, name):
		self.id = id
		self.name = name

class Substatus(Enum):
	FLN = object()
	NLN = object()
	BSY = object()
	IDL = object()
	BRB = object()
	AWY = object()
	PHN = object()
	LUN = object()
	HDN = object()

class Lst(IntFlag):
	FL = 0x01
	AL = 0x02
	BL = 0x04
	RL = 0x08
	PL = 0x10

SHIELDS = '''<?xml version="1.0" encoding="utf-8" ?>
<config>
	<shield><cli maj="7" min="0" minbld="0" maxbld="9999" deny=" " /></shield>
	<block></block>
</config>'''.encode('utf-8')
TIMESTAMP = '2000-01-01T00:00:00.0-00:00'
