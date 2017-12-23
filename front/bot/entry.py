from typing import cast
import asyncio
from core.client import Client
from core.models import Substatus, Lst, Contact, User
from core.backend import Backend, BackendSession, Chat, ChatSession
from core import event

CLIENT = Client('testbot', '0.1')
BOT_EMAIL = 'test@bot.log1p.xyz'

def register(loop: asyncio.AbstractEventLoop, backend: Backend) -> None:
	evt = BackendEventHandler()
	bs = backend.login_IKWIAD(BOT_EMAIL, CLIENT, evt)
	assert bs is not None
	evt.bs = bs

class BackendEventHandler(event.BackendEventHandler):
	__slots__ = ('bs',)
	
	bs: BackendSession
	
	def __init__(self) -> None:
		# `bs` is only None temporarily.
		# TODO: Find a better way.
		self.bs = cast(BackendSession, None)
	
	def on_open(self) -> None:
		detail = self.bs.user.detail
		assert detail is not None
		
		self.bs.me_update({ 'substatus': Substatus.NLN })
		uuid = self.bs.backend.util_get_uuid_from_email('test1@example.com')
		if uuid not in detail.contacts:
			self.bs.me_contact_add(uuid, Lst.FL, "Test 1")
			self.bs.me_contact_add(uuid, Lst.AL, "Test 1")
	
	def on_presence_notification(self, contact: Contact) -> None:
		pass
	
	def on_chat_invite(self, chat: Chat, inviter: User) -> None:
		evt = ChatEventHandler(self.bs)
		cs = chat.join(self.bs, evt)
		evt.cs = cs
	
	def on_added_to_list(self, lst: Lst, user: User) -> None:
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
		# `cs` is only None temporarily.
		# TODO: Find a better way.
		self.cs = cast(ChatSession, None)
	
	def on_open(self) -> None:
		self.cs.send_message_to_everyone((MSG_HEADER + "Hello, world!").encode('utf-8'))
	
	def on_participant_joined(self, cs_other: 'ChatSession') -> None:
		pass
	
	def on_participant_left(self, cs_other: 'ChatSession') -> None:
		pass
	
	def on_message(self, sender: User, data: bytes) -> None:
		if b'Content-Type: text/plain' not in data:
			return
		d = data.decode('utf-8').split('\r\n\r\n')[-1]
		msg = "You, {}, insist that \"{}\".".format(sender.status.name, d)
		self.cs.send_message_to_everyone((MSG_HEADER + msg).encode('utf-8'))

MSG_HEADER = '''
MIME-Version: 1.0
Content-Type: text/plain; charset=UTF-8
X-MMS-IM-Format: FN=MS%20Shell%20Dlg; EF=; CO=0; CS=0; PF=0

'''.replace('\n', '\r\n')
