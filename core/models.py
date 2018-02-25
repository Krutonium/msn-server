from typing import Dict, Optional
from enum import Enum, IntFlag

class User:
	detail: Optional['UserDetail']
	
	def __init__(self, uuid, email, verified, status, date_created):
		self.uuid = uuid
		self.email = email
		self.verified = verified
		# `status`: true status of user
		self.status = status
		self.detail = None
		self.date_created = date_created

class UserYahoo:
	detail: Optional['UserYahooDetail']
	
	def __init__(self, uuid, email, yahoo_id, verified, status, date_created):
		self.uuid = uuid
		self.email = email
		self.yahoo_id = yahoo_id
		self.verified = verified
		# `status`: true status of user
		self.status = status
		self.detail = None
		self.date_created = date_created

class Contact:
	def __init__(self, user, groups, lists, status, *, is_messenger_user = None):
		self.head = user
		self.groups = groups
		self.lists = lists
		# `status`: status as known by the contact
		self.status = status
		self.is_messenger_user = _default_if_none(is_messenger_user, True)
	
	def compute_visible_status(self, to_user):
		# Set Contact.status based on BLP and Contact.lists
		# If not blocked, Contact.status == Contact.head.status
		if self.head.detail is None or _is_blocking(self.head, to_user):
			self.status.substatus = Substatus.FLN
			return
		true_status = self.head.status
		self.status.substatus = true_status.substatus
		self.status.name = true_status.name
		self.status.message = true_status.message
		self.status.media = true_status.media

class YahooContact:
	def __init__(self, user, yahoo_id, groups, status, *, is_messenger_user = None):
		self.head = user
		self.yahoo_id = yahoo_id
		self.groups = groups
		# `status`: status as known by the contact
		self.status = status
		self.is_messenger_user = _default_if_none(is_messenger_user, False)
	
	def compute_visible_status(self, to_user):
		true_status = self.head.status
		self.status.substatus = true_status.substatus
		self.status.message = true_status.message

def _is_blocking(blocker, blockee):
	detail = blocker.detail
	contact = detail.contacts.get(blockee.uuid)
	lists = (contact and contact.lists or 0)
	if lists & Lst.BL: return True
	if lists & Lst.AL: return False
	return (detail.settings.get('BLP', 'AL') == 'BL')

class UserStatus:
	__slots__ = ('substatus', 'name', 'message', 'media')
	
	def __init__(self, name, message = None):
		self.substatus = Substatus.FLN
		self.name = name
		self.message = message
		self.media = None
	
	def is_offlineish(self):
		ss = self.substatus
		return ss == Substatus.FLN or ss == Substatus.HDN

class UserYahooStatus:
	__slots__ = ('substatus', 'message')
	
	def __init__(self):
		# Use Offline as default
		self.substatus = YMSGStatus.Offline
		self.message = {'text': '', 'is_away_message': 0}
	
	def is_offlineish(self):
		ss = self.substatus
		return ss == YMSGStatus.Offline or ss == YMSGStatus.Invisible

class UserDetail:
	def __init__(self, settings):
		self.settings = settings
		self.groups = {} # type: Dict[str, Group]
		self.contacts = {} # type: Dict[str, Contact]

class UserYahooDetail:
	def __init__(self):
		self.groups = {} # type: Dict[str, Group]
		self.contacts = {} # type: Dict[str, YahooContact]

class Group:
	def __init__(self, id, name, *, is_favorite = None):
		self.id = id
		self.name = name
		self.is_favorite = _default_if_none(is_favorite, False)

def _default_if_none(x, default):
	if x is None: return default
	return x

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
	
	# TODO: This is ugly.
	def __init__(self, id):
		super().__init__()
		if id == 0x01:
			self.label = "Follow"
		elif id == 0x02:
			self.label = "Allow"
		elif id == 0x04:
			self.label = "Block"
		elif id == 0x08:
			self.label = "Reverse"
		else:
			self.label = "Pending"
	
	@classmethod
	def Parse(cls, label):
		if not hasattr(cls, '_MAP'):
			map = {}
			for lst in cls:
				map[lst.label.lower()] = lst
			cls._MAP = map
		return cls._MAP.get(label.lower())

class Service:
	def __init__(self, host, port):
		self.host = host
		self.port = port

# Yahoo stuff

class YMSGService:
	LogOn = 0x01
	LogOff = 0x02
	IsAway = 0x03
	IsBack = 0x04
	Message = 0x06
	UserStat = 0x0a
	ContactNew = 0x0f
	AddIgnore = 0x11
	PingConfiguration = 0x12
	ConfInvite = 0x18
	ConfLogon = 0x19
	ConfDecline = 0x1a
	ConfLogoff = 0x1b
	ConfAddInvite = 0x1c
	ConfMsg = 0x1d
	Notify = 0x4b
	Handshake = 0x4c
	P2PFileXfer = 0x4d
	AuthResp = 0x54
	List = 0x55
	Auth = 0x57
	FriendAdd = 0x83
	Ignore = 0x85
	Ping = 0x8a

class YMSGStatus:
	# Available/Client Request
	Available = 0x00000000
	WebLogin = 0x5a55aa55
	# BRB/Server Response
	BRB = 0x00000001
	Busy = 0x00000002
	# "Not at Home"/BadUsername
	NotAtHome = 0x00000003
	OnVacation = 0x00000007
	Invisible = 0x0000000c
	Bad = 0x0000000d
	Locked = 0x0000000e
	Typing = 0x00000016
	Custom = 0x00000063
	Offline = 0x5a55aa56
	LoginError = 0xffffffff