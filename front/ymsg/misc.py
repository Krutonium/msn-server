from typing import Optional, Tuple, Any, Iterable, Dict, List
from multidict import MultiDict
from util.misc import first_in_iterable
import time

from core.backend import Backend, YahooBackendSession, ConferenceSession
from core.models import UserYahoo, YahooContact, YMSGService, YMSGStatus

def build_yahoo_login_presence_notif(ctc: YahooContact, dialect: int, ybs: Optional[YahooBackendSession]) -> Iterable[Tuple[int, int, Dict[str, Any]]]:
	status = ctc.status
	head = ctc.head
	
	user_yahoo = ybs.user_yahoo
	
	user_id = head.uuid[:8].upper()
	
	contact_presence_data = MultiDict(
		[
			('0', user_yahoo.yahoo_id),
			('7', ctc.yahoo_id),
			('10', status.substatus),
			('11', user_id),
			('17', 0),
			('13', 1)
		]
	)
	
	yield (YMSGService.LogOn, YMSGStatus.BRB, contact_presence_data)

def build_yahoo_presence_invisible_notif(ctc: YahooContact, dialect: int) -> Iterable[Tuple[int, int, Dict[str, Any]]]:
	status = ctc.status
	head = ctc.head
	
	user_id = head.uuid[:8].upper()
	
	contact_presence_data = MultiDict(
		[
			('7', ctc.yahoo_id),
			('10', status.substatus),
			('11', user_id),
			('17', 0),
			('13', 1)
		]
	)
	
	yield (YMSGService.LogOn, YMSGStatus.BRB, contact_presence_data)

def build_yahoo_logout_notif(ctc: YahooContact, dialect: int) -> Iterable[Tuple[int, int, Dict[str, Any]]]:
	head = ctc.head
	
	user_id = head.uuid[:8].upper()
	
	contact_logoff_data = MultiDict(
		[
			('7', ctc.yahoo_id),
			('10', 0),
			('11', user_id),
			('17', 0),
			('13', 0)
		]
	)
	
	yield (YMSGService.LogOff, YMSGStatus.BRB, contact_logoff_data)

def build_yahoo_presence_notif(ctc: YahooContact, dialect: int, ybs: Optional[YahooBackendSession]) -> Iterable[Tuple[int, int, Dict[str, Any]]]:
	status = ctc.status
	head = ctc.head
	user_to = ybs.user_yahoo
	
	user_id = head.uuid[:8].upper()
	
	contact_presence_data = MultiDict(
		[
			('0', user_to.yahoo_id),
			('7', ctc.yahoo_id),
			('10', status.substatus),
			('11', user_id),
			('17', 0),
			('13', 1)
		]
	)
	
	yield (YMSGService.IsBack, YMSGStatus.BRB, contact_presence_data)

def build_yahoo_absence_notif(ctc: YahooContact, dialect: int) -> Iterable[Tuple[int, int, Dict[str, Any]]]:
	status = ctc.status
	head = ctc.head
	
	user_id = head.uuid[:8].upper()
	
	contact_absence_data = MultiDict(
		[
			('7', ctc.yahoo_id),
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
	contact_request_data = MultiDict(
		[
			('1', user_added.yahoo_id),
			('3', user_adder.yahoo_id),
			('14', message),
		]
	)
	
	if utf8 is not None: contact_request_data.add('97', utf8)
	contact_request_data.add('15', time.time())
	
	yield (YMSGService.ContactNew, YMSGStatus.NotAtHome, contact_request_data)

def build_yahoo_contact_deny_notif(user_denier: UserYahoo, ybs: Optional[YahooBackendSession], deny_message: Optional[str]) -> Iterable[Tuple[int, int, Dict[str, Any]]]:
	user_to = ybs.user_yahoo
	
	contact_deny_data = MultiDict(
		[
			('1', user_to.yahoo_id),
			('3', user_denier.yahoo_id),
			('14', deny_message)
		]
	)
	
	yield (YMSGService.ContactNew, YMSGStatus.OnVacation, contact_deny_data)

def build_yahoo_notify_notif(user_from: UserYahoo, ybs: Optional[YahooBackendSession], notif_dict: Dict[str, Any]) -> Iterable[Tuple[int, int, Dict[str, Any]]]:
	user_to = ybs.user_yahoo
	
	notif_to_dict = MultiDict(
		[
			('5', user_to.yahoo_id),
			('4', user_from.yahoo_id),
			('49', notif_dict.get('49')),
			('14', notif_dict.get('14')),
			('13', notif_dict.get('13'))
		]
	)
	
	yield (YMSGService.Notify, YMSGStatus.BRB, notif_to_dict)

def build_yahoo_message_packet(user_from: UserYahoo, ybs: Optional[YahooBackendSession], message_dict: Dict[str, Any]) -> Iterable[Tuple[int, int, Dict[str, Any]]]:
	user_to = ybs.user_yahoo
	
	message_to_dict = MultiDict(
		[
			('5', user_to.yahoo_id),
			('4', user_from.yahoo_id),
			('14', message_dict.get('14')),
			('63', message_dict.get('63')),
			('64', message_dict.get('64'))
		]
	)
	
	if message_dict.get('97') is not None: message_to_dict.add('97', message_dict.get('97'))
	
	yield (YMSGService.Message, YMSGStatus.BRB, message_to_dict)

def build_yahoo_ft_packet(user_from: UserYahoo, ybs: Optional[YahooBackendSession], xfer_dict: Dict[str, Any]) -> Iterable[Tuple[int, int, Dict[str, Any]]]:
	user_to = ybs.user_yahoo
	
	ft_dict = MultiDict(
		[
			('5', user_to.yahoo_id),
			('4', user_from.yahoo_id)
		]
	)
	
	ft_dict.add('13', xfer_dict.get('13'))
	if xfer_dict.get('27') is not None: ft_dict.add('27', xfer_dict.get('27'))
	if xfer_dict.get('28') is not None: ft_dict.add('28', xfer_dict.get('28'))
	if xfer_dict.get('20') is not None: ft_dict.add('20', xfer_dict.get('20'))
	if xfer_dict.get('14') is not None: ft_dict.add('14', xfer_dict.get('14'))
	if xfer_dict.get('53') is not None: ft_dict.add('53', xfer_dict.get('53'))
	ft_dict.add('49', xfer_dict.get('49'))
	
	yield (YMSGService.P2PFileXfer, YMSGStatus.BRB, ft_dict)

def build_yahoo_conf_invite(user_from: UserYahoo, ybs: Optional[YahooBackendSession], conf_id: str, invite_msg: Optional[str], conf_roster: List[str], voice_chat: int, existing_conf: bool = False) -> Iterable[Tuple[int, int, Dict[str, Any]]]:
	user_to = ybs.user_yahoo
	
	conf_invite_dict = MultiDict(
		[
			('1', user_to.yahoo_id),
			('57', conf_id),
			('50', user_from.yahoo_id),
			('58', invite_msg)
		]
	)
	
	for conf_user in conf_roster:
		conf_invite_dict.add('52', conf_user)
	
	conf_invite_dict.add('13', voice_chat)
	
	yield ((YMSGService.ConfInvite if not existing_conf else YMSGService.ConfAddInvite), YMSGStatus.BRB, conf_invite_dict)

def build_yahoo_conf_invite_decline(inviter: UserYahoo, ybs: Optional[YahooBackendSession], conf_id: str, deny_msg: Optional[str]) -> Iterable[Tuple[int, int, Dict[str, Any]]]:
	user_to = ybs.user_yahoo
	
	conf_decline_dict = MultiDict(
		[
			('1', user_to.yahoo_id),
			('57', conf_id),
			('54', inviter.yahoo_id),
			('14', deny_msg)
		]
	)
	
	yield (YMSGService.ConfDecline, YMSGStatus.BRB, conf_decline_dict)

def build_yahoo_conf_logon(ybs: Optional[YahooBackendSession], cs_other: ConferenceSession) -> Iterable[Tuple[int, int, Dict[str, Any]]]:
	user_from = cs_other.user_yahoo
	user_to = ybs.user_yahoo
	
	conf_logon_dict = MultiDict(
		[
			('1', user_to.yahoo_id),
			('57', cs_other.conf.id),
			('53', user_from.yahoo_id)
		]
	)
	
	yield (YMSGService.ConfLogon, YMSGStatus.BRB, conf_logon_dict)

def build_yahoo_conf_logoff(ybs: Optional[YahooBackendSession], cs_other: ConferenceSession) -> Iterable[Tuple[int, int, Dict[str, Any]]]:
	user_from = cs_other.user_yahoo
	user_to = ybs.user_yahoo
	
	conf_logoff_dict = MultiDict(
		[
			('1', user_to.yahoo_id),
			('57', cs_other.conf.id),
			('56', user_from.yahoo_id)
		]
	)
	
	yield (YMSGService.ConfLogoff, YMSGStatus.BRB, conf_logoff_dict)

def build_yahoo_conf_message_packet(sender: UserYahoo, cs: ConferenceSession, message_dict: Dict[str, Any]) -> Iterable[Tuple[int, int, Dict[str, Any]]]:
	from_status = sender.status
	user_to = cs.user_yahoo
	
	conf_message_dict = MultiDict(
		[
			('1', user_to.yahoo_id),
			('57', cs.conf.id),
			('3', sender.yahoo_id),
			('14', message_dict.get('14'))
		]
	)
	
	if message_dict.get('97') is not None: conf_message_dict.add('97', message_dict.get('97'))
	
	yield (YMSGService.ConfMsg, YMSGStatus.BRB, conf_message_dict)