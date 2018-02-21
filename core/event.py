from typing import TYPE_CHECKING, Callable, Optional, Dict, Any, List
from abc import ABCMeta, abstractmethod
from .models import User, UserYahoo, Contact, YahooContact, Lst

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

class YahooBackendEventHandler(metaclass = ABCMeta):
	__slots__ = ()
	
	def on_open(self) -> None:
		pass
	
	def on_close(self) -> None:
		pass
	
	@abstractmethod
	def on_presence_notification(self, contact: YahooContact) -> None: pass
	
	@abstractmethod
	def on_login_presence_notification(self, contact: YahooContact) -> None: pass
	
	@abstractmethod
	def on_invisible_absence_notification(self, contact: YahooContact) -> None: pass
	
	@abstractmethod
	def on_invisible_presence_notification(self, contact: YahooContact) -> None: pass
	
	@abstractmethod
	def on_absence_notification(self, contact: YahooContact) -> None: pass
	
	@abstractmethod
	def on_logout_notification(self, contact: YahooContact) -> None: pass
	
	@abstractmethod
	def on_conf_invite(self, conf: 'Conference', inviter: UserYahoo, invite_msg: Optional[str], conf_roster: List[str], voice_chat: int) -> None: pass
	
	@abstractmethod
	def on_conf_invite_decline(self, inviter: UserYahoo, conf_id: str, deny_msg: Optional[str]) -> None: pass
	
	@abstractmethod
	def on_init_contact_request(self, user_adder: UserYahoo, user_added: UserYahoo, message: Optional[str]) -> None: pass
	
	@abstractmethod
	def on_deny_contact_request(self, user_denier: UserYahoo, deny_message: Optional[str]) -> None: pass
	
	@abstractmethod
	def on_notify_notification(self, sender: UserYahoo, notif_dict: Dict[str, Any]) -> None: pass
	
	@abstractmethod
	def on_xfer_init(self, sender: UserYahoo, xfer_dict: Dict[str, Any]) -> None: pass

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

class ConferenceEventHandler(metaclass = ABCMeta):
	__slots__ = ()
	
	def on_open(self) -> None:
		pass
	
	@abstractmethod
	def on_participant_joined(self, cs_other: 'ConferenceSession') -> None: pass
	
	@abstractmethod
	def on_participant_left(self, cs_other: 'ConferenceSession') -> None: pass
	
	@abstractmethod
	def on_message(self, sender: UserYahoo, message_dict: Dict[str, Any]) -> None: pass
