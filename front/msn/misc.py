from typing import Optional, Tuple, Any, Iterable, ClassVar, Dict
from urllib.parse import quote
from enum import Enum

from util.misc import first_in_iterable, DefaultDict

from core import error
from core.backend import Backend
from core.models import User, Contact, Substatus

def build_presence_notif(trid: Optional[str], ctc: Contact, dialect: int, backend: Backend) -> Iterable[Tuple[Any, ...]]:
	status = ctc.status
	is_offlineish = status.is_offlineish()
	if is_offlineish and trid is not None:
		return
	head = ctc.head
	
	networkid = None # type: Optional[int]
	if dialect >= 14:
		networkid = 1
	
	if is_offlineish:
		if dialect >= 18:
			yield ('FLN', '{}:{}'.format(networkid, head.email))
		else:
			yield ('FLN', head.email, networkid)
		return
	
	if trid: frst = ('ILN', trid) # type: Tuple[Any, ...]
	else: frst = ('NLN',)
	rst = []
	ctc_sess = first_in_iterable(backend.util_get_sessions_by_user(head))
	assert ctc_sess is not None
	
	if dialect >= 8:
		rst.append(ctc_sess.front_data.get('msn_capabilities') or 0)
	if dialect >= 9:
		rst.append(encode_msnobj(ctc_sess.front_data.get('msn_msnobj') or '<msnobj/>'))
	
	msn_status = MSNStatus.FromSubstatus(status.substatus)
	
	if dialect >= 18:
		yield (*frst, msn_status.name, encode_email_networkid(head.email, networkid), status.name, *rst)
	else:
		yield (*frst, msn_status.name, head.email, networkid, status.name, *rst)
	
	if dialect < 11:
		return
	
	ubx_payload = '<Data><PSM>{}</PSM><CurrentMedia>{}</CurrentMedia></Data>'.format(
		status.message or '', status.media or ''
	).encode('utf-8')
	
	if dialect >= 18:
		yield ('UBX', encode_email_networkid(head.email, networkid), ubx_payload)
	elif dialect >= 11:
		yield ('UBX', head.email, networkid, ubx_payload)

def encode_email_networkid(email: str, networkid: Optional[int]) -> str:
	return '{}:{}'.format(networkid or 1, email)

def encode_msnobj(msnobj: Optional[str]) -> Optional[str]:
	if msnobj is None: return None
	return quote(msnobj, safe = '')

def gen_mail_data(user: User, backend: Backend, *, oim_uuid: Optional[str] = None, just_sent: bool = False, on_ns: bool = True, e_node: bool = True, q_node: bool = True) -> str:
	md_m_pl = ''
	if just_sent:
		oim_collection = backend.user_service.msn_get_oim_single(user.email, oim_uuid or '')
	else:
		oim_collection = backend.user_service.msn_get_oim_batch(user.email)
	if on_ns and len(oim_collection) > 25: return 'too-large'
	
	for oim in oim_collection:
		md_m_pl += M_MAIL_DATA_PAYLOAD.format(
			rt = (RT_M_MAIL_DATA_PAYLOAD.format(
				senttime = (oim.last_oim_sent.isoformat()[:19] + 'Z')
			) if not just_sent else ''), oimsz = oim.oim_content_length,
			frommember = oim.from_member_name, guid = oim.run_id, fid = ('00000000-0000-0000-0000-000000000009' if not just_sent else '.!!OIM'),
			fromfriendly = (oim.from_member_friendly if not just_sent else _format_friendly(oim.from_member_friendly)),
			su = (SU_M_MAIL_DATA_PAYLOAD if just_sent else ''),
		)
	
	return MAIL_DATA_PAYLOAD.format(
		e = (E_MAIL_DATA_PAYLOAD if e_node else ''),
		q = (Q_MAIL_DATA_PAYLOAD if q_node else ''),
		m = md_m_pl,
	)

def _format_friendly(friendlyname: str) -> str:
	friendly_parts = friendlyname.split('?')
	friendly_parts[3] += ' '
	return '?'.join(friendly_parts)

MAIL_DATA_PAYLOAD = '''<MD>{e}{q}{m}</MD>'''

E_MAIL_DATA_PAYLOAD = '''<E><I>0</I><IU>0</IU><O>0</O><OU>0</OU></E>'''

Q_MAIL_DATA_PAYLOAD = '''<Q><QTM>409600</QTM><QNM>204800</QNM></Q>'''

M_MAIL_DATA_PAYLOAD = '''<M><T>11</T><S>6</S>{rt}<RS>0</RS><SZ>{oimsz}</SZ><E>{frommember}</E><I>{guid}</I><F>{fid}</F><N>{fromfriendly}</N></M>{su}'''

RT_M_MAIL_DATA_PAYLOAD = '''<RT>{senttime}</RT>'''

SU_M_MAIL_DATA_PAYLOAD = '''<SU> </SU>'''

class MSNStatus(Enum):
	FLN = object()
	NLN = object()
	BSY = object()
	IDL = object()
	BRB = object()
	AWY = object()
	PHN = object()
	LUN = object()
	HDN = object()
	
	@classmethod
	def ToSubstatus(cls, msn_status: 'MSNStatus') -> Substatus:
		return _ToSubstatus[msn_status]
	
	@classmethod
	def FromSubstatus(cls, substatus: 'Substatus') -> 'MSNStatus':
		return _FromSubstatus[substatus]

_ToSubstatus = DefaultDict(Substatus.Busy, {
	MSNStatus.FLN: Substatus.Offline,
	MSNStatus.NLN: Substatus.Online,
	MSNStatus.BSY: Substatus.Busy,
	MSNStatus.IDL: Substatus.Idle,
	MSNStatus.BRB: Substatus.BRB,
	MSNStatus.AWY: Substatus.Away,
	MSNStatus.PHN: Substatus.OnPhone,
	MSNStatus.LUN: Substatus.OutToLunch,
	MSNStatus.HDN: Substatus.Invisible,
})
_FromSubstatus = DefaultDict(MSNStatus.BSY, {
	Substatus.Offline: MSNStatus.FLN,
	Substatus.Online: MSNStatus.NLN,
	Substatus.Busy: MSNStatus.BSY,
	Substatus.Idle: MSNStatus.IDL,
	Substatus.BRB: MSNStatus.BRB,
	Substatus.Away: MSNStatus.AWY,
	Substatus.OnPhone: MSNStatus.PHN,
	Substatus.OutToLunch: MSNStatus.LUN,
	Substatus.Invisible: MSNStatus.HDN,
	Substatus.NotAtHome: MSNStatus.AWY,
	Substatus.NotAtDesk: MSNStatus.BRB,
	Substatus.NotInOffice: MSNStatus.AWY,
	Substatus.OnVacation: MSNStatus.AWY,
	Substatus.SteppedOut: MSNStatus.BRB,
})

class Err:
	InvalidParameter = 201
	InvalidPrincipal = 205
	InvalidUser = 207
	PrincipalOnList = 215
	PrincipalNotOnList = 216
	PrincipalNotOnline = 217
	GroupInvalid = 224
	PrincipalNotInGroup = 225
	GroupNameTooLong = 229
	GroupZeroUnremovable = 230
	InternalServerError = 500
	CommandDisabled = 502
	AuthFail = 911
	
	@classmethod
	def GetCodeForException(cls, exc: Exception) -> int:
		if isinstance(exc, error.GroupNameTooLong):
			return cls.GroupNameTooLong
		if isinstance(exc, error.GroupDoesNotExist):
			return cls.GroupInvalid
		if isinstance(exc, error.CannotRemoveSpecialGroup):
			return cls.GroupZeroUnremovable
		if isinstance(exc, error.ContactDoesNotExist):
			return cls.InvalidPrincipal
		if isinstance(exc, error.ContactAlreadyOnList):
			return cls.PrincipalOnList
		if isinstance(exc, error.ContactNotOnList):
			return cls.PrincipalNotOnList
		if isinstance(exc, error.UserDoesNotExist):
			return cls.InvalidUser
		if isinstance(exc, error.ContactNotOnline):
			return cls.PrincipalNotOnline
		if isinstance(exc, error.AuthFail):
			return cls.AuthFail
		raise ValueError("Exception not convertible to MSNP error") from exc

class NetworkID:
	WINDOWS_LIVE = 0x01
	OFFICE_COMMUNICATOR = 0x02
	TELEPHONE = 0x04
	MNI = 0x08 # Mobile Network Interop, used by Vodafone
	SMTP = 0x10 # Jaguire, Japanese mobile interop
	YAHOO = 0x20
