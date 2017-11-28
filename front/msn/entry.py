import asyncio
from functools import partial

from core.session import PersistentSession
from util.misc import AIOHTTPRunner, Logger

from .msnp import MSNPReader, MSNPWriter, MSNP_NS_SessState, MSNP_SB_SessState

def register(loop, backend):
	import settings
	from .http import create_app
	
	coros = [
		loop.create_server(AIOHTTPRunner(create_app(backend)).setup(), '0.0.0.0', 80),
		loop.create_server(partial(ListenerMSNP, 'NS', backend, MSNP_NS_SessState), '0.0.0.0', 1863),
		loop.create_server(partial(ListenerMSNP, 'SB', backend, MSNP_SB_SessState), '0.0.0.0', 1864),
	]
	
	if settings.DEBUG:
		from dev import autossl
		coros.append(loop.create_server(AIOHTTPRunner(create_app(backend)).setup(), '0.0.0.0', 443, ssl = autossl.create_context()))
	
	servers = loop.run_until_complete(asyncio.gather(*coros))
	for server in servers:
		print("Serving on {}".format(server.sockets[0].getsockname()))

class ListenerMSNP(asyncio.Protocol):
	def __init__(self, logger_prefix, backend, sess_state_factory):
		super().__init__()
		self.logger_prefix = logger_prefix
		self.backend = backend
		self.sess_state_factory = sess_state_factory
		self.transport = None
		self.logger = None
		self.sess = None
	
	def connection_made(self, transport):
		self.transport = transport
		self.logger = Logger(self.logger_prefix)
		sess_state = self.sess_state_factory(MSNPReader(self.logger), self.backend)
		self.sess = PersistentSession(sess_state, MSNPWriter(self.logger, sess_state), transport)
		self.logger.log_connect()
	
	def connection_lost(self, exc):
		self.logger.log_disconnect()
		self.sess.close()
		self.sess = None
		self.logger = None
		self.transport = None
	
	def data_received(self, data):
		self.sess.data_received(data)
