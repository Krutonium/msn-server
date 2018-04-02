from typing import Tuple, Optional, Iterable, List, Any, Callable, Dict
import io
import asyncio
from enum import IntEnum

from util.misc import Logger

from core import event
from core.models import Contact, Substatus, User, TextWithData, MessageData, MessageType, Substatus, LoginOption
from core.backend import Backend, BackendSession, Chat, ChatSession
from core.client import Client

class IRCCtrl:
	__slots__ = (
		'logger', 'reader', 'writer', 'peername', 'close_callback', 'closed', 'transport',
		'backend', 'bs', 'client',
		'password', 'username', 'chat_sessions'
	)
	
	logger: Logger
	reader: 'IRCReader'
	writer: 'IRCWriter'
	peername: Tuple[str, int]
	close_callback: Optional[Callable[[], None]]
	closed: bool
	transport: Optional[asyncio.WriteTransport]
	backend: Backend
	bs: Optional[BackendSession]
	client: Client
	password: Optional[str]
	username: Optional[str]
	chat_sessions: Dict[Chat, ChatSession]
	
	def __init__(self, logger: Logger, via: str, backend: Backend) -> None:
		self.logger = logger
		self.reader = IRCReader(logger)
		self.writer = IRCWriter(logger)
		self.peername = ('0.0.0.0', 6667)
		self.close_callback = None
		self.closed = False
		self.transport = None
		
		self.backend = backend
		self.bs = None
		self.client = Client('irc', '?', via)
		self.password = None
		self.username = None
		self.chat_sessions = {}
	
	def _m_pass(self, pwd: str) -> None:
		self.password = pwd
	
	def _m_user(self, email: str, junk1: str, junk2: str, realname: str) -> None:
		password = self.password
		self.password = None
		assert password is not None
		uuid = self.backend.user_service.login(email, password)
		if uuid is not None:
			bs = self.backend.login(uuid, self.client, BackendEventHandler(self), LoginOption.BootOthers)
		else:
			bs = None
		if bs is None:
			self.send_numeric(Err.PasswdMismatch, ':Wrong email/password')
			return
		self.bs = bs
		
		self.bs.me_update({ 'substatus': Substatus.Online })
		
		self.send_numeric(RPL.Welcome, email, ':Log on successful.')
	
	def _m_join(self, channel: str, keys: Optional[str] = None) -> None:
		assert self.bs is not None
		email = self.bs.user.email
		
		chat = self._channel_to_chat(channel)
		if chat is None:
			chat = self.backend.chat_create()
			chat.add_id('irc', channel)
		cs = self._channel_to_chatsession(channel)
		if cs is None:
			cs = chat.join('irc', self.bs, ChatEventHandler(self))
			chat.send_participant_joined(cs)
			self.chat_sessions[chat] = cs
		
		self.send_numeric(RPL.NamReply, email, '=', channel, ':' + ' '.join(
			cs.user.email for cs in chat.get_roster()
		))
		
		# TODO: Chats created in other frontends are usually secret+private.
		#self.send_numeric(Err.InviteOnlyChan, email, channel, ":Cannot join channel")
	
	def _m_invite(self, user: str, channel: str) -> None:
		assert self.bs is not None
		cs = self._channel_to_chatsession(channel)
		assert cs is not None
		uuid = self.backend.util_get_uuid_from_email(user)
		assert uuid is not None
		cs.invite(uuid)
		self.send_numeric(RPL.Inviting, self.bs.user.email, user, channel)
	
	def _m_mode(self, channel: str) -> None:
		#self.send_numeric(RPL.ChannelModeIs, self.bs.user.email, channel, '+tnl', 200)
		pass
	
	def _m_who(self, channel: str) -> None:
		assert self.bs is not None
		cs = self._channel_to_chatsession(channel)
		assert cs is not None
		for cs_other in cs.chat.get_roster():
			email = cs_other.user.email
			name = cs_other.user.status.name or email
			self.send_numeric(RPL.WhoReply, self.bs.user.email, channel, email, 'host', 'server', email, 'H', ':0 0PNE ' + name)
	
	def _m_part(self, channel: str, message: Optional[str] = None) -> None:
		assert self.bs is not None
		cs = self._channel_to_chatsession(channel)
		assert cs is not None
		cs.close()
		self.send_reply('PART', channel, source = self.bs.user.email)
	
	def _m_privmsg(self, dest: str, message: str) -> None:
		assert self.bs is not None
		cs = self._channel_to_chatsession(dest)
		assert cs is not None
		cs.send_message_to_everyone(MessageData(sender = self.bs.user, type = MessageType.Chat, text = message))
	
	def _m_quit(self, reason: Optional[str]) -> None:
		self.close()
	
	def _m_cap(self, subcommand: str, capabilities: Optional[str] = None) -> None:
		self._reply_unsupported('CAP')
	
	def _m_nick(self, nickname: str) -> None:
		if self.bs is None: return
		self._reply_unsupported('NICK')
	
	def _reply_unsupported(self, cmd: str) -> None:
		self.send_numeric(Err.UnknownCommand, cmd, ":Not supported")
	
	def _channel_to_chatsession(self, channel: str) -> Optional[ChatSession]:
		chat = self._channel_to_chat(channel)
		assert chat is not None
		return self.chat_sessions.get(chat)
	
	def _channel_to_chat(self, channel: str) -> Optional[Chat]:
		return self.backend.chat_get('irc', channel)
	
	def data_received(self, transport: asyncio.BaseTransport, data: bytes) -> None:
		self.peername = transport.get_extra_info('peername')
		for m in self.reader.data_received(data):
			try:
				f = getattr(self, '_m_{}'.format(m[0].lower()))
				f(*m[1:])
			except Exception as ex:
				self.logger.error(ex)
	
	def send_numeric(self, n: int, *m: str, source: Optional[str] = None) -> None:
		self.send_reply('{:03}'.format(n), *m, source = source)
	
	def send_reply(self, *m: str, source: Optional[str] = None) -> None:
		if source is None:
			source = 'localhost'
		self.writer.write((':' + source,) + m)
		transport = self.transport
		if transport is not None:
			transport.write(self.flush())
	
	def flush(self) -> bytes:
		return self.writer.flush()
	
	def close(self) -> None:
		if self.closed: return
		self.closed = True
		if self.close_callback:
			self.close_callback()

class BackendEventHandler(event.BackendEventHandler):
	__slots__ = ('ctrl', 'bs')
	
	ctrl: IRCCtrl
	bs: BackendSession
	
	def __init__(self, ctrl: IRCCtrl) -> None:
		self.ctrl = ctrl
	
	def on_presence_notification(self, contact: Contact, old_substatus: Substatus) -> None:
		self.ctrl.send_reply('NOTICE', ":{} is now {}".format(contact.head.email, contact.status.substatus))
	
	def on_chat_invite(self, chat: Chat, inviter: User, *, invite_msg: Optional[str] = None, roster: Optional[List[str]] = None, voice_chat: Optional[int] = None, existing: bool = False) -> None:
		self.ctrl.send_reply('INVITE', self.bs.user.email, chat.ids['irc'], source = inviter.email)
	
	def on_added_me(self, user: User, *, message: Optional[TextWithData] = None) -> None:
		self.ctrl.send_reply('NOTICE', ":{} added you to their friend list".format(user.email), source = user.email)
		if message:
			self.ctrl.send_reply('NOTICE', ":\"{}\"".format(message), source = user.email)
	
	def on_contact_request_denied(self, user: User, message: Optional[str]) -> None:
		self.ctrl.send_reply('NOTICE', ":{} declined your friend request".format(user.email), source = user.email)
		if message:
			self.ctrl.send_reply('NOTICE', ":\"{}\"".format(message), source = user.email)
	
	def on_login_elsewhere(self, option: LoginOption) -> None:
		if option is LoginOption.BootOthers:
			self.ctrl.send_reply('NOTICE', ":You are being booted because your account is used elsewhere.")
		else:
			self.ctrl.send_reply('NOTICE', ":Your account is being used elsewhere.")
	
	def on_close(self) -> None:
		self.ctrl.close()

class ChatEventHandler(event.ChatEventHandler):
	__slots__ = ('ctrl', 'cs')
	
	ctrl: IRCCtrl
	cs: ChatSession
	
	def __init__(self, ctrl: IRCCtrl) -> None:
		self.ctrl = ctrl
	
	def on_close(self) -> None:
		self.ctrl.chat_sessions.pop(self.cs.chat, None)
	
	def on_participant_joined(self, cs_other: ChatSession) -> None:
		self.ctrl.send_reply('JOIN', self.cs.chat.ids['irc'], source = cs_other.user.email)
	
	def on_participant_left(self, cs_other: ChatSession) -> None:
		self.ctrl.send_reply('PART', self.cs.chat.ids['irc'], source = cs_other.user.email)
	
	def on_invite_declined(self, invited_user: User, *, message: Optional[str] = None) -> None:
		self.ctrl.send_reply('NOTICE', ":{} declined the invitation".format(invited_user.email), source = invited_user.email)
		if message:
			self.ctrl.send_reply('NOTICE', ":\"{}\"".format(message), source = invited_user.email)
	
	def on_message(self, data: MessageData) -> None:
		if data.type is not MessageType.Chat:
			return
		if data.text is None:
			return
		self.ctrl.send_reply('PRIVMSG', self.cs.chat.ids['irc'], ':' + data.text, source = data.sender.email)

class IRCReader:
	__slots__ = ('_logger', '_data')
	
	_logger: Logger
	_data: bytes
	
	def __init__(self, logger: Logger) -> None:
		self._logger = logger
		self._data = b''
	
	def data_received(self, data: bytes) -> Iterable[List[str]]:
		if self._data:
			self._data += data
		else:
			self._data = data
		while self._data:
			m = self._read()
			if m is None: break
			self._logger.info('>>>', *m)
			yield m
	
	def _read(self) -> Optional[List[str]]:
		try:
			i = self._data.index(b'\r\n')
		except IndexError:
			return None
		chunk = self._data[:i].decode('utf-8')
		self._data = self._data[i+2:]
		
		# TODO: Support @foo :bar prefixes
		
		toks = []
		while True:
			chunk = chunk.lstrip(' ')
			if chunk[:1] == ':':
				toks.append(chunk[1:])
				break
			k = chunk.find(' ')
			if k < 0:
				tok = chunk
			else:
				tok = chunk[:k]
				chunk = chunk[k:]
			if tok:
				toks.append(tok)
			if k < 0:
				break
		return toks

class IRCWriter:
	__slots__ = ('_logger', '_buf')
	
	_logger: Logger
	_buf: io.BytesIO
	
	def __init__(self, logger: Logger) -> None:
		self._logger = logger
		self._buf = io.BytesIO()
	
	def write(self, m: Iterable[Any]) -> None:
		self._logger.info('<<<', *m)
		self._buf.write(' '.join(map(str, m)).encode('utf-8'))
		self._buf.write(b'\r\n')
	
	def flush(self) -> bytes:
		data = self._buf.getvalue()
		if data:
			self._buf = io.BytesIO()
		return data

class Err(IntEnum):
	UnknownError = 400
	UnknownCommand = 421
	PasswdMismatch = 464
	InviteOnlyChan = 473

class RPL(IntEnum):
	Welcome = 1
	NamReply = 353
	ChannelModeIs = 324
	Inviting = 341
	WhoReply = 352
