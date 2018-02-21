from typing import Optional, Tuple, Any, Iterable, Dict, List
from multidict import MultiDict
from util.misc import first_in_iterable
import time

from core.backend import Backend, YahooBackendSession, ConferenceSession
from core.models import UserYahoo, YahooContact, YMSGService, YMSGStatus

def build_yahoo_login_presence_notif(ctc: YahooContact, dialect: int, backend: Backend, ybs: Optional[YahooBackendSession]) -> Iterable[Tuple[int, int, Dict[str, Any]]]:
	status = ctc.status
	head = ctc.head
	
	user_yahoo = ybs.user_yahoo
	detail = user_yahoo.detail
	user_status = user_yahoo.status
	
	user_id = backend.util_get_yahoo_uuid_from_email(status.name)[:8].upper()
	
	contact_presence_data = MultiDict(
		[
			('0', user_status.name),
			('7', status.name),
			('10', status.substatus)
		]
	)
	
	contact_presence_data.add('11', user_id)
	contact_presence_data.add('17', 0)
	contact_presence_data.add('13', 1)
	
	yield (YMSGService.LogOn, YMSGStatus.BRB, contact_presence_data)

def build_yahoo_presence_invisible_notif(ctc: YahooContact, dialect: int, backend: Backend, ybs: Optional[YahooBackendSession]) -> Iterable[Tuple[int, int, Dict[str, Any]]]:
	status = ctc.status
	
	user_id = backend.util_get_yahoo_uuid_from_email(status.name)[:8].upper()
	
	contact_presence_data = MultiDict(
		[
			('7', status.name),
			('10', status.substatus)
		]
	)
	
	contact_presence_data.add('11', user_id)
	contact_presence_data.add('17', 0)
	contact_presence_data.add('13', 1)
	
	yield (YMSGService.LogOn, YMSGStatus.BRB, contact_presence_data)

def build_yahoo_logout_notif(ctc: YahooContact, dialect: int, backend: Backend, ybs: Optional[YahooBackendSession]) -> Iterable[Tuple[int, int, Dict[str, Any]]]:
	status = ctc.status
	
	user_id = backend.util_get_yahoo_uuid_from_email(status.name)[:8].upper()
	
	contact_logoff_data = MultiDict(
		[
			('7', status.name),
			('10', status.substatus),
			('11', user_id),
			('17', 0),
			('13', 0),
			('60', 2)
		]
	)
	
	yield (YMSGService.LogOff, YMSGStatus.BRB, contact_logoff_data)

def build_yahoo_presence_notif(ctc: YahooContact, dialect: int, backend: Backend, ybs: Optional[YahooBackendSession]) -> Iterable[Tuple[int, int, Dict[str, Any]]]:
	status = ctc.status
	head = ctc.head
	
	user_id = backend.util_get_yahoo_uuid_from_email(status.name)[:8].upper()
	
	contact_presence_data = MultiDict(
		[
			('7', status.name),
			('10', status.substatus)
		]
	)
	
	contact_presence_data.add('11', user_id)
	contact_presence_data.add('17', 0)
	contact_presence_data.add('13', 1)
	
	yield (YMSGService.IsBack, YMSGStatus.BRB, contact_presence_data)

def build_yahoo_invisible_absence_notif(ctc: YahooContact, dialect: int, backend: Backend, ybs: Optional[YahooBackendSession]) -> Iterable[Tuple[int, int, Dict[str, Any]]]:
	status = ctc.status
	head = ctc.head
	
	user_id = backend.util_get_yahoo_uuid_from_email(status.name)[:8].upper()
	
	contact_invisible_absence_data = MultiDict(
		[
			('7', status.name),
			('10', 0),
			('13', 0),
			('47', 2)
		]
	)
	
	yield (YMSGService.LogOff, YMSGStatus.BRB, contact_invisible_absence_data)

def build_yahoo_absence_notif(ctc: YahooContact, dialect: int, backend: Backend, ybs: Optional[YahooBackendSession]) -> Iterable[Tuple[int, int, Dict[str, Any]]]:
	status = ctc.status
	
	user_id = backend.util_get_yahoo_uuid_from_email(status.name)[:8].upper()
	
	contact_absence_data = MultiDict(
		[
			('7', status.name),
			('10', status.substatus)
		]
	)
	
	if status.substatus == YMSGStatus.Custom and status.message not in ({'text': '', 'is_away_message': 0},):
		contact_absence_data.add('19', status.message['text'])
		contact_absence_data.add('47', status.message['is_away_message'])
	
	contact_absence_data.add('11', user_id)
	contact_absence_data.add('17', 0)
	contact_absence_data.add('13', 1)
	
	yield (YMSGService.IsAway, YMSGStatus.BRB, contact_absence_data)

def build_yahoo_contact_request_notif(user_adder: UserYahoo, user_added: UserYahoo, message: Optional[str], utf8: Optional[str]) -> Iterable[Tuple[int, int, Dict[str, Any]]]:
	adder_status = user_adder.status
	user_status = user_added.status
	
	contact_request_data = MultiDict(
		[
			('1', user_status.name),
			('3', adder_status.name),
			('14', message),
		]
	)
	
	if utf8 is not None: contact_request_data.add('97', utf8)
	contact_request_data.add('15', time.time())
	
	yield (YMSGService.ContactNew, YMSGStatus.NotAtHome, contact_request_data)

def build_yahoo_contact_deny_notif(user_denier: UserYahoo, deny_message: Optional[str]) -> Iterable[Tuple[int, int, Dict[str, Any]]]:
	denier_status = user_denier.status
	
	contact_deny_data = MultiDict(
		[
			('3', denier_status.name),
			('14', deny_message)
		]
	)
	
	yield (YMSGService.ContactNew, YMSGStatus.OnVacation, contact_deny_data)

def build_yahoo_notify_notif(user_from: UserYahoo, ybs: Optional[YahooBackendSession], notif_dict: Dict[str, Any]) -> Iterable[Tuple[int, int, Dict[str, Any]]]:
	from_status = user_from.status
	user_to = ybs.user_yahoo
	to_status = user_to.status
	
	notif_to_dict = MultiDict(
		[
			('5', to_status.name),
			('4', from_status.name),
			('49', notif_dict.get('49')),
			('14', notif_dict.get('14')),
			('13', notif_dict.get('13'))
		]
	)
	
	yield (YMSGService.Notify, YMSGStatus.BRB, notif_to_dict)

def build_yahoo_message_packet(user_from: UserYahoo, ybs: Optional[YahooBackendSession], message_dict: Dict[str, Any]) -> Iterable[Tuple[int, int, Dict[str, Any]]]:
	from_status = user_from.status
	user_to = ybs.user_yahoo
	to_status = user_to.status
	
	message_to_dict = MultiDict(
		[
			('5', to_status.name),
			('4', from_status.name),
			('14', message_dict.get('14')),
			('63', message_dict.get('63')),
			('64', message_dict.get('64'))
		]
	)
	
	if message_dict.get('97') is not None: message_to_dict.add('97', message_dict.get('97'))
	
	yield (YMSGService.Message, YMSGStatus.BRB, message_to_dict)

def build_yahoo_ft_packet(user_from: UserYahoo, ybs: Optional[YahooBackendSession], xfer_dict: Dict[str, Any]) -> Iterable[Tuple[int, int, Dict[str, Any]]]:
	from_status = user_from.status
	user_to = ybs.user_yahoo
	to_status = user_to.status
	
	ft_dict = MultiDict(
		[
			('5', to_status.name),
			('4', from_status.name),
			('13', xfer_dict.get('13')),
			('27', xfer_dict.get('27')),
			('28', xfer_dict.get('28')),
			('20', xfer_dict.get('20')),
			('14', xfer_dict.get('14')),
			('38', 86400),
			('49', xfer_dict.get('49'))
		]
	)
	
	yield (YMSGService.P2PFileXfer, YMSGStatus.BRB, ft_dict)

def build_yahoo_conf_invite(user_from: UserYahoo, ybs: Optional[YahooBackendSession], conf_id: str, invite_msg: Optional[str], conf_roster: List[str], voice_chat: int, existing_conf: bool = False) -> Iterable[Tuple[int, int, Dict[str, Any]]]:
	from_status = user_from.status
	user_to = ybs.user_yahoo
	to_status = user_to.status
	
	conf_invite_dict = MultiDict(
		[
			('1', to_status.name),
			('57', conf_id),
			('50', from_status.name),
			('58', invite_msg)
		]
	)
	
	for conf_user in conf_roster:
		conf_invite_dict.add('52', conf_user)
	
	conf_invite_dict.add('13', voice_chat)
	
	yield ((YMSGService.ConfInvite if not existing_conf else YMSGService.ConfAddInvite), YMSGStatus.BRB, conf_invite_dict)

def build_yahoo_conf_invite_decline(inviter: UserYahoo, ybs: Optional[YahooBackendSession], conf_id: str, deny_msg: Optional[str]) -> Iterable[Tuple[int, int, Dict[str, Any]]]:
	from_status = inviter.status
	user_to = ybs.user_yahoo
	to_status = user_to.status
	
	conf_decline_dict = MultiDict(
		[
			('1', to_status.name),
			('57', conf_id),
			('54', from_status.name),
			('14', deny_msg)
		]
	)
	
	yield (YMSGService.ConfDecline, YMSGStatus.BRB, conf_decline_dict)

def build_yahoo_conf_logon(ybs: Optional[YahooBackendSession], cs_other: ConferenceSession) -> Iterable[Tuple[int, int, Dict[str, Any]]]:
	from_status = cs_other.user_yahoo.status
	user_to = ybs.user_yahoo
	to_status = user_to.status
	
	conf_logon_dict = MultiDict(
		[
			('1', to_status.name),
			('57', cs_other.conf.id),
			('53', from_status.name)
		]
	)
	
	yield (YMSGService.ConfLogon, YMSGStatus.BRB, conf_logon_dict)

def build_yahoo_conf_logoff(ybs: Optional[YahooBackendSession], cs_other: ConferenceSession) -> Iterable[Tuple[int, int, Dict[str, Any]]]:
	from_status = cs_other.user_yahoo.status
	user_to = ybs.user_yahoo
	to_status = user_to.status
	
	conf_logoff_dict = MultiDict(
		[
			('1', to_status.name),
			('57', cs_other.conf.id),
			('56', from_status.name)
		]
	)
	
	yield (YMSGService.ConfLogoff, YMSGStatus.BRB, conf_logoff_dict)

def build_yahoo_conf_message_packet(sender: UserYahoo, cs: ConferenceSession, message_dict: Dict[str, Any]) -> Iterable[Tuple[int, int, Dict[str, Any]]]:
	from_status = sender.status
	user_to = cs.user_yahoo
	to_status = user_to.status
	
	conf_message_dict = MultiDict(
		[
			('1', to_status.name),
			('57', cs.conf.id),
			('3', from_status.name),
			('14', message_dict.get('14'))
		]
	)
	
	if message_dict.get('97') is not None: conf_message_dict.add('97', message_dict.get('97'))
	
	yield (YMSGService.ConfMsg, YMSGStatus.BRB, conf_message_dict)