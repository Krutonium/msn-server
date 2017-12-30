import secrets

from util.misc import Logger

from core import event
from core.backend import Backend, BackendSession, Chat
from core.models import Substatus, Lst, User, Contact
from core.client import Client

from .ymsg import YMSGCtrlBase

class YMSGService:
	LogOn = 0x01
	LogOff = 0x02
	Verify = 0x4c
	AuthResp = 0x54
	Auth = 0x57

class YMSGCtrlPager(YMSGCtrlBase):
	__slots__ = ('backend', 'dialect', 'usr_email', 'bs', 'client', 'syn_ser', 'iln_sent')
	
	backend: Backend
	dialect: int
	usr_email: Optional[str]
	sess_id: int
	init_client_id: int
	challenge: Optional[str]
	bs: Optional[BackendSession]
	client: Client
	syn_ser: int
	iln_sent: bool
	
	def __init__(self, logger: Logger, via: str, backend: Backend) -> None:
		super().__init__(logger)
		self.backend = backend
		self.dialect = 0
		self.usr_email = None
		self.sess_id = 0
		self.init_client_id = 0
		self.challenge = None
		self.bs = None
		self.client = Client('yahoo', '?', via)
		self.syn_ser = 0
		self.iln_sent = False
	
	def _on_close(self) -> None:
		if self.bs:
			self.bs.close()
	
	def _y_004c(self, *args) -> None:
	    self.client = Client('yahoo', 'YMSG' + str(args[0]), self.client.via)
	    self.dialect = int(args[0])
	    self.send_reply(YMSGService.Verify, args[2], 0, None)
	
	def _y_0057(self, *args):
	    self.usr_email = args[4][1]
	    # Keep session ID in variable until login is complete; then transfer to Backend Session
	    self.sess_id = secrets.randbelow(89999999) + 10000000