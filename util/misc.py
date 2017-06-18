from uuid import uuid4
from settings import DEBUG

def gen_uuid():
	return str(uuid4())

class AIOHTTPRunner:
	def __init__(self, app):
		self.app = app
		self.handler = None
	
	def setup(self):
		import asyncio
		from aiohttp.log import access_logger
		loop = asyncio.get_event_loop()
		self.handler = self.app.make_handler(loop = loop, access_log = access_logger)
		loop.run_until_complete(self.app.startup())
		return self.handler
	
	def teardown(self, loop, shutdown_timeout = 60):
		loop.run_until_complete(self.app.shutdown())
		loop.run_until_complete(self.handler.shutdown(shutdown_timeout))
		loop.run_until_complete(self.app.cleanup())

class Logger:
	def __init__(self, prefix):
		self.prefix = prefix
		self.transport = None
	
	def info(self, *args):
		if DEBUG:
			print(self._name(), *args)
	
	def log_connect(self, transport):
		self.transport = transport
		self.info("con")
	
	def log_disconnect(self):
		self.info("dis")
		self.transport = None
	
	def _name(self):
		name = ''
		if self.transport:
			(_, port) = self.transport.get_extra_info('peername')
			name += '{}'.format(port)
		name += ' ' + self.prefix
		return name
