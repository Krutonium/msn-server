from datetime import datetime
from typing import Dict, Optional, Set, Any, TypeVar
from enum import Enum, IntFlag

class User:
	__slots__ = ('uuid', 'email', 'verified', 'status', 'detail', 'date_created')
	
	uuid: str
	email: str
	verified: bool
	status: 'UserStatus'
	detail: Optional['UserDetail']
	date_created: datetime
	
	def __init__(self, uuid: str, email: str, verified: bool, status: 'UserStatus', date_created: datetime) -> None:
		self.uuid = uuid
		self.email = email
		self.verified = verified
		# `status`: true status of user
		self.status = status
		self.detail = None
		self.date_created = date_created

class Contact:
	__slots__ = ('head', 'groups', 'lists', 'status', 'is_messenger_user')
	
	head: User
	groups: Set[str]
	lists: 'Lst'
	status: 'UserStatus'
	is_messenger_user: bool
	
	def __init__(self, user: User, groups: Set[str], lists: 'Lst', status: 'UserStatus', *, is_messenger_user: Optional[bool] = None) -> None:
		self.head = user
		self.groups = groups
		self.lists = lists
		# `status`: status as known by the contact
		self.status = status
		self.is_messenger_user = _default_if_none(is_messenger_user, True)
	
	def compute_visible_status(self, to_user: User) -> None:
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

def _is_blocking(blocker: User, blockee: User) -> bool:
	detail = blocker.detail
	assert detail is not None
	contact = detail.contacts.get(blockee.uuid)
	lists = (contact and contact.lists or 0)
	if lists & Lst.BL: return True
	if lists & Lst.AL: return False
	return (detail.settings.get('BLP', 'AL') == 'BL')

class UserStatus:
	__slots__ = ('substatus', 'name', 'message', 'media')
	
	substatus: 'Substatus'
	name: Optional[str]
	message: Optional[str]
	media: Optional[Any]
	
	def __init__(self, name: Optional[str], message: Optional[str] = None) -> None:
		self.substatus = Substatus.FLN
		self.name = name
		self.message = message
		self.media = None
	
	def is_offlineish(self) -> bool:
		return self.substatus.is_offlineish()

class UserDetail:
	__slots__ = ('settings', 'groups', 'contacts')
	
	settings: Dict[str, Any]
	groups: Dict[str, 'Group']
	contacts: Dict[str, 'Contact']
	
	def __init__(self, settings: Dict[str, Any]) -> None:
		self.settings = settings
		self.groups = {}
		self.contacts = {}

class Group:
	__slots__ = ('id', 'name', 'is_favorite')
	
	id: str
	name: str
	is_favorite: bool
	
	def __init__(self, id: str, name: str, *, is_favorite: Optional[bool] = None) -> None:
		self.id = id
		self.name = name
		self.is_favorite = _default_if_none(is_favorite, False)

class MessageType(Enum):
	Chat = object()
	Typing = object()

class MessageData:
	__slots__ = ('sender', 'type', 'text', 'front_cache')
	
	sender: User
	type: MessageType
	text: Optional[str]
	front_cache: Dict[str, Any]
	
	def __init__(self, *, sender: User, type: MessageType, text: Optional[str]) -> None:
		self.sender = sender
		self.type = type
		self.text = text
		self.front_cache = {}

class TextWithData:
	__slots__ = ('text', 'yahoo_utf8')
	
	text: str
	yahoo_utf8: Any
	
	def __init__(self, text: str, yahoo_utf8: Any) -> None:
		self.text = text
		self.yahoo_utf8 = yahoo_utf8

class OIMMetadata:
	__slots__ = ('run_id', 'oim_num', 'from_member_name', 'from_member_friendly', 'to_member_name', 'last_oim_sent', 'oim_content_length')
	
	run_id: str
	oim_num: int
	from_member_name: str
	from_member_friendly: str
	to_member_name: str
	last_oim_sent: datetime
	oim_content_length: int
	
	def __init__(self, run_id: str, oim_num: int, from_member_name: str, from_member_friendly: str, to_member_name: str, last_oim_sent: datetime, oim_content_length: int) -> None:
		self.run_id = run_id
		self.oim_num = oim_num
		self.from_member_name = from_member_name
		self.from_member_friendly = from_member_friendly
		self.to_member_name = to_member_name
		self.last_oim_sent = last_oim_sent
		self.oim_content_length = oim_content_length

T = TypeVar('T')
def _default_if_none(x: Optional[T], default: T) -> T:
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
	
	def is_offlineish(self) -> bool:
		return self is Substatus.FLN or self is Substatus.HDN

class Lst(IntFlag):
	Empty = 0x00
	
	FL = 0x01
	AL = 0x02
	BL = 0x04
	RL = 0x08
	PL = 0x10
	
	label: str
	
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
	def Parse(cls, label: str) -> Optional['Lst']:
		if not hasattr(cls, '_MAP'):
			map = {}
			for lst in cls:
				map[lst.label.lower()] = lst
			setattr(cls, '_MAP', map)
		return getattr(cls, '_MAP').get(label.lower())

class Service:
	__slots__ = ('host', 'port')
	
	host: str
	port: int
	
	def __init__(self, host: str, port: int) -> None:
		self.host = host
		self.port = port
