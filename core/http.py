import asyncio
from typing import Any, Dict, Optional
from aiohttp import web
import jinja2

from core.backend import Backend
import settings

def register(loop: asyncio.AbstractEventLoop, backend: Backend, *, devmode: bool = False) -> web.Application:
	from util.misc import AIOHTTPRunner
	
	if devmode:
		http_host = '0.0.0.0'
		http_port = 80
		from dev import autossl
		ssl_context = autossl.create_context() # type: Optional[Any]
	else:
		http_host = '127.0.0.1'
		http_port = 8081
		ssl_context = None
	
	app = create_app(loop, backend)
	backend.add_runner(AIOHTTPRunner(http_host, http_port, app, ssl_context = ssl_context))
	return app

def create_app(loop: asyncio.AbstractEventLoop, backend: Backend) -> Any:
	app = web.Application(loop = loop)
	app['backend'] = backend
	app['jinja_env'] = jinja2.Environment(
		loader = jinja2.PrefixLoader({}, delimiter = ':'),
		autoescape = jinja2.select_autoescape(default = True),
	)
	
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

def render(req: web.Request, tmpl_name: str, ctxt: Optional[Dict[str, Any]] = None, status: int = 200) -> web.Response:
	if tmpl_name.endswith('.xml'):
		content_type = 'text/xml'
	else:
		content_type = 'text/html'
	tmpl = req.app['jinja_env'].get_template(tmpl_name)
	content = tmpl.render(**(ctxt or {}))
	return web.Response(status = status, content_type = content_type, text = content)
