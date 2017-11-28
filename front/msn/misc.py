from urllib.parse import quote

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

def build_msnp_presence_notif(trid, ctc, dialect):
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
	if dialect >= 8:
		rst.append(head.detail.capabilities)
	if dialect >= 9:
		rst.append(encode_msnobj(head.detail.msnobj or '<msnobj/>'))
	
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
		# TODO: GetCodeForException
		raise NotImplementedError
