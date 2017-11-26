import asyncio
from functools import partial

from core.session import PersistentSession
from util.misc import AIOHTTPRunner, Logger

from .msnp import MSNPReader, MSNPWriter, MSNP_NS_SessState, MSNP_SB_SessState

def register(loop, ns, sb):
	import settings
	from .http import create_app
	
	coros = [
		loop.create_server(AIOHTTPRunner(create_app(ns, sb)).setup(), '0.0.0.0', 80),
		#loop.create_server(partial(ListenerMSNP_NS, ns), '0.0.0.0', 1863),
		#loop.create_server(partial(ListenerMSNP_SB, ns, sb), '0.0.0.0', 1864),
	]
	
	if settings.DEBUG:
		from dev import autossl
		coros.append(loop.create_server(AIOHTTPRunner(create_app(ns, sb)).setup(), '0.0.0.0', 443, ssl = autossl.create_context()))
	
	servers = loop.run_until_complete(asyncio.gather(*coros))
	for server in servers:
		print("Serving on {}".format(server.sockets[0].getsockname()))

class ListenerMSNP(asyncio.Protocol):
	def __init__(self, logger_prefix):
		super().__init__()
		self.logger_prefix = logger_prefix
		self.transport = None
		self.logger = None
		self.sess = None
	
	def connection_made(self, transport):
		self.transport = transport
		self.logger = Logger(self.logger_prefix)
		sess_state = self.create_session_state()
		self.sess = PersistentSession(sess_state, MSNPWriter(self.logger, sess_state), transport)
		self.logger.log_connect()
	
	def connection_lost(self, exc):
		self.logger.log_disconnect()
		self.sess.state.on_connection_lost(self.sess)
		self.sess = None
		self.logger = None
		self.transport = None
	
	def data_received(self, data):
		self.sess.data_received(data)
	
	def create_session_state(self):
		raise NotImplementedError

class ListenerMSNP_NS(ListenerMSNP):
	def __init__(self, ns):
		super().__init__('NS')
		self.ns = ns
	
	def create_session_state(self):
		return MSNP_NS_SessState(MSNPReader(self.logger), self.ns)

class ListenerMSNP_SB(ListenerMSNP):
	def __init__(self, ns, sb):
		super().__init__('SB')
		self.ns = ns
		self.sb = sb
	
	def create_session_state(self):
		return MSNP_SB_SessState(MSNPReader(self.logger), self.ns, self.sb)
