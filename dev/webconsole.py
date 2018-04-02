from typing import Dict, Set, Tuple, Any, List, Optional
import asyncio
import json
import sys
import io
import cProfile, pstats
import traceback

from aiohttp import web
from aiohttp.web import WSMsgType

import util.misc
from core.backend import Backend
from core.http import render

def register(loop: asyncio.AbstractEventLoop, backend: Backend, http_app: web.Application) -> None:
	util.misc.add_to_jinja_env(http_app, 'dev', 'dev/tmpl')
	
	http_app['webconsole'] = Webconsole(loop, backend)
	
	http_app.router.add_get('/dev', handle_index)
	http_app.router.add_get('/dev/ws', handle_websocket)

async def handle_index(req: web.Request) -> web.Response:
	return render(req, 'dev:index.html', { 'wsurl': 'ws://localhost/dev/ws' })

async def handle_websocket(req: web.Request) -> web.Response:
	webconsole: Webconsole = req.app['webconsole']
	
	ws = web.WebSocketResponse()
	await ws.prepare(req)
	
	webconsole.websocks.add(ws)
	await ws.send_str("Webconsole on.")
	
	async for msg in ws:
		type = msg.type
		data = msg.data
		if type == WSMsgType.ERROR:
			print("ws error with exception {}".format(ws.exception()))
			continue
		if type == WSMsgType.TEXT:
			data = data.strip()
			if data:
				ret = webconsole.run(data)
				if ret:
					await ws.send_str(ret)
			continue
		print("ws unknown", type, data[:30])
	
	webconsole.websocks.remove(ws)
	
	return ws

class Webconsole:
	__slots__ = ('loop', 'locals', 'objs', 'websocks', 'i')
	
	loop: asyncio.AbstractEventLoop
	locals: Dict[str, object]
	objs: Dict[object, str]
	websocks: Set[web.WebSocketResponse]
	i: int
	
	def __init__(self, loop: asyncio.AbstractEventLoop, backend: Backend) -> None:
		self.loop = loop
		self.locals = {
			'_': None,
			'be': backend,
			'dir': useful_dir,
			'dirfull': dir,
			'prof': profile,
		}
		self.objs = {}
		self.websocks = set()
		self.i = 0
		
		backend._dev = self
	
	def run(self, cmd: str) -> Any:
		tmp = io.StringIO()
		sys.stdout = tmp
		try:
			self.locals['_'] = exec(compile(cmd + '\n', '<stdin>', 'single'), None, self.locals)
		except:
			(exctype, excvalue, tb) = sys.exc_info() # type: Tuple[Any, Any, Any]
			ret = '\n'.join(traceback.format_exception(exctype, excvalue, tb))
		else:
			ret = tmp.getvalue()
		finally:
			sys.stdout = sys.__stdout__
		return ret
	
	def connect(self, obj: object) -> None:
		varname = 'k{}'.format(self.i)
		self.i += 1
		self.objs[obj] = varname
		self.locals[varname] = obj
		msg = "# Connect: `{}`".format(varname)
		for ws in self.websocks:
			self.loop.create_task(ws.send_str(msg))
	
	def disconnect(self, obj: object) -> None:
		varname = self.objs.pop(obj)
		msg = "# Disconnect: `{}`".format(varname)
		self.locals.pop(varname, None)
		for ws in self.websocks:
			self.loop.create_task(ws.send_str(msg))

_PROFILE: Optional[cProfile.Profile] = None

def profile(*restrictions: Any) -> None:
	global _PROFILE
	if _PROFILE is None:
		_PROFILE = cProfile.Profile()
		_PROFILE.enable()
		print("Profiling ON")
		return
	_PROFILE.disable()
	ps = pstats.Stats(_PROFILE).sort_stats('cumulative')
	ps.print_stats(*restrictions)
	_PROFILE = None

def useful_dir(*args: Any, **kwargs: Any) -> List[str]:
	return [
		x for x in dir(*args, **kwargs)
		if not x.endswith('__')
	]
