from urllib.parse import quote
from util.misc import first_in_iterable

from core import error

class MSNPHandlers:
	def __init__(self):
		self._map = { 'OUT': _m_out }
	
	def apply(self, msg, sess):
		handler = self._map.get(msg[0])
		if handler:
			handler(sess, *msg[1:])
	
	def __call__(self, f):
		msg = f.__name__[3:].upper()
		assert len(msg) == 3, "All MSNP command names are 3 characters long"
		self._map[msg] = f

def _m_out(sess):
	sess.send_reply('OUT')
	sess.close()

def build_msnp_presence_notif(trid, ctc, dialect, backend):
	status = ctc.status
	is_offlineish = status.is_offlineish()
	if is_offlineish and trid is not None:
		return
	head = ctc.head
	
	if dialect >= 14:
		networkid = 1
	else:
		networkid = None
	
	if is_offlineish:
		yield ('FLN', head.email, networkid)
		return
	
	if trid: frst = ('ILN', trid)
	else: frst = ('NLN',)
	rst = []
	ctc_sess = first_in_iterable(backend.util_get_sessions_by_user(head))
	if dialect >= 8:
		rst.append(ctc_sess.state.capabilities)
	if dialect >= 9:
		rst.append(encode_msnobj(ctc_sess.state.msnobj or '<msnobj/>'))
	
	yield (*frst, status.substatus.name, head.email, networkid, status.name, *rst)
	
	if dialect >= 11:
		yield ('UBX', head.email, networkid, '<Data><PSM>{}</PSM><CurrentMedia>{}</CurrentMedia></Data>'.format(
			status.message or '', status.media or ''
		).encode('utf-8'))

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
		return cls.InternalServerError

