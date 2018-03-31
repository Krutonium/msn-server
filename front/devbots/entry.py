from typing import cast, Optional, List
import asyncio
import random

from core.client import Client
from core.models import Substatus, Lst, Contact, User, TextWithData, MessageData, MessageType
from core.backend import Backend, BackendSession, Chat, ChatSession
from core import event

CLIENT = Client('testbot', '0.1', 'direct')

def register(loop: asyncio.AbstractEventLoop, backend: Backend) -> None:
	for i in range(5):
		uuid = backend.util_get_uuid_from_email('bot{}@bot.log1p.xyz'.format(i))
		assert uuid is not None
		bs = backend.login(uuid, CLIENT, BackendEventHandler(loop))
		assert bs is not None

class BackendEventHandler(event.BackendEventHandler):
	__slots__ = ('loop', 'bs')
	
	loop: asyncio.AbstractEventLoop
	bs: BackendSession
	
	def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
		self.loop = loop
	
	def on_open(self) -> None:
		self.bs.me_update({ 'substatus': Substatus.Online })
		print("Bot active:", self.bs.user.status.name)
	
	def on_presence_notification(self, contact: Contact, old_substatus: Substatus) -> None:
		pass
	
	def on_chat_invite(self, chat: Chat, inviter: User, *, invite_msg: Optional[str] = None, roster: Optional[List[str]] = None, voice_chat: Optional[int] = None, existing: bool = False) -> None:
		cs = chat.join('testbot', self.bs, ChatEventHandler(self.loop, self.bs))
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
	__slots__ = ('loop', 'bs', 'cs', '_sending')
	
	loop: asyncio.AbstractEventLoop
	bs: BackendSession
	cs: ChatSession
	_sending: bool
	
	def __init__(self, loop: asyncio.AbstractEventLoop, bs: BackendSession) -> None:
		self.loop = loop
		self.bs = bs
		self._sending = False
	
	def on_open(self) -> None:
		pass
	
	def on_participant_joined(self, cs_other: ChatSession) -> None:
		pass
	
	def on_participant_left(self, cs_other: ChatSession) -> None:
		pass
	
	def on_invite_declined(self, invited_user: User, *, message: Optional[str] = None) -> None:
		pass
	
	def on_message(self, message: MessageData) -> None:
		if message.type is not MessageType.Chat:
			return
		
		if self._sending:
			return
		
		if message.sender.email.endswith('@bot.log1p.xyz'):
			return
		
		me = self.cs.user
		self._sending = True
		
		typing_message = MessageData(sender = me, type = MessageType.Typing)
		self.cs.send_message_to_everyone(typing_message)
		
		self.loop.create_task(self._send_delayed(random.uniform(0.5, 1), MessageData(
			sender = me, type = MessageType.Chat,
			text = "lol :p",
		)))
	
	async def _send_delayed(self, delay: float, message: MessageData) -> None:
		await asyncio.sleep(delay, loop = self.loop)
		self.cs.send_message_to_everyone(message)
		self._sending = False
