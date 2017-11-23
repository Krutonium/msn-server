import asyncio
from functools import partial
from io import BytesIO
from aiohttp import web

from core.session import PersistentSession
from util.misc import AIOHTTPRunner, Logger

from .msnp import MSNPReader, MSNPWriter, MSNP_NS_SessState

class ListenerMSNP_NS(asyncio.Protocol):
	def __init__(self, ns):
		self.ns = ns
		self.transport = None
		self.sess = None
		self.logger = Logger('NS')
	
	def connection_made(self, transport):
		self.transport = transport
		sess_state = MSNP_NS_SessState()
		self.sess = PersistentSession(sess_state, MSNPWriter(self.logger, sess_state), transport)
		self.reader = MSNPReader(self.logger, self.sess, self.ns)
		self.logger.log_connect(transport)
	
	def connection_lost(self, exc):
		self.logger.log_disconnect()
		self.ns.on_connection_lost(self.sess)
		self.transport = None
		self.sess = None
	
	def data_received(self, data):
		sess = self.sess
		for incoming_event in self.reader.data_received(data):
			incoming_event.apply()
		sess.flush()

def register(loop, ns):
	import settings
	from .http import create_app
	
	coros = [
		loop.create_server(AIOHTTPRunner(create_app(ns)).setup(), '0.0.0.0', 80),
		loop.create_server(partial(ListenerMSNP_NS, ns), '0.0.0.0', 1863),
		#loop.create_server(partial(ListenerMSNP_SB, ns), '0.0.0.0', 1864),
	]
	
	if settings.DEBUG:
		from dev import autossl
		coros.append(loop.create_server(AIOHTTPRunner(create_app(ns)).setup(), '0.0.0.0', 443, ssl = autossl.create_context()))
	
	servers = loop.run_until_complete(asyncio.gather(*coros))
	for server in servers:
		print("Serving on {}".format(server.sockets[0].getsockname()))
