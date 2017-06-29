from datetime import datetime
from urllib.parse import unquote
import jinja2
from aiohttp import web
from random import random

import settings
import models

LOGIN_PATH = '/login'

def create_app(user_service, auth_service):
	app = web.Application()
	app['user_service'] = user_service
	app['auth_service'] = auth_service
	app['jinja_env'] = jinja2.Environment(loader = jinja2.FileSystemLoader('tmpl'))
	
	app.router.add_get('/etc/debug', handle_debug)
	app.router.add_get('/nexus-mock', handle_nexus)
	app.router.add_post('/NotRST.srf', handle_not_rst)
	app.router.add_get(LOGIN_PATH, handle_login)
	app.router.add_get('/Config/MsgrConfig.asmx', handle_msgrconfig)
	app.router.add_post('/Config/MsgrConfig.asmx', handle_msgrconfig)
	
	# MSN 8.1.0178
	app.router.add_post('/abservice/SharingService.asmx', handle_sharingservice)
	app.router.add_post('/abservice/abservice.asmx', handle_abservice)
	
	app.router.add_route('*', '/{path:.*}', handle_other)
	
	app.on_response_prepare.append(on_response_prepare)
	
	return app

async def on_response_prepare(req, res):
	if not settings.DEBUG:
		return
	print("# Request: {} {}://{}{}".format(req.method, req.scheme, req.host, req.path_qs))
	#print(req.headers)
	#body = await req.read()
	#if body:
	#	print("body {")
	#	print(body)
	#	print("}")
	#else:
	#	print("body {}")

async def handle_debug(req):
	return render(req, 'debug.html')

async def handle_sharingservice(req):
	user = await _get_user_for_soap_request(req)
	if user is None:
		return web.Response(status = 403, text = '')
	detail = req.app['user_service'].get_detail(user.uuid)
	return render(req, 'SharingService.xml', {
		'user': user,
		'detail': detail,
		'lists': [models.Lst.AL, models.Lst.BL, models.Lst.RL, models.Lst.PL],
		'now': datetime.utcnow().isoformat(),
		'random_crap': str(random())[2:],
	})

async def handle_abservice(req):
	user = await _get_user_for_soap_request(req)
	if user is None:
		return web.Response(status = 403, text = '')
	detail = req.app['user_service'].get_detail(user.uuid)
	return render(req, 'abservice.xml', {
		'user': user,
		'detail': detail,
		'Lst': models.Lst,
		'now': datetime.utcnow().isoformat(),
		'random_crap': str(random())[2:],
	})

async def _get_user_for_soap_request(req):
	from lxml import etree
	from lxml.objectify import fromstring as parse_xml
	
	body = await req.read()
	elm = parse_xml(body)
	print("request", body.decode('utf-8'))
	token = elm.find('.//{http://www.msn.com/webservices/AddressBook}TicketToken')
	if token is None: return None
	auth = req.app['auth_service']
	uuid = auth.pop_token('contacts', token)
	if uuid is None: return None
	user = req.app['user_service'].get(uuid)
	if user is None: return None
	# Refresh the token for later use
	auth.create_token('contacts', uuid, token = token, lifetime = 24 * 60 * 60)
	return user

async def handle_msgrconfig(req):
	return web.Response(status = 200, text = '')

async def handle_msgrconfig(req):
	msgr_config = _get_msgr_config()
	return web.Response(status = 200, content_type = 'text/xml', text = msgr_config)

def _get_msgr_config():
	with open('tmpl/MsgrConfigEnvelope.xml') as fh:
		envelope = fh.read()
	with open('tmpl/MsgrConfig.xml') as fh:
		config = fh.read()
	return envelope.format(MsgrConfig = config)

async def handle_nexus(req):
	return web.Response(status = 200, headers = {
		'PassportURLs': 'DALogin=https://{}{}'.format(settings.LOGIN_HOST, LOGIN_PATH),
	})

async def handle_login(req):
	email, pwd = _extract_pp_credentials(req.headers.get('Authorization'))
	token = _login(req, email, pwd)
	if token is None:
		return web.Response(status = 401, headers = {
			'WWW-Authenticate': '{}da-status=failed'.format(PP),
		})
	return web.Response(status = 200, headers = {
		'Authentication-Info': '{}da-status=success,from-PP=\'{}\''.format(PP, token),
	})

async def handle_not_rst(req):
	email = req.headers.get('X-User')
	pwd = req.headers.get('X-Password')
	token = _login(req, email, pwd)
	headers = {}
	if token is not None:
		headers['X-Token'] = token
	return web.Response(status = 200, headers = headers)

def _extract_pp_credentials(auth_str):
	if auth_str is None:
		return None, None
	assert auth_str.startswith(PP)
	auth = {}
	for part in auth_str[len(PP):].split(','):
		parts = part.split('=', 1)
		if len(parts) == 2:
			auth[unquote(parts[0])] = unquote(parts[1])
	email = auth['sign-in']
	pwd = auth['pwd']
	return email, pwd

def _login(req, email, pwd):
	uuid = req.app['user_service'].login(email, pwd)
	if uuid is None: return None
	return req.app['auth_service'].create_token('nb/login', uuid)

async def handle_other(req):
	return web.Response(status = 404, text = '')

def render(req, tmpl_name, ctxt = None):
	if tmpl_name.endswith('.xml'):
		content_type = 'text/xml'
	else:
		content_type = 'text/html'
	tmpl = req.app['jinja_env'].get_template(tmpl_name)
	content = tmpl.render(**(ctxt or {}))
	return web.Response(status = 200, content_type = content_type, text = content)

PP = 'Passport1.4 '
