from typing import Optional

import asyncio

from core.backend import Backend
from util.misc import Logger

def register(loop: asyncio.AbstractEventLoop, backend: Backend) -> None:
	from util.misc import ProtocolRunner
	backend.add_runner(ProtocolRunner('0.0.0.0', 6667, ListenerIRC, args = ['IR', backend]))

class ListenerIRC(asyncio.Protocol):
	logger: Logger
	backend: Backend
	transport: Optional[asyncio.WriteTransport]
	
	def __init__(self, logger_prefix: str, backend: Backend) -> None:
		super().__init__()
		self.logger = Logger(logger_prefix, self)
		self.backend = backend
		self.transport = None
	
	def connection_made(self, transport: asyncio.BaseTransport) -> None:
		assert isinstance(transport, asyncio.WriteTransport)
		self.transport = transport
		self.logger.log_connect()
	
	def connection_lost(self, exc: Exception) -> None:
		#self.controller.close()
		self.logger.log_disconnect()
		self.transport = None
	
	def data_received(self, data: bytes) -> None:
		transport = self.transport
		assert transport is not None
	
	def _on_close(self) -> None:
		if self.transport is None: return
		self.transport.close()
