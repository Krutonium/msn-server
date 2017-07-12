from util.misc import gen_uuid
from util.msnp import Err

class SB:
	def __init__(self, user_service, auth_service):
		self._user = user_service
		self._auth = auth_service
		# Dict[sessid, SBSession]
		self._sessions = {}
	
	def login_xfr(self, sc, token):
		user, dialect = self._load_user('sb/xfr', token)
		if user is None: return None
		sbsess = SBSession()
		self._sessions[sbsess.id] = sbsess
		sbsess.add_sc(sc, user)
		return user, sbsess, dialect
	
	def auth_cal(self, uuid):
		return self._auth.create_token('sb/cal', uuid)
	
	def login_cal(self, sc, email, token, sessid):
		user, dialect = self._load_user('sb/cal', token)
		if user is None: return None
		if user.email != email: return None
		sbsess = self._sessions.get(sessid)
		if sbsess is None: return None
		sbsess.add_sc(sc, user)
		return user, sbsess, dialect
		
	def _load_user(self, purpose, token):
		data = self._auth.pop_token(purpose, token)
		return self._user.get(data['uuid']), data['dialect']

class SBSession:
	def __init__(self):
		self.id = gen_uuid()
		# Dict[SBConn, User]
		self._users_by_sc = {}
	
	def add_sc(self, sc, user):
		self._users_by_sc[sc] = user
	
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

class SBConn:
	STATE_QUIT = 'q'
	STATE_AUTH = 'a'
	STATE_LIVE = 'l'
	
	def __init__(self, sb, nb, writer):
		self.sb = sb
		self.nb = nb
		self.writer = writer
		self.state = SBConn.STATE_AUTH
		self.sbsess = None
		self.user = None
		self.dialect = None
	
	def connection_lost(self):
		if self.sbsess:
			self.sbsess.on_leave(self)
	
	# Hooks
	
	def send_message(self, user, data):
		self.writer.write('MSG', user.email, user.status.name, data)
	
	def send_join(self, user):
		extra = ()
		if self.dialect >= 13:
			extra = (user.detail.capabilities,)
		self.writer.write('JOI', user.email, user.status.name, *extra)
	
	def send_leave(self, user):
		self.writer.write('BYE', user.email)
	
	# State = Auth
	
	def _a_usr(self, trid, email, token):
		#>>> USR trid email@example.com token
		data = self.sb.login_xfr(self, token)
		if data is None:
			self.writer.write(Err.AuthFail, trid)
			return
		(user, sbsess, dialect) = data
		if email != user.email:
			self.writer.write(Err.AuthFail, trid)
			return
		self.state = SBConn.STATE_LIVE
		self.sbsess = sbsess
		self.user = user
		self.dialect = dialect
		self.writer.write('USR', trid, 'OK', user.email, user.status.name)
	
	def _a_ans(self, trid, email, token, sessid):
		#>>> ANS trid email@example.com token sessionid
		data = self.sb.login_cal(self, email, token, sessid)
		if data is None:
			self.writer.write(Err.AuthFail, trid)
			return
		(user, sbsess, dialect) = data
		self.state = SBConn.STATE_LIVE
		self.sbsess = sbsess
		self.user = user
		self.dialect = dialect
		roster = sbsess.get_roster(self)
		l = len(roster)
		for i, (sc, su) in enumerate(roster):
			sc.send_join(self.user)
			extra = ()
			if self.dialect >= 13:
				extra = (su.detail.capabilities,)
			self.writer.write('IRO', trid, i + 1, l, su.email, su.status.name, *extra)
		self.writer.write('ANS', trid, 'OK')
	
	# State = Live
	
	def _l_cal(self, trid, callee_email):
		#>>> CAL trid email@example.com
		err = self.nb.notify_call(self.user.uuid, callee_email, self.sbsess.id)
		if err:
			self.writer.write(err, trid)
		else:
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
