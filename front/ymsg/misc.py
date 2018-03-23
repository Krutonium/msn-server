from typing import Optional, Tuple, Any, Iterable, Dict, List
from multidict import MultiDict
from enum import IntEnum
import time

from util.misc import first_in_iterable

from core.backend import Backend, BackendSession, ChatSession
from core.models import User, Contact, Substatus

class YMSGService(IntEnum):
	LogOn = 0x01
	LogOff = 0x02
	IsAway = 0x03
	IsBack = 0x04
	Message = 0x06
	UserStat = 0x0a
	ContactNew = 0x0f
	AddIgnore = 0x11
	PingConfiguration = 0x12
	SkinName = 0x15
	Passthrough2 = 0x16
	ConfInvite = 0x18
	ConfLogon = 0x19
	ConfDecline = 0x1a
	ConfLogoff = 0x1b
	ConfAddInvite = 0x1c
	ConfMsg = 0x1d
	Notify = 0x4b
	Handshake = 0x4c
	P2PFileXfer = 0x4d
	PeerToPeer = 0x4f
	AuthResp = 0x54
	List = 0x55
	Auth = 0x57
	FriendAdd = 0x83
	FriendRemove = 0x84
	Ignore = 0x85
	Ping = 0x8a
	
	# TODO: Figure out these `YMSGService`s
	# Happens when enabling voice
	Unknown_0x4a = 0x4a
	# Happens when enabling video
	Unknown_0x50 = 0x50
	# Happens during "Join user in chat"
	Unknown_0x96 = 0x96
	# Happened after waiting a long time doing nothing
	# YMSG \x0a \0\0\0\0 \x14\x00 \xa1\x00 \0\0\0\xb4 D\x8221 09\xc0\x80 t1y@yahoo.com\xc0\x80
	Unknown_0xa1 = 0xa1

class YMSGStatus(IntEnum):
	# Available/Client Request
	Available   = 0x00000000
	WebLogin    = 0x5a55aa55
	# BRB/Server Response
	BRB         = 0x00000001
	Busy        = 0x00000002
	# "Not at Home"/BadUsername
	NotAtHome   = 0x00000003
	NotAtDesk   = 0x00000004
	NotInOffice = 0x00000005
	OnPhone     = 0x00000006
	OnVacation  = 0x00000007
	OutToLunch  = 0x00000008
	SteppedOut  = 0x00000009
	Invisible   = 0x0000000c
	Bad         = 0x0000000d
	Locked      = 0x0000000e
	Typing      = 0x00000016
	Custom      = 0x00000063
	Offline     = 0x5a55aa56
	LoginError  = 0xffffffff

EncodedYMSG = Tuple[YMSGService, YMSGStatus, Dict[str, str]]

def build_contact_request_notif(user_adder: User, user_added: User, message: Optional[str], utf8: Optional[str]) -> Iterable[EncodedYMSG]:
	contact_request_data = MultiDict([
		('1', yahoo_id(user_added)),
		('3', yahoo_id(user_adder)),
		('14', message),
	])
	
	if utf8 is not None: contact_request_data.add('97', utf8)
	contact_request_data.add('15', time.time())
	
	yield (YMSGService.ContactNew, YMSGStatus.NotAtHome, contact_request_data)

def build_contact_deny_notif(user_denier: User, bs: BackendSession, deny_message: Optional[str]) -> Iterable[EncodedYMSG]:
	user_to = bs.user
	
	contact_deny_data = MultiDict([
		('1', yahoo_id(user_to)),
		('3', yahoo_id(user_denier)),
		('14', deny_message)
	])
	
	yield (YMSGService.ContactNew, YMSGStatus.OnVacation, contact_deny_data)

def build_notify_notif(user_from: User, bs: BackendSession, notif_dict: Dict[str, Any]) -> Iterable[EncodedYMSG]:
	user_to = bs.user
	
	notif_to_dict = MultiDict([
		('5', yahoo_id(user_to)),
		('4', yahoo_id(user_from)),
		('49', notif_dict.get('49')),
		('14', notif_dict.get('14')),
		('13', notif_dict.get('13'))
	])
	
	yield (YMSGService.Notify, YMSGStatus.BRB, notif_to_dict)

def build_message_packet(user_from: User, bs: BackendSession, message_dict: Dict[str, Any]) -> Iterable[EncodedYMSG]:
	user_to = bs.user
	
	message_to_dict = MultiDict([
		('5', yahoo_id(user_to)),
		('4', yahoo_id(user_from)),
		('14', message_dict.get('14')),
		('63', message_dict.get('63')),
		('64', message_dict.get('64'))
	])
	
	if message_dict.get('97') is not None:
		message_to_dict.add('97', message_dict.get('97'))
	
	yield (YMSGService.Message, YMSGStatus.BRB, message_to_dict)

def build_ft_packet(user_from: User, bs: BackendSession, xfer_dict: Dict[str, Any]) -> Iterable[EncodedYMSG]:
	user_to = bs.user
	
	ft_dict = MultiDict([
		('5', yahoo_id(user_to)),
		('4', yahoo_id(user_from))
	])
	
	ft_dict.add('13', xfer_dict.get('13'))
	if xfer_dict.get('27') is not None: ft_dict.add('27', xfer_dict.get('27'))
	if xfer_dict.get('28') is not None: ft_dict.add('28', xfer_dict.get('28'))
	if xfer_dict.get('20') is not None: ft_dict.add('20', xfer_dict.get('20'))
	if xfer_dict.get('14') is not None: ft_dict.add('14', xfer_dict.get('14'))
	if str(xfer_dict.get('13')) == '1': ft_dict.add('38', 86400)
	if xfer_dict.get('53') is not None: ft_dict.add('53', xfer_dict.get('53'))
	ft_dict.add('49', xfer_dict.get('49'))
	
	yield (YMSGService.P2PFileXfer, YMSGStatus.BRB, ft_dict)

def build_conf_invite(user_from: User, bs: BackendSession, conf_id: str, invite_msg: Optional[str], conf_roster: List[str], voice_chat: int, existing_conf: bool = False) -> Iterable[EncodedYMSG]:
	user_to = bs.user
	
	conf_invite_dict = MultiDict([
		('1', yahoo_id(user_to)),
		('57', conf_id),
		('50', yahoo_id(user_from)),
		('58', invite_msg)
	])
	
	for conf_user in conf_roster:
		conf_invite_dict.add('52', conf_user)
	
	conf_invite_dict.add('13', voice_chat)
	
	yield ((YMSGService.ConfInvite if not existing_conf else YMSGService.ConfAddInvite), YMSGStatus.BRB, conf_invite_dict)

def build_conf_invite_decline(inviter: User, bs: BackendSession, conf_id: str, deny_msg: Optional[str]) -> Iterable[EncodedYMSG]:
	user_to = bs.user
	
	conf_decline_dict = MultiDict([
		('1', yahoo_id(user_to)),
		('57', conf_id),
		('54', yahoo_id(inviter)),
		('14', deny_msg)
	])
	
	yield (YMSGService.ConfDecline, YMSGStatus.BRB, conf_decline_dict)

def build_conf_logon(bs: BackendSession, cs_other: ChatSession) -> Iterable[EncodedYMSG]:
	user_from = cs_other.user
	user_to = bs.user
	
	conf_logon_dict = MultiDict([
		('1', yahoo_id(user_to)),
		('57', cs_other.chat.ids['ymsg/conf']),
		('53', yahoo_id(user_from))
	])
	
	yield (YMSGService.ConfLogon, YMSGStatus.BRB, conf_logon_dict)

def build_conf_logoff(bs: BackendSession, cs_other: ChatSession) -> Iterable[EncodedYMSG]:
	user_from = cs_other.user
	user_to = bs.user
	
	conf_logoff_dict = MultiDict([
		('1', yahoo_id(user_to)),
		('57', cs_other.chat.ids['ymsg/conf']),
		('56', yahoo_id(user_from))
	])
	
	yield (YMSGService.ConfLogoff, YMSGStatus.BRB, conf_logoff_dict)

def build_conf_message_packet(sender: User, cs: ChatSession, message_dict: Dict[str, Any]) -> Iterable[EncodedYMSG]:
	from_status = sender.status
	user_to = cs.user
	
	conf_message_dict = MultiDict([
		('1', yahoo_id(user_to)),
		('57', cs.chat.ids['ymsg/conf']),
		('3', yahoo_id(sender)),
		('14', message_dict.get('14'))
	])
	
	if message_dict.get('97') is not None:
		conf_message_dict.add('97', message_dict.get('97'))
	
	yield (YMSGService.ConfMsg, YMSGStatus.BRB, conf_message_dict)

def yahoo_id(user: User) -> str:
	return user.email.split('@', 1)[0]

def convert_to_substatus(ymsg_status: YMSGStatus) -> Substatus:
	if ymsg_status is YMSGStatus.Offline:
		return Substatus.FLN
	if ymsg_status is YMSGStatus.Available:
		return Substatus.NLN
	if ymsg_status is YMSGStatus.BRB:
		return Substatus.BRB
	if ymsg_status is YMSGStatus.Busy:
		return Substatus.BSY
	if ymsg_status is YMSGStatus.Invisible:
		return Substatus.HDN
	if ymsg_status is YMSGStatus.NotAtHome:
		return Substatus.AWY
	if ymsg_status is YMSGStatus.NotAtDesk:
		return Substatus.AWY
	if ymsg_status is YMSGStatus.NotInOffice:
		return Substatus.AWY
	if ymsg_status is YMSGStatus.OnPhone:
		return Substatus.PHN
	if ymsg_status is YMSGStatus.OutToLunch:
		return Substatus.LUN
	if ymsg_status is YMSGStatus.SteppedOut:
		return Substatus.AWY
	if ymsg_status is YMSGStatus.OnVacation:
		return Substatus.AWY
	if ymsg_status is YMSGStatus.Locked:
		return Substatus.AWY
	if ymsg_status is YMSGStatus.LoginError:
		return Substatus.FLN
	if ymsg_status is YMSGStatus.Bad:
		return Substatus.FLN
	return Substatus.NLN

def convert_from_substatus(substatus: Substatus) -> YMSGStatus:
	if substatus is Substatus.FLN:
		return YMSGStatus.Offline
	if substatus is Substatus.NLN:
		return YMSGStatus.Available
	if substatus is Substatus.BSY:
		return YMSGStatus.Busy
	if substatus is Substatus.IDL:
		return YMSGStatus.NotAtHome
	if substatus is Substatus.BRB:
		return YMSGStatus.BRB
	if substatus is Substatus.AWY:
		return YMSGStatus.NotAtHome
	if substatus is Substatus.PHN:
		return YMSGStatus.OnPhone
	if substatus is Substatus.LUN:
		return YMSGStatus.OutToLunch
	if substatus is Substatus.HDN:
		return YMSGStatus.Invisible
	return YMSGStatus.Bad
