from typing import cast, Optional, List
import asyncio

from core.client import Client
from core.models import Substatus, Lst, Contact, User, TextWithData, MessageData, MessageType
from core.backend import Backend, BackendSession, Chat, ChatSession
from core import event

CLIENT = Client('testbot', '0.1', 'direct')

def register(loop: asyncio.AbstractEventLoop, backend: Backend) -> None:
	for i in range(5):
		uuid = backend.util_get_uuid_from_email('bot{}@bot.log1p.xyz'.format(i))
		assert uuid is not None
		evt = BackendEventHandler()
		bs = backend.login(uuid, CLIENT, evt)
		assert bs is not None
		evt.bs = bs
		
		bs.me_update({ 'substatus': Substatus.NLN })
		print("Bot active:", bs.user.status.name)

class BackendEventHandler(event.BackendEventHandler):
	__slots__ = ('bs',)
	
	bs: BackendSession
	
	def __init__(self) -> None:
		# `bs` is assigned shortly after
		pass
	
	def on_open(self) -> None:
		pass
	
	def on_presence_notification(self, contact: Contact, old_substatus: Substatus) -> None:
		pass
	
	def on_chat_invite(self, chat: Chat, inviter: User, *, invite_msg: Optional[str] = None, roster: Optional[List[str]] = None, voice_chat: Optional[int] = None, existing: bool = False) -> None:
		evt = ChatEventHandler(self.bs)
		cs = chat.join(self.bs, evt)
		evt.cs = cs
		chat.send_participant_joined(cs)
	
	def on_added_to_list(self, user: User, *, message: Optional[TextWithData] = None) -> None:
		pass
	
	def on_contact_request_denied(self, user: User, message: Optional[str]) -> None:
		pass
	
	def on_pop_boot(self) -> None:
		pass
	
	def on_pop_notify(self) -> None:
		pass

class ChatEventHandler(event.ChatEventHandler):
	__slots__ = ('bs', 'cs')
	
	bs: BackendSession
	cs: ChatSession
	
	def __init__(self, bs: BackendSession) -> None:
		self.bs = bs
		# `cs` is assigned shortly after
	
	def on_open(self) -> None:
		pass
	
	def on_participant_joined(self, cs_other: 'ChatSession') -> None:
		pass
	
	def on_participant_left(self, cs_other: 'ChatSession') -> None:
		pass
	
	def on_invite_declined(self, invited_user: User, *, message: Optional[str] = None) -> None:
		pass
	
	def on_message(self, message: MessageData) -> None:
		if message.type is not MessageType.Chat:
			return
		self.cs.send_message_to_everyone(MessageData(
			sender = self.cs.user,
			type = MessageType.Chat,
			text = "lol :p",
		))
