from typing import Any
import asyncio
from aiohttp import web

import settings
from core.backend import Backend

def create_app(loop: asyncio.AbstractEventLoop, backend: Backend, msn_http: bool, yahoo_http: bool) -> Any:
	app = web.Application(loop = loop)
	app['backend'] = backend
	
	# MSN, Plus! Sounds, and MSN HTTP Gateway
	if msn_http:
		from . import http_msn
		from . import http_sound
		from . import http_gateway
		http_msn.register(app)
		http_sound.register(app)
		http_gateway.register(loop, app)
	
	# Yahoo!
	if yahoo_http:
		from . import http_yahoo
		http_yahoo.register(app)
	
	# Misc
	app.router.add_get('/etc/debug', handle_debug)
	app.router.add_route('*', '/{path:.*}', handle_other)
	
	app.on_response_prepare.append(on_response_prepare)
	
	return app

async def on_response_prepare(req, res):
	if not settings.DEBUG:
		return
	if not settings.DEBUG_HTTP_REQUEST:
		return
	print("# Request: {} {}://{}{}".format(req.method, req.scheme, req.host, req.path_qs))
	if not settings.DEBUG_HTTP_REQUEST_FULL:
		return
	print(req.headers)
	body = await req.read()
	if body:
		print("body {")
		print(body)
		print("}")
	else:
		print("body {}")

async def handle_debug(req):
	return render(req, 'debug.html')

async def handle_other(req):
	if settings.DEBUG:
		print("! Unknown: {} {}://{}{}".format(req.method, req.scheme, req.host, req.path_qs))
	return web.Response(status = 404, text = '')
