from typing import TYPE_CHECKING, Callable
from abc import ABCMeta, abstractmethod
from .models import User, Contact, Lst

if TYPE_CHECKING:
	from .backend import Chat, ChatSession

class BackendEventHandler(metaclass = ABCMeta):
	__slots__ = ()
	
	def on_open(self) -> None:
		pass
	
	def on_close(self) -> None:
		pass
	
	@abstractmethod
	def on_presence_notification(self, contact: Contact) -> None: pass
	
	@abstractmethod
	def on_chat_invite(self, chat: 'Chat', inviter: User) -> None: pass
	
	@abstractmethod
	def on_added_to_list(self, lst: Lst, user: User) -> None: pass
	
	@abstractmethod
	def on_pop_boot(self) -> None: pass
	
	@abstractmethod
	def on_pop_notify(self) -> None: pass

class ChatEventHandler(metaclass = ABCMeta):
	__slots__ = ()
	
	def on_open(self) -> None:
		pass
	
	def on_close(self) -> None:
		pass
	
	@abstractmethod
	def on_participant_joined(self, cs_other: 'ChatSession') -> None: pass
	
	@abstractmethod
	def on_participant_left(self, cs_other: 'ChatSession') -> None: pass
	
	@abstractmethod
	def on_message(self, sender: User, data: bytes) -> None: pass
