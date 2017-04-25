import asyncio

from db import Session, User, Auth
from util.misc import gen_uuid
from msnp import Logger, MSNPWriter, MSNPReader, decode_email

class SB:
	def __init__(self):
		# Dict[sessid, SBSession]
		self._sessions = {}
	
	def login_usr(self, sc, token):
		sbuser = _load_sbuser(token)
		if sbuser is None: return None
		sbsess = SBSession()
		self._sessions[sbsess.id] = sbsess
		sbsess.add_sc(sc, sbuser)
		return sbuser, sbsess
	
	def login_ans(self, sc, email, token, sessid):
		sbuser = _load_sbuser(token)
		if sbuser is None: return None
		if sbuser.email != email: return None
		sbsess = self._sessions.get(sessid)
		if sbsess is None: return None
		sbsess.add_sc(sc, sbuser)
		return sbuser, sbsess

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

def _load_sbuser(token):
	with Session() as sess:
		email = Auth.PopToken(token)
		if email is None: return None
		user = sess.query(User).filter(User.email == email).one_or_none()
		if user is None: return None
		return SBUser(user)

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
		self.reader.data_received(data)
		with self.writer:
			for m in self.reader:
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
		data = self.sb.login_usr(self, token)
		if data is None:
			self.writer.error(911, trid)
			return
		(sbuser, sbsess) = data
		self.state = SBConn.STATE_LIVE
		self.sbsess = sbsess
		self.sbuser = sbuser
		self.writer.write('USR', trid, 'OK', sbuser.email, sbuser.name)
	
	def _a_ans(self, trid, email, token, sessid):
		#>>> ANS trid email@example.com token sessionid
		(email, _) = decode_email(email)
		data = self.sb.login_ans(self, email, token, sessid)
		if data is None:
			self.writer.error(911, trid)
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
		err = self.nb.sb_call(self.sbuser.uuid, email, self.sbsess)
		if err is None:
			self.writer.write('CAL', trid, 'RINGING', self.sbsess.id)
		else:
			self.writer.write(err, trid)
	
	def _l_msg(self, trid, ack, data):
		#>>> MSG trid [UNAD] len
		data = _remove_pw_from_msg(data)
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

def _remove_pw_from_msg(data):
	i = data.find(b'\r\nTypingUser:')
	if i < 0: return data
	i += 14
	j = data.find(b'\r\n', i)
	if j < 0: j = len(data)
	s = data[i:j]
	l = s.find(b'|')
	if l < 0: return data
	r = s.rfind(b'@')
	if r <= l: return data
	return data[:i+l] + data[i+r:]
