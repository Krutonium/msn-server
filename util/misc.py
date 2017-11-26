import asyncio
from uuid import uuid4
from settings import DEBUG

def gen_uuid():
	return str(uuid4())

class AIOHTTPRunner:
	def __init__(self, app):
		self.app = app
		self.handler = None
	
	def setup(self):
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

def run_loop(loop):
	task = loop.create_task(_windows_ctrl_c_workaround())
	try:
		loop.run_forever()
	except KeyboardInterrupt:
		# To prevent "Task exception was never retrieved"
		if task.done():
			task.exception()
		raise
	finally:
		for task in asyncio.Task.all_tasks():
			task.cancel()
			try: loop.run_until_complete(task)
			except asyncio.CancelledError: pass
		loop.close()

async def _windows_ctrl_c_workaround():
	# https://bugs.python.org/issue23057
	while True:
		await asyncio.sleep(0.1)

def create_jinja_env(tmpl_dir, globals = None):
	import jinja2
	jinja_env = jinja2.Environment(
		loader = jinja2.FileSystemLoader(tmpl_dir),
		autoescape = jinja2.select_autoescape(default = True),
	)
	if globals:
		jinja_env.globals.update(globals)
	return jinja_env
