import asyncio
from typing import Any, Dict, Optional
from aiohttp import web
from urllib.parse import unquote
import jinja2

from core.backend import Backend
import settings

SYSBOARD_TMPL_DIR = 'core/tmpl/sysboard'

def register(loop: asyncio.AbstractEventLoop, backend: Backend, *, devmode: bool = False) -> web.Application:
	from util.misc import AIOHTTPRunner
	
	sysboard_host = ('0.0.0.0' if devmode else '127.0.0.1')
	
	app = create_app(loop, backend)
	backend.add_runner(AIOHTTPRunner(sysboard_host, 52478, app))
	
	app.router.add_get('/sysboard/', handle_sysboard_gui)
	app.router.add_get('/sysboard/login', handle_sysboard_login)
	app.router.add_post('/sysboard/login', handle_sysboard_login_verify)
	app.router.add_post('/sysboard/', handle_sysboard_action)
	
	return app

def create_app(loop: asyncio.AbstractEventLoop, backend: Backend) -> Any:
	app = web.Application(loop = loop)
	app['backend'] = backend
	app['jinja_env'] = jinja2.Environment(
		loader = jinja2.FileSystemLoader(SYSBOARD_TMPL_DIR),
		autoescape = jinja2.select_autoescape(default = True),
	)
	
	app.on_response_prepare.append(on_response_prepare)
	
	return app

async def on_response_prepare(req, res):
	if not settings.DEBUG:
		return
	if not settings.DEBUG_SYSBOARD:
		return
		
	if req.path == '/sysboard/' and req.method == 'POST':
		print('Pushing maintenance/system message to online users...')
	if req.path == '/sysboard/login' and req.method == 'POST':
		print('Admin being verified...')

# Sysboard HTTP entries

async def handle_sysboard_login(req: web.Request) -> web.Response:
	backend = req.app['backend']
	
	if True in (backend.maintenance_mode,backend.notify_maintenance):
		return render(req, 'unavailable.html')
	
	return (web.HTTPFound('/sysboard/') if _validate_session(backend) else render(req, 'login.html'))

async def handle_sysboard_login_verify(req: web.Request) -> web.Response:
	password = (req.headers.get('X-Password') or '')
	
	if password == '':
		return web.HTTPInternalServerError()
	
	if password == settings.SYSBOARD_PASS:
		req.app['backend'].auth_service.create_token('sysboard/token', password, lifetime = 300)
		return web.HTTPOk()

async def handle_sysboard_gui(req: web.Request) -> web.Response:
	backend = req.app['backend']
	
	if True in (backend.maintenance_mode,backend.notify_maintenance):
		return render(req, 'unavailable.html')
	
	return (render(req, 'index.html') if _validate_session(backend) else web.HTTPFound('/sysboard/login'))

async def handle_sysboard_action(req: web.Request) -> web.Response:
	body = await req.read()
	backend = req.app['backend']
	
	if True in (backend.maintenance_mode,backend.notify_maintenance):
		return web.HTTPMisdirectedRequest()
	
	if not _validate_session(backend):
		return web.HTTPUnauthorized()
	
	system_msg = (None if _parse_urlencoded(body) is None else _parse_urlencoded(body).get('sysmsg'))
	
	if req.headers.get('X-Maintenance-Minutes') is not None and system_msg is not None:
		return web.HTTPInternalServerError()
	
	if req.headers.get('X-Maintenance-Minutes') is not None:
		try:
			mt_mins = int(req.headers.get('X-Maintenance-Minutes'))
		except ValueError:
			return web.HTTPInternalServerError()
		
		backend.push_system_message(1, mt_mins)
	elif system_msg is not None:
		backend.push_system_message(1, -1, message = system_msg)
	else:
		return web.HTTPInternalServerError()
	
	return web.HTTPOk()

def _validate_session(backend: Backend) -> bool:
	if backend.auth_service.sysboard_retreive_last_valid_token(settings.SYSBOARD_PASS) is not None:
		return True
	else:
		return False

def _parse_urlencoded(body: bytes) -> Optional[Dict[str, Any]]:
	param_dict = {}
	
	for param in body.decode().split('&'):
		param_two = param.split('=')
		for i in range(1, len(param_two), 2):
			try:
				param_dict[param_two[i - 1]] = unquote(param_two[i])
			except IndexError:
				return None
	
	return param_dict

def render(req: web.Request, tmpl_name: str, ctxt: Optional[Dict[str, Any]] = None, status: int = 200) -> web.Response:
	if tmpl_name.endswith('.xml'):
		content_type = 'text/xml'
	else:
		content_type = 'text/html'
	tmpl = req.app['jinja_env'].get_template(tmpl_name)
	content = tmpl.render(**(ctxt or {}))
	return web.Response(status = status, content_type = content_type, text = content)
