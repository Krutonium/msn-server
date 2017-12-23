from typing import Optional
import asyncio

from core.backend import Backend
from util.misc import Logger

from .msnp import MSNPCtrl

def register(loop, backend, *, http_port = None, devmode = False):
	from util.misc import AIOHTTPRunner, ProtocolRunner
	from .http import create_app
	from .msnp_ns import MSNPCtrlNS
	from .msnp_sb import MSNPCtrlSB
	
	assert http_port, "Please specify `http_port`."
	
	if devmode:
		http_host = '0.0.0.0'
	else:
		http_host = '127.0.0.1'
	
	backend.add_runner(ProtocolRunner('0.0.0.0', 1863, ListenerMSNP, args = ['NS', backend, MSNPCtrlNS]))
	backend.add_runner(ProtocolRunner('0.0.0.0', 1864, ListenerMSNP, args = ['SB', backend, MSNPCtrlSB]))
	backend.add_runner(AIOHTTPRunner(http_host, http_port, create_app(backend)))
	if devmode:
		from dev import autossl
		ssl_context = autossl.create_context()
		backend.add_runner(AIOHTTPRunner(http_host, 443, create_app(backend), ssl = ssl_context))

class ListenerMSNP(asyncio.Protocol):
	logger: Logger
	backend: Backend
	controller: MSNPCtrl
	transport: Optional[asyncio.WriteTransport]
	
	def __init__(self, logger_prefix: str, backend: Backend, controller: MSNPCtrl) -> None:
		super().__init__()
		self.logger = Logger(logger_prefix, self)
		self.backend = backend
		self.controller = controller
		self.transport = None
	
	def connection_made(self, transport: asyncio.BaseTransport) -> None:
		assert isinstance(transport, asyncio.WriteTransport)
		self.transport = transport
		self.logger.log_connect()
	
	def connection_lost(self, exc: Exception) -> None:
		self.logger.log_disconnect()
		self.transport = None
	
	def data_received(self, data: bytes) -> None:
		controller = self.controller
		transport = self.transport
		assert transport is not None
		controller.data_received(data)
		transport.write(controller.flush())
