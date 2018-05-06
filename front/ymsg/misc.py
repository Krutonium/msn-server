from typing import Optional, Tuple, Any, Iterable, Dict, List, ClassVar
from urllib.parse import quote_plus
from multidict import MultiDict
from enum import IntEnum
import time

from util.misc import first_in_iterable, DefaultDict

from core.backend import Backend, BackendSession, Chat, ChatSession
from core.models import User, Contact, Substatus

import settings

class YMSGService(IntEnum):
	LogOn = 0x01
	LogOff = 0x02
	IsAway = 0x03
	IsBack = 0x04
	Message = 0x06
	IDActivate = 0x07
	UserStat = 0x0A
	ContactNew = 0x0F
	AddIgnore = 0x11
	PingConfiguration = 0x12
	SystemMessage = 0x14
	SkinName = 0x15
	Passthrough2 = 0x16
	MassMessage = 0x17
	ConfInvite = 0x18
	ConfLogon = 0x19
	ConfDecline = 0x1A
	ConfLogoff = 0x1B
	ConfAddInvite = 0x1C
	ConfMsg = 0x1D
	FileTransfer = 0x46
	VoiceChat = 0x4A
	Notify = 0x4B
	Handshake = 0x4C
	P2PFileXfer = 0x4D
	PeerToPeer = 0x4F
	VideoChat = 0x50
	AuthResp = 0x54
	List = 0x55
	Auth = 0x57
	FriendAdd = 0x83
	FriendRemove = 0x84
	Ignore = 0x85
	ContactDeny = 0x86
	GroupRename = 0x89
	Ping = 0x8A
	ChatJoin = 0x96
	
	# TODO: Figure out this `YMSGService`
	# Happens after waiting a long time doing nothing
	# YMSG \x0a \0\0\0\0 \x14\x00 \xa1\x00 \0\0\0\xb4 D\x8221 09\xc0\x80 t1y@yahoo.com\xc0\x80
	Unknown_0xa1 = 0xA1

class YMSGStatus(IntEnum):
	# Available/Client Request
	Available   = 0x00000000
	# BRB/Server Response
	BRB         = 0x00000001
	Busy        = 0x00000002
	# "Not at Home"/BadUsername
	NotAtHome   = 0x00000003
	NotAtDesk   = 0x00000004
	# "Not in Office"/OfflineMessage
	NotInOffice = 0x00000005
	OnPhone     = 0x00000006
	OnVacation  = 0x00000007
	OutToLunch  = 0x00000008
	SteppedOut  = 0x00000009
	Invisible   = 0x0000000C
	Bad         = 0x0000000D
	Locked      = 0x0000000E
	Typing      = 0x00000016
	Custom      = 0x00000063
	Idle        = 0x000003E7
	WebLogin    = 0x5A55AA55
	Offline     = 0x5A55AA56
	LoginError  = 0xFFFFFFFF
	
	@classmethod
	def ToSubstatus(cls, ymsg_status: 'YMSGStatus') -> Substatus:
		return _ToSubstatus[ymsg_status]
	
	@classmethod
	def FromSubstatus(cls, substatus: Substatus) -> 'YMSGStatus':
		return _FromSubstatus[substatus]

_ToSubstatus = DefaultDict(Substatus.Busy, {
	YMSGStatus.Offline: Substatus.Offline,
	YMSGStatus.Available: Substatus.Online,
	YMSGStatus.BRB: Substatus.BRB,
	YMSGStatus.Busy: Substatus.Busy,
	YMSGStatus.Idle: Substatus.Idle,
	YMSGStatus.Invisible: Substatus.Invisible,
	YMSGStatus.NotAtHome: Substatus.NotAtHome,
	YMSGStatus.NotAtDesk: Substatus.NotAtDesk,
	YMSGStatus.NotInOffice: Substatus.NotInOffice,
	YMSGStatus.OnPhone: Substatus.OnPhone,
	YMSGStatus.OutToLunch: Substatus.OutToLunch,
	YMSGStatus.SteppedOut: Substatus.SteppedOut,
	YMSGStatus.OnVacation: Substatus.OnVacation,
	YMSGStatus.Locked: Substatus.Away,
	YMSGStatus.LoginError: Substatus.Offline,
	YMSGStatus.Bad: Substatus.Offline,
})
_FromSubstatus = DefaultDict(YMSGStatus.Bad, {
	Substatus.Offline: YMSGStatus.Offline,
	Substatus.Online: YMSGStatus.Available,
	Substatus.Busy: YMSGStatus.Busy,
	Substatus.Idle: YMSGStatus.Idle,
	Substatus.BRB: YMSGStatus.BRB,
	Substatus.Away: YMSGStatus.NotAtHome,
	Substatus.OnPhone: YMSGStatus.OnPhone,
	Substatus.OutToLunch: YMSGStatus.OutToLunch,
	Substatus.Invisible: YMSGStatus.Invisible,
	Substatus.NotAtHome: YMSGStatus.NotAtHome,
	Substatus.NotAtDesk: YMSGStatus.NotAtDesk,
	Substatus.NotInOffice: YMSGStatus.NotInOffice,
	Substatus.OnVacation: YMSGStatus.OnVacation,
	Substatus.SteppedOut: YMSGStatus.SteppedOut,
})

EncodedYMSG = Tuple[YMSGService, YMSGStatus, Dict[str, str]]

def build_notify_notif(user_from: User, bs: BackendSession, notif_dict: Dict[str, Any]) -> Iterable[EncodedYMSG]:
	user_to = bs.user
	
	notif_to_dict = MultiDict([
		('5', yahoo_id(user_to.email)),
		('4', yahoo_id(user_from.email)),
		('49', notif_dict.get('49')),
		('14', notif_dict.get('14')),
		('13', notif_dict.get('13'))
	])
	
	yield (YMSGService.Notify, YMSGStatus.BRB, notif_to_dict)

def build_ft_packet(user_from: User, bs: BackendSession, xfer_dict: Dict[str, Any]) -> Iterable[EncodedYMSG]:
	user_to = bs.user
	
	ft_dict = MultiDict([
		('5', yahoo_id(user_to.email)),
		('4', yahoo_id(user_from.email))
	])
	
	ft_type = xfer_dict.get('13')
	ft_dict.add('13', ft_type)
	if ft_type == '1':
		ft_dict.add('27', xfer_dict.get('27'))
		ft_dict.add('28', xfer_dict.get('28'))
		url_filename = xfer_dict.get('53') or ''
		# When file name in HTTP string is sent to recipient by server, it is unescaped for some reason
		# Replace it with `urllib.parse.quote()`'d version!
		ft_dict.add('20', (xfer_dict.get('20') or '').replace(url_filename, quote_plus(url_filename, safe = '')))
		ft_dict.add('53', url_filename)
		ft_dict.add('14', xfer_dict.get('14'))
		ft_dict.add('53', xfer_dict.get('53'))
	if ft_type in ('2','3'):
		ft_dict.add('27', xfer_dict.get('27'))
		ft_dict.add('53', xfer_dict.get('53'))
	ft_dict.add('49', xfer_dict.get('49'))
	
	yield (YMSGService.P2PFileXfer, YMSGStatus.BRB, ft_dict)

def build_http_ft_packet(bs: BackendSession, sender: str, url_path: str, message: str):
	user = bs.user
	
	yield (YMSGService.FileTransfer, YMSGStatus.BRB, MultiDict([
		('1', yahoo_id(user.email)),
		('5', sender),
		('4', yahoo_id(user.email)),
		('14', message),
		# Uploaded files will only last for a day on the server
		('38', time.time() + 86400),
		('20', settings.YAHOO_FT_DL_HOST + '/tmp/file/' + url_path),
	]))

def build_conf_invite(user_from: User, bs: BackendSession, chat: Chat, invite_msg: str) -> Iterable[EncodedYMSG]:
	user_to = bs.user
	conf_id = chat.ids['ymsg/conf']
	
	conf_invite_dict = MultiDict([
		('1', yahoo_id(user_to.email)),
		('57', conf_id),
		('50', yahoo_id(user_from.email)),
		('58', invite_msg)
	])
	
	roster = list(chat.get_roster())
	for cs in roster:
		if cs.user.uuid == user_from.uuid: continue
		conf_invite_dict.add('52', yahoo_id(cs.user.email))
		conf_invite_dict.add('53', yahoo_id(cs.user.email))
	
	conf_invite_dict.add('13', chat.front_data.get('ymsg_voice_chat') or 0)
	
	yield ((YMSGService.ConfAddInvite if len(roster) > 1 else YMSGService.ConfInvite), YMSGStatus.BRB, conf_invite_dict)

def yahoo_id(email: str) -> str:
	email_parts = email.split('@', 1)
	if len(email_parts) == 2 and email_parts[1].startswith('yahoo.'):
		return email_parts[0]
	else:
		return email

def yahoo_id_to_uuid(bs: Optional[BackendSession], backend: Backend, yahoo_id: str) -> Optional[str]:
	email = None # type: Optional[str]
	
	if '@' in yahoo_id:
		email = yahoo_id
	elif '@yahoo.' in yahoo_id:
		return None
	elif bs:
		detail = bs.user.detail
		assert detail is not None
		pre = yahoo_id + '@yahoo.'
		for ctc in detail.contacts.values():
			if ctc.head.email.startswith(pre):
				email = ctc.head.email
				break
	
	if email is None:
		# Assume that it's an "@yahoo.com" address
		email = yahoo_id + '@yahoo.com'
	
	return backend.util_get_uuid_from_email(email)
