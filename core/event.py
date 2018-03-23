from typing import TYPE_CHECKING, Callable, Optional, Dict, Any, List
from abc import ABCMeta, abstractmethod
from .models import User, Contact, Lst, MessageData, TextWithData, Substatus

if TYPE_CHECKING:
	from .backend import Chat, ChatSession

class BackendEventHandler(metaclass = ABCMeta):
	__slots__ = ()
	
	def on_open(self) -> None:
		pass
	
	def on_close(self) -> None:
		pass
	
	@abstractmethod
	def on_presence_notification(self, contact: Contact, old_substatus: Substatus) -> None: pass
	
	@abstractmethod
	def on_chat_invite(self, chat: 'Chat', inviter: User, *, invite_msg: Optional[str] = None, roster: Optional[List[str]] = None, voice_chat: Optional[int] = None, existing: bool = False) -> None: pass
	
	# `user` added me to their FL, and they're now on my RL.
	@abstractmethod
	def on_added_to_list(self, user: User, *, message: Optional[TextWithData] = None) -> None: pass
	
	# `user` didn't accept contact request; currently only used on YMSG
	@abstractmethod
	def on_contact_request_denied(self, user: User, message: Optional[str]) -> None: pass
	
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
	def on_invite_declined(self, invited_user: User, *, message: Optional[str] = None) -> None: pass
	
	@abstractmethod
	def on_message(self, data: MessageData) -> None: pass
