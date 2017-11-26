from util.misc import gen_uuid

class Switchboard:
	def __init__(self, user_service, auth_service):
		self._user_service = user_service
		self._auth_service = auth_service
		
		# Dict[chatid, Chat]
		self._chats = {}
	
	def on_connection_lost(self, sess):
		chat = sess.state.chat
		if chat:
			chat.on_leave(sess)
	
	def login_xfr(self, sess, email, token):
		user, extra_data = self._load_user('sb/xfr', token)
		if user is None: return None
		if user.email != email: return None
		sess.user = user
		chat = Chat()
		self._chats[chat.id] = chat
		chat.add_session(sess)
		return chat, extra_data
	
	def auth_cal(self, uuid):
		return self._auth_service.create_token('sb/cal', uuid)
	
	def login_cal(self, sess, email, token, chatid):
		user, extra_data = self._load_user('sb/cal', token)
		if user is None: return None
		if user.email != email: return None
		sess.user = user
		chat = self._chats.get(chatid)
		if chat is None: return None
		for sc, _ in chat.get_roster(self):
			sc.send_event(ChatParticipantJoined(user))
		chat.add_session(sess)
		return chat, extra_data
		
	def _load_user(self, purpose, token):
		data = self._auth_service.pop_token(purpose, token)
		return self._user_service.get(data['uuid']), data['extra_data']

class Chat:
	def __init__(self):
		self.id = gen_uuid()
		# Dict[Session, User]
		self._users_by_sess = {}
	
	def add_session(self, sess):
		self._users_by_sess[sess] = sess.user
	
	def send_message_to_everyone(self, sess_sender, data):
		su_sender = self._users_by_sess[sess_sender]
		for sess in self._users_by_sess.keys():
			if sess == sess_sender: continue
			sess.send_event(ChatMessage(su_sender, data))
	
	def get_roster(self, sess):
		roster = []
		for sess1, su1 in self._users_by_sess.items():
			if sess1 == sess: continue
			roster.append((sess1, su1))
		return roster
	
	def on_leave(self, sess):
		su = self._users_by_sess.pop(sess, None)
		if su is None: return
		# Notify others that `sess` has left
		for sess1, su1 in self._users_by_sess.items():
			if sess1 == sess: continue
			sess1.send_event(ChatParticipantLeft(su))
