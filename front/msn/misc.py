from typing import Optional, Tuple, Any, Iterable
from urllib.parse import quote
from util.misc import first_in_iterable

from core import error
from core.backend import Backend
from core.models import Contact

def build_msnp_presence_notif(trid: Optional[str], ctc: Contact, dialect: int, backend: Backend) -> Iterable[Tuple[Any, ...]]:
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
	
	if dialect >= 18:
		yield (*frst, status.substatus.name, encode_email_networkid(head.email, networkid), status.name, *rst)
	else:
		yield (*frst, status.substatus.name, head.email, networkid, status.name, *rst)
	
	if dialect < 11:
		return
	
	ubx_payload = '<Data><PSM>{}</PSM><CurrentMedia>{}</CurrentMedia></Data>'.format(
		status.message or '', status.media or ''
	).encode('utf-8')
	
	if dialect >= 18:
		yield ('UBX', encode_email_networkid(head.email, networkid), ubx_payload)
	elif dialect >= 11:
		yield ('UBX', head.email, networkid, ubx_payload)

def encode_email_networkid(email, networkid):
	return '{}:{}'.format(networkid or 1, email)

def encode_msnobj(msnobj):
	if msnobj is None: return None
	return quote(msnobj, safe = '')

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
	def GetCodeForException(cls, exc):
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
