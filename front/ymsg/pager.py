import secrets

from util.misc import Logger

from core import event
from core.backend import Backend, BackendSession, Chat
from core.models import Substatus, Lst, User, Contact
from core.client import Client
from core.yahoo.YCS import YahooSessionClearing

from .ymsg_ctrl import YMSGCtrlBase

class YMSGService:
	LogOn = 0x01
	LogOff = 0x02
	Verify = 0x4c
	AuthResp = 0x54
	Auth = 0x57

class YMSGCtrlPager(YMSGCtrlBase):
	__slots__ = ('backend', 'dialect', 'usr_email', 'sess_id', 'init_client_id', 'challenge', 'bs', 'client')
	
	backend: Backend
	dialect: int
	usr_email: Optional[str]
	sess_id: int
	init_client_id: int
	challenge: Optional[str]
	bs: Optional[BackendSession]
	sc: Optional[YahooSessionClearing]
	client: Client
	
	def __init__(self, logger: Logger, via: str, backend: Backend) -> None:
		super().__init__(logger)
		self.backend = backend
		self.dialect = 0
		self.usr_email = None
		self.sess_id = 0
		self.init_client_id = 0
		self.challenge = None
		self.bs = None
		self.sc = None
		self.client = Client('yahoo', '?', via)
	
	def _on_close(self) -> None:
		if self.bs:
			self.bs.close()
	
	def _y_004c(self, *args) -> None:
	    self.client = Client('yahoo', 'YMSG' + str(args[0]), self.client.via)
	    self.dialect = int(args[0])
	    self.send_reply(YMSGService.Verify, args[2], 0, None)
	
	def _y_0057(self, *args):
	    self.usr_email = args[4][1]
	    # Generate a 64-bit session ID within a range if 10000000-99999999
	    # Keep session ID in variable until login is complete; then transfer to Backend Session
	    self.sess_id = secrets.randbelow(89999999) + 10000000
	    self.sc = YahooSessionClearing(str(self.sess_id), self.usr_email)
	    
	    auth_dict = {1: self.usr_email}
	    if self.dialect in (9, 10):
	        self.challenge = backend.generate_challenge_v1()
	        auth_dict[94] = self.challenge
	    elif self.dialect in (11,):
	        # Implement V2 challenge string generation later
	        auth_dict[94] = ''
	        auth_dict[13] = '1'
	    
	    self.send_reply(YMSGService.Auth, 1, self.sess_id, auth_dict)
	    