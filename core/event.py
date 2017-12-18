class OutgoingEvent:
	pass

class PresenceNotificationEvent(OutgoingEvent):
	def __init__(self, contact):
		self.contact = contact

class AddedToListEvent(OutgoingEvent):
	def __init__(self, lst, user):
		self.lst = lst
		self.user = user

class InvitedToChatEvent(OutgoingEvent):
	def __init__(self, chatid, token, caller):
		self.chatid = chatid
		self.token = token
		self.caller = caller

class ChatParticipantJoined(OutgoingEvent):
	def __init__(self, sess):
		self.sess = sess

class ChatParticipantLeft(OutgoingEvent):
	def __init__(self, user):
		self.user = user

class ChatMessage(OutgoingEvent):
	def __init__(self, user_sender, data):
		self.user_sender = user_sender
		self.data = data

class ReplyEvent(OutgoingEvent):
	def __init__(self, data):
		self.data = data

class POPBootEvent(OutgoingEvent):
	pass

class POPNotifyEvent(OutgoingEvent):
	pass

class CloseEvent(OutgoingEvent):
	pass
