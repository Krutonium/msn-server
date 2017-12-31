from typing import Optional, Callable

import asyncio
import struct

from core.backend import Backend
from util.misc import Logger

from .ymsg import YMSGCtrlBase

def register(loop, backend):
	from util.misc import ProtocolRunner
	from .pager import YMSGCtrlPager
	
	backend.add_runner(ProtocolRunner('0.0.0.0', 5050, ListenerYMSG, args = ['YH', backend, YMSGCtrlPager]))

# TODO: Fix weird inconsistent indentation
class ListenerYMSG(asyncio.Protocol):
    logger: Logger
    backend: Backend
    controller: YMSGCtrlBase
    transport: Optional[asyncio.WriteTransport]
    
	def __init__(self, logger_prefix: str, backend: Backend, controller_factory: Callable[[Logger, str, Backend], YMSGCtrlBase]) -> None:
		super().__init__()
		self.logger = Logger(logger_prefix, self)
		self.backend = backend
		self.controller = controller_factory(self.logger, 'direct', backend)
		self.controller.close_callback = self._on_close
		self.transport = None
    
	def connection_made(self, transport: asyncio.BaseTrasport) -> None:
		assert isinstance(transport, asyncio.WriteTransport)
		self.transport = transport
		self.logger.log_connect()
	
	def connection_lost(self, exc: Exception) -> None:
		self.controller.close()
		self.logger.log_disconnect()
		self.transport = None
	
	def data_received(self, data: bytes) -> None:
		transport = self.transport
		assert transport is not None
		self.controller.transport = None
		self.controller.data_received(transport, data)
		transport.write(self.controller.flush())
		self.controller.transport = transport
		
		# TODO: Move this chunk of code to it's own library
		
		# Verify and Auth service functions were moved to "pager.py"
		if service == YMSGService.AuthResp:
			session_id = 1239999999
			email = kvs[0]
			if kvs[1] != email and kvs[2] != "1":
				print('auth_resp failed')
				self.transport.write(_encode_ymsg(YMSGService.LogOff, 0, 0))
				self.transport.close()
			resp_6 = kvs[6]
			resp_96 = kvs[96]
			if version in (9, 10):
				is_resp_correct = verify_challenge_v1(email, self.challenge, resp_6, resp_96)
				if is_resp_correct:
					# Implement friends/cookies packet later
					pass
			
			print(kvs)
			print("session_id", session_id)
		
		self.logger.info("unknown", service)
		self.transport.write(_encode_ymsg(YMSGService.LogOff, 0, 0))
		self.transport.close()
	
	def _on_close(self) -> None:
		if self.transport is None: return
		self.transport.close()

# ymsg encode and decode function moved to "ymsg.py"
# ymsg service class also moved to "pager.py"

