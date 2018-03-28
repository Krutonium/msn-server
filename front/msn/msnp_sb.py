from typing import Tuple, Any, Optional, List, Set

from util.misc import Logger
from core.models import User, MessageData, MessageType
from core.backend import Backend, BackendSession, ChatSession, Chat
from core import event, error
from .misc import Err
from .msnp import MSNPCtrl

class MSNPCtrlSB(MSNPCtrl):
	__slots__ = ('backend', 'dialect', 'bs', 'cs')
	
	backend: Backend
	dialect: int
	bs: Optional[BackendSession]
	cs: Optional[ChatSession]
	
	def __init__(self, logger: Logger, via: str, backend: Backend) -> None:
		super().__init__(logger)
		self.backend = backend
		self.dialect = 0
		self.bs = None
		self.cs = None
	
	def _on_close(self) -> None:
		if self.cs:
			self.cs.close()
	
	# State = Auth
	
	def _m_usr(self, trid: str, arg: str, token: str) -> None:
		#>>> USR trid email@example.com token (MSNP < 18)
		#>>> USR trid email@example.com;{00000000-0000-0000-0000-000000000000} token (MSNP >= 18)
		(email, pop_id) = _decode_email_pop(arg)
		
		data = self.backend.auth_service.pop_token('sb/xfr', token) # type: Optional[Tuple[BackendSession, int]]
		if data is None:
			self.send_reply(Err.AuthFail, trid)
			return
		bs, dialect = data
		if bs.user.email != email:
			self.send_reply(Err.AuthFail, trid)
			return
		chat = self.backend.chat_create()
		
		cs = chat.join('msn', bs, ChatEventHandler(self))
		self.dialect = dialect
		self.bs = bs
		self.cs = cs
		self.send_reply('USR', trid, 'OK', arg, cs.user.status.name)
	
	def _m_ans(self, trid: str, arg: str, token: str, sessid: str) -> None:
		#>>> ANS trid email@example.com token sessionid (MSNP < 18)
		#>>> ANS trid email@example.com;{00000000-0000-0000-0000-000000000000} token sessionid (MSNP >= 18)
		(email, _) = _decode_email_pop(arg)
		
		data = self.backend.auth_service.pop_token('sb/cal', token) # type: Optional[Tuple[BackendSession, int, Chat]]
		if data is None:
			self.send_reply(Err.AuthFail, trid)
			return
		(bs, dialect, chat) = data
		if bs.user.email != email:
			self.send_reply(Err.AuthFail, trid)
			return
		
		cs = chat.join('msn', bs, ChatEventHandler(self))
		self.dialect = dialect
		self.bs = bs
		self.cs = cs
		
		chat.send_participant_joined(cs)
		
		roster_chatsessions = list(chat.get_roster()) # type: List[ChatSession]
		
		if dialect < 18:
			roster_one_per_user = [] # type: List[ChatSession]
			seen_users = { self.cs.user } # type: Set[User]
			for other_cs in roster_chatsessions:
				if other_cs.user in seen_users:
					continue
				seen_users.add(other_cs.user)
				roster_one_per_user.append(other_cs)
			l = len(roster_one_per_user)
			for i, other_cs in enumerate(roster_one_per_user):
				other_user = other_cs.user
				extra = () # type: Tuple[Any, ...]
				if dialect >= 13:
					extra = (other_cs.bs.front_data.get('msn_capabilities') or 0,)
				self.send_reply('IRO', trid, i + 1, l, other_user.email, other_user.status.name, *extra)
		else:
			tmp = [] # type: List[Tuple[ChatSession, Optional[str]]]
			for other_cs in roster_chatsessions:
				tmp.append((other_cs, None))
				pop_id = other_cs.bs.front_data.get('msn_pop_id')
				if pop_id:
					tmp.append((other_cs, pop_id))
			l = len(tmp)
			for i, (other_cs, pop_id) in enumerate(tmp):
				other_user = other_cs.user
				capabilities = other_cs.bs.front_data.get('msn_capabilities') or 0
				email = other_user.email
				if pop_id:
					email = '{};{}'.format(email, pop_id)
				self.send_reply('IRO', trid, i + 1, l, other_user.email, other_user.status.name, capabilities)
		
		self.send_reply('ANS', trid, 'OK')
	
	# State = Live
	
	def _m_cal(self, trid: str, invitee_email: str) -> None:
		#>>> CAL trid email@example.com
		cs = self.cs
		assert cs is not None
		
		invitee_uuid = self.backend.util_get_uuid_from_email(invitee_email)
		if invitee_uuid is None:
			self.send_reply(Err.InvalidUser)
			return
		
		chat = cs.chat
		try:
			cs.invite(invitee_uuid)
		except Exception as ex:
			self.send_reply(Err.GetCodeForException(ex), trid)
		else:
			self.send_reply('CAL', trid, 'RINGING', chat.ids['main'])
	
	def _m_msg(self, trid: str, ack: str, data: bytes) -> None:
		#>>> MSG trid [UNAD] len
		cs = self.cs
		assert cs is not None
		
		cs.send_message_to_everyone(messagedata_from_msnp(cs.user, data))
		
		# TODO: Implement ACK/NAK
		if ack == 'U':
			return
		any_failed = False
		if any_failed: # ADN
			self.send_reply('NAK', trid)
		elif ack != 'N': # AD
			self.send_reply('ACK', trid)

class ChatEventHandler(event.ChatEventHandler):
	__slots__ = ('ctrl',)
	
	ctrl: MSNPCtrlSB
	
	def __init__(self, ctrl: MSNPCtrlSB) -> None:
		self.ctrl = ctrl
	
	def on_participant_joined(self, cs_other: ChatSession) -> None:
		ctrl = self.ctrl
		bs = ctrl.bs
		assert bs is not None
		cs = self.cs
		
		if ctrl.dialect >= 13:
			extra = (bs.front_data.get('msn_capabilities') or 0,) # type: Tuple[Any, ...]
		else:
			extra = ()
		user = cs_other.user
		if ctrl.dialect >= 18 and cs is not cs_other:
			pop_id = bs.front_data.get('msn_pop_id')
			if pop_id is not None:
				assert isinstance(pop_id, int)
				ctrl.send_reply('JOI', '{};{}'.format(user.email, pop_id), user.status.name, *extra)
		ctrl.send_reply('JOI', user.email, user.status.name, *extra)
	
	def on_participant_left(self, cs_other: ChatSession) -> None:
		# TODO: What about PoP?
		# Just sending "BYE" seems to imply ALL PoPs of that email left.
		self.ctrl.send_reply('BYE', cs_other.user.email)
	
	def on_invite_declined(self, invited_user: User, *, message: Optional[str] = None) -> None:
		pass
	
	def on_message(self, data: MessageData) -> None:
		self.ctrl.send_reply('MSG', data.sender.email, data.sender.status.name, messagedata_to_msnp(data))
	
	def on_close(self):
		self.ctrl.close()

def messagedata_from_msnp(sender: User, data: bytes) -> MessageData:
	# TODO: Implement these `Content-Type`s:
	# voice:
	# b'MIME-Version: 1.0\r\nContent-Type: text/x-msmsgsinvite; charset=UTF-8\r\n\r\nInvitation-Command: CANCEL\r\nCancel-Code: TIMEOUT\r\nInvitation-Cookie: 126868552\r\nSession-ID: {CE64F989-2AAD-44C4-A780-2C55A812B0B6}\r\nConn-Type: Firewall\r\nSip-Capability: 1\r\n\r\n'
	# xfer:
	# b'MIME-Version: 1.0\r\nContent-Type: application/x-msnmsgrp2p\r\nP2P-Dest: t2h@hotmail.com\r\n\r\n\x00\x00\x00\x00Gt\xc4\n\x00\x00\x00\x00\x00\x00\x00\x00\xfa\x04\x00\x00\x00\x00\x00\x00\xb2\x04\x00\x00\x00\x00\x00\x00wn\xc5\n\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00INVITE MSNMSGR:t2h@hotmail.com MSNSLP/1.0\r\nTo: <msnmsgr:t2h@hotmail.com>\r\nFrom: <msnmsgr:t1h@hotmail.com>\r\nVia: MSNSLP/1.0/TLP ;branch={CDE28DAF-B67C-4B2D-8186-D3F46EEF0916}\r\nCSeq: 0 \r\nCall-ID: {F87327A8-741F-4FEF-AB63-45D06F51A0C2}\r\nMax-Forwards: 0\r\nContent-Type: application/x-msnmsgr-sessionreqbody\r\nContent-Length: 948\r\n\r\nEUF-GUID: {5D3E02AB-6190-11D3-BBBB-00C04F795683}\r\nSessionID: 180646677\r\nAppID: 2\r\nContext: fgIAAAMAAAAAAAAAAAAAAAEAAABhAC4AdAB4AHQAA...AAAAAAAAA/////wAAAAAAAAAAAAAAAAAAA\x00\x00\x00\x00'
	# b'MIME-Version: 1.0\r\nContent-Type: application/x-msnmsgrp2p\r\nP2P-Dest: t2h@hotmail.com\r\n\r\n\x00\x00\x00\x00Gt\xc4\n\xb2\x04\x00\x00\x00\x00\x00\x00\xfa\x04\x00\x00\x00\x00\x00\x00H\x00\x00\x00\x00\x00\x00\x00wn\xc5\n\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=\r\n\r\n\x00\x00\x00\x00\x00'
	# b'MIME-Version: 1.0\r\nContent-Type: application/x-msnmsgrp2p\r\nP2P-Dest: t1h@hotmail.com\r\n\r\n\x00\x00\x00\x00Wt\xc4\n\x00\x00\x00\x00\x00\x00\x00\x00\xfa\x04\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02\x00\x00\x00Gt\xc4\nwn\xc5\n\xfa\x04\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00'
	# xfer decline:
	# b'MIME-Version: 1.0\r\nContent-Type: application/x-msnmsgrp2p\r\nP2P-Dest: t1h@hotmail.com\r\n\r\n\x00\x00\x00\x00Xt\xc4\n\x00\x00\x00\x00\x00\x00\x00\x00K\x01\x00\x00\x00\x00\x00\x00K\x01\x00\x00\x00\x00\x00\x00N\x0b\xc7\n\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00MSNSLP/1.0 603 Decline\r\nTo: <msnmsgr:t1h@hotmail.com>\r\nFrom: <msnmsgr:t2h@hotmail.com>\r\nVia: MSNSLP/1.0/TLP ;branch={CDE28DAF-B67C-4B2D-8186-D3F46EEF0916}\r\nCSeq: 1 \r\nCall-ID: {F87327A8-741F-4FEF-AB63-45D06F51A0C2}\r\nMax-Forwards: 0\r\nContent-Type: application/x-msnmsgr-sessionreqbody\r\nContent-Length: 25\r\n\r\nSessionID: 180646677\r\n\r\n\x00\x00\x00\x00\x00'
	# b'MIME-Version: 1.0\r\nContent-Type: application/x-msnmsgrp2p\r\nP2P-Dest: t2h@hotmail.com\r\n\r\n\x00\x00\x00\x00Ht\xc4\n\x00\x00\x00\x00\x00\x00\x00\x00K\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02\x00\x00\x00Xt\xc4\nN\x0b\xc7\nK\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00'
	# etc.
	
	s = data.decode('utf-8')
	i = s.index('\r\n\r\n')
	headers = s[:i]
	body = s[i+4:]
	
	if 'text/x-msmsgscontrol' in headers:
		type = MessageType.Typing
		text = ''
	elif 'text/plain' in headers:
		type = MessageType.Chat
		text = body
	else:
		type = MessageType.Chat
		text = "(Unsupported MSNP Content-Type)"
	
	message = MessageData(sender = sender, type = type, text = text)
	message.front_cache['msnp'] = data
	return message

def messagedata_to_msnp(data: MessageData) -> bytes:
	if 'msnp' not in data.front_cache:
		if data.type is MessageType.Typing:
			s = 'MIME-Version: 1.0\r\nContent-Type: text/x-msmsgscontrol\r\nTypingUser: {}\r\n\r\n\r\n'.format(data.sender.email)
		elif data.type is MessageType.Chat:
			s = 'MIME-Version: 1.0\r\nContent-Type: text/plain; charset=UTF-8\r\n\r\n' + (data.text or '')
		else:
			raise ValueError("unknown message type", data.type)
		data.front_cache['msnp'] = s.encode('utf-8')
	return data.front_cache['msnp']

def _decode_email_pop(s: str) -> Tuple[str, Optional[str]]:
	# Split `foo@email.com;{uuid}` into (email, pop_id)
	parts = s.split(';', 1)
	if len(parts) < 2:
		pop_id = None
	else:
		pop_id = parts[1]
	return (parts[0], pop_id)
