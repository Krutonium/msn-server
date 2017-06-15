import asyncio

from util.misc import gen_uuid, Logger
from util.msnp import MSNPWriter, MSNPReader, Err

class SB:
	def __init__(self, auth_service):
		# Dict[sessid, SBSession]
		self._sessions = {}
		self._auth = auth_service
	
	def login_xfr(self, sc, token):
		sbuser = self._load_sbuser('xfr', token)
		if sbuser is None: return None
		sbsess = SBSession()
		self._sessions[sbsess.id] = sbsess
		sbsess.add_sc(sc, sbuser)
		return sbuser, sbsess
	
	def auth_cal(self, email):
		return self._auth.create_token('cal', email)
	
	def login_cal(self, sc, email, token, sessid):
		sbuser = self._load_sbuser('cal', token)
		if sbuser is None: return None
		if sbuser.email != email: return None
		sbsess = self._sessions.get(sessid)
		if sbsess is None: return None
		sbsess.add_sc(sc, sbuser)
		return sbuser, sbsess
		
	def _load_sbuser(self, purpose, token):
		from db import Session, User
		with Session() as sess:
			email = self._auth.pop_token(purpose, token)
			if email is None: return None
			user = sess.query(User).filter(User.email == email).one_or_none()
			if user is None: return None
			return SBUser(user)

class SBSession:
	def __init__(self):
		self.id = gen_uuid()
		# Dict[SBConn, SBUser]
		self._users_by_sc = {}
	
	def add_sc(self, sc, sbuser):
		self._users_by_sc[sc] = sbuser
	
	def send_message(self, sc_sender, data):
		su_sender = self._users_by_sc[sc_sender]
		for sc in self._users_by_sc.keys():
			if sc == sc_sender: continue
			sc.send_message(su_sender, data)
	
	def get_roster(self, sc):
		roster = []
		for sc1, su1 in self._users_by_sc.items():
			if sc1 == sc: continue
			roster.append((sc1, su1))
		return roster
	
	def on_leave(self, sc):
		su = self._users_by_sc.pop(sc, None)
		if su is None: return
		# Notify others that `sc` has left
		for sc1, su1 in self._users_by_sc.items():
			if sc1 == sc: continue
			sc1.send_leave(su)

class SBUser:
	def __init__(self, user):
		self.uuid = user.uuid
		self.email = user.email
		self.name = user.name

class SBConn(asyncio.Protocol):
	STATE_QUIT = 'q'
	STATE_AUTH = 'a'
	STATE_LIVE = 'l'
	
	def __init__(self, sb, nb):
		self.sb = sb
		self.nb = nb
		self.logger = Logger('SB')
	
	def connection_made(self, transport):
		self.transport = transport
		self.logger.log_connect(transport)
		self.writer = MSNPWriter(self.logger, transport)
		self.reader = MSNPReader(self.logger)
		self.state = SBConn.STATE_AUTH
		self.sbsess = None
		self.sbuser = None
	
	def connection_lost(self, exc):
		if self.sbsess:
			self.sbsess.on_leave(self)
		self.logger.log_disconnect()
	
	def data_received(self, data):
		with self.writer:
			for m in self.reader.data_received(data):
				cmd = m[0].lower()
				if cmd == 'out':
					self.state = SBConn.STATE_QUIT
					break
				handler = getattr(self, '_{}_{}'.format(self.state, cmd), None)
				if handler is None:
					self._unknown_cmd(m)
				else:
					handler(*m[1:])
				if self.state == SBConn.STATE_QUIT:
					break
		if self.state == SBConn.STATE_QUIT:
			self.transport.close()
	
	# Hooks
	
	def send_message(self, sbuser, data):
		self.writer.write('MSG', sbuser.email, sbuser.name, data)
	
	def send_join(self, sbuser):
		self.writer.write('JOI', sbuser.email, sbuser.name)
	
	def send_leave(self, sbuser):
		self.writer.write('BYE', sbuser.email)
	
	# State = Auth
	
	def _a_usr(self, trid, email, token):
		#>>> USR trid email@example.com token
		data = self.sb.login_xfr(self, token)
		if data is None:
			self.writer.write(Err.AuthFail, trid)
			return
		(sbuser, sbsess) = data
		self.state = SBConn.STATE_LIVE
		self.sbsess = sbsess
		self.sbuser = sbuser
		self.writer.write('USR', trid, 'OK', sbuser.email, sbuser.name)
	
	def _a_ans(self, trid, email, token, sessid):
		#>>> ANS trid email@example.com token sessionid
		data = self.sb.login_cal(self, email, token, sessid)
		if data is None:
			self.writer.write(Err.AuthFail, trid)
			return
		(sbuser, sbsess) = data
		self.state = SBConn.STATE_LIVE
		self.sbsess = sbsess
		self.sbuser = sbuser
		roster = sbsess.get_roster(self)
		l = len(roster)
		for i, (sc, su) in enumerate(roster):
			sc.send_join(self.sbuser)
			self.writer.write('IRO', trid, i + 1, l, su.email, su.name)
		self.writer.write('ANS', trid, 'OK')
	
	# State = Live
	
	def _l_cal(self, trid, email):
		#>>> CAL trid email@example.com
		res = self.nb.get_contact_connections(self.sbuser.uuid, email)
		if isinstance(res, int):
			self.writer.write(res, trid)
			return
		
		ctc_ncs = res
		if not ctc_ncs:
			return Err.PrincipalNotOnline
		
		for ctc_nc in ctc_ncs:
			token = self.sb.auth_cal(email)
			ctc_nc.notify_ring(self.sbsess, token, self.sbuser.email, self.sbuser.name)
		
		self.writer.write('CAL', trid, 'RINGING', self.sbsess.id)
	
	def _l_msg(self, trid, ack, data):
		#>>> MSG trid [UNAD] len
		self.sbsess.send_message(self, data)
		if ack == 'U':
			return
		# TODO: Figure out if any recipient didn't receive
		any_failed = False
		if any_failed: # ADN
			self.writer.write('NAK', trid)
		elif ack != 'N': # AD
			self.writer.write('ACK', trid)
	
	# Utils
	
	def _unknown_cmd(self, m):
		self.logger.info("unknown (state = {}): {}".format(self.state, m))
