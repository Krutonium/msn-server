from typing import FrozenSet, Any, Iterable, Optional, TypeVar, List, Dict
from abc import ABCMeta, abstractmethod
import asyncio
import functools
import itertools
import traceback
from uuid import uuid4
import ssl
import jinja2
from aiohttp import web

EMPTY_SET: FrozenSet[Any] = frozenset()

def gen_uuid() -> str:
	return str(uuid4())

T = TypeVar('T')
def first_in_iterable(iterable: Iterable[T]) -> Optional[T]:
	for x in iterable: return x
	return None

class Runner(metaclass = ABCMeta):
	__slots__ = ('host', 'port', 'ssl_context')
	
	host: str
	port: int
	ssl_context: Optional[ssl.SSLContext]
	
	def __init__(self, host: str, port: int, *, ssl_context: Optional[ssl.SSLContext] = None) -> None:
		self.host = host
		self.port = port
		self.ssl_context = ssl_context
	
	@abstractmethod
	def create_servers(self, loop: asyncio.AbstractEventLoop) -> List[Any]: pass
	
	def teardown(self, loop: asyncio.AbstractEventLoop) -> Any:
		pass

class ProtocolRunner(Runner):
	__slots__ = ('_protocol')
	
	_protocol: Any
	
	def __init__(self, host: str, port: int, protocol, *, args: Optional[List[Any]] = None, ssl_context: Optional[ssl.SSLContext] = None) -> None:
		super().__init__(host, port, ssl_context = ssl_context)
		if args:
			protocol = functools.partial(protocol, *args)
		self._protocol = protocol
	
	def create_servers(self, loop: asyncio.AbstractEventLoop) -> List[Any]:
		return [loop.create_server(self._protocol, self.host, self.port, ssl = self.ssl_context)]

class AIOHTTPRunner(Runner):
	__slots__ = ('app', '_handler')
	
	app: Any
	_handler: Optional[Any]
	
	def __init__(self, host: str, port: int, app: Any, *, ssl_context: Optional[ssl.SSLContext] = None) -> None:
		super().__init__(host, port, ssl_context = ssl_context)
		self.app = app
		self._handler = None
	
	def create_servers(self, loop: asyncio.AbstractEventLoop) -> List[Any]:
		assert self._handler is None
		self._handler = self.app.make_handler(loop = loop)
		loop.run_until_complete(self.app.startup())
		
		ret = [loop.create_server(self._handler, self.host, self.port, ssl = None)]
		if self.ssl_context is not None:
			ret.append(loop.create_server(self._handler, self.host, 443, ssl = self.ssl_context))
		return ret
	
	def teardown(self, loop: asyncio.AbstractEventLoop) -> None:
		handler = self._handler
		assert handler is not None
		self._handler = None
		loop.run_until_complete(self.app.shutdown())
		loop.run_until_complete(handler.shutdown(60))
		loop.run_until_complete(self.app.cleanup())

class Logger:
	__slots__ = ('prefix', '_log')
	
	prefix: str
	_log: bool
	
	def __init__(self, prefix: str, obj: object) -> None:
		import settings
		self.prefix = '{}/{:04x}'.format(prefix, hash(obj) % 0xFFFF)
		self._log = settings.DEBUG and settings.DEBUG_MSNP
	
	def info(self, *args: Any) -> None:
		if self._log:
			print(self.prefix, *args)
	
	def error(self, exc: Exception) -> None:
		traceback.print_exception(type(exc), exc, exc.__traceback__)
	
	def log_connect(self) -> None:
		self.info("con")
	
	def log_disconnect(self) -> None:
		self.info("dis")

def run_loop(loop: asyncio.AbstractEventLoop, runners: List[Runner]) -> None:
	for runner in runners:
		print("Serving on {}:{}".format(runner.host, runner.port))
	
	task = loop.create_task(_windows_ctrl_c_workaround())
	
	foos = itertools.chain(*(
		runner.create_servers(loop) for runner in runners
	))
	servers = loop.run_until_complete(asyncio.gather(*foos))
	
	try:
		loop.run_forever()
	except KeyboardInterrupt:
		# To prevent "Task exception was never retrieved"
		if task.done():
			task.exception()
		raise
	finally:
		for server in servers:
			server.close()
		loop.run_until_complete(asyncio.gather(*(
			server.wait_closed() for server in servers
		)))
		for runner in runners:
			runner.teardown(loop)
		loop.close()

async def _windows_ctrl_c_workaround() -> None:
	import os
	if os.name != 'nt': return
	
	# https://bugs.python.org/issue23057
	while True:
		await asyncio.sleep(0.1)

def add_to_jinja_env(app: web.Application, prefix: str, tmpl_dir: str, *, globals: Optional[Dict[str, Any]] = None) -> None:
	jinja_env = app['jinja_env']
	jinja_env.loader.mapping[prefix] = jinja2.FileSystemLoader(tmpl_dir)
	if globals:
		jinja_env.globals.update(globals)

K = TypeVar('K')
V = TypeVar('V')
class DefaultDict(Dict[K, V]):
	_default: V
	
	def __init__(self, default: V, mapping: Dict[K, V]) -> None:
		super().__init__(mapping)
		self._default = default
	
	def __getitem__(self, key: K) -> V:
		v = super().__getitem__(key)
		if v is None:
			v = self._default
		return v
