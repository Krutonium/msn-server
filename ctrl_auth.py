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
	app['jinja_env'] = jinja2.Environment(
		loader = jinja2.FileSystemLoader('tmpl'),
		autoescape = jinja2.select_autoescape(default = True),
	)
	
	# MSN >= 5
	app.router.add_get('/nexus-mock', handle_nexus)
	app.router.add_get(LOGIN_PATH, handle_login)
	
	# MSN >= 6
	app.router.add_get('/Config/MsgrConfig.asmx', handle_msgrconfig)
	app.router.add_post('/Config/MsgrConfig.asmx', handle_msgrconfig)
	
	# MSN >= 7.5
	app.router.add_post('/NotRST.srf', handle_not_rst)
	
	# MSN 8.1.0178
	app.router.add_post('/abservice/SharingService.asmx', handle_soap)
	app.router.add_post('/abservice/abservice.asmx', handle_soap)
	
	# Misc
	app.router.add_get('/etc/debug', handle_debug)
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

async def handle_soap(req):
	action, user = await _preprocess_soap(req)
	if user is None:
		return web.Response(status = 403, text = '')
	if bool(action.find('.//{http://www.msn.com/webservices/AddressBook}deltasOnly')):
		return render(req, 'fault.fullsync.xml')
	action_str = _get_tag_localname(action)
	detail = req.app['user_service'].get_detail(user.uuid)
	now_str = datetime.utcnow().isoformat() + 'Z'
	if action_str == 'FindMembership':
		return render(req, 'SharingService.xml', {
			'user': user,
			'detail': detail,
			'lists': [models.Lst.AL, models.Lst.BL, models.Lst.RL, models.Lst.PL],
			'now': now_str,
			'random_crap': str(random())[2:],
		})
	elif action_str == 'ABFindAll':
		return render(req, 'abservice.xml', {
			'user': user,
			'detail': detail,
			'Lst': models.Lst,
			'list': list,
			'now': now_str,
			'random_crap': str(random())[2:],
		})
	else:
		import lxml
		print("Unknown SOAP:")
		print(lxml.etree.tostring(action, pretty_print = True).decode('utf-8'))
		return render(req, 'fault.xml', { 'action': action_str })

async def _preprocess_soap(req):
	from lxml.objectify import fromstring as parse_xml
	
	body = await req.read()
	root = parse_xml(body)
	
	user = None
	token = root.find('.//{http://www.msn.com/webservices/AddressBook}TicketToken')
	if token is not None:
		auth = req.app['auth_service']
		uuid = auth.pop_token('contacts', token)
		if uuid is not None:
			user = req.app['user_service'].get(uuid)
	
	if user is not None:
		action = root.find('.//{http://schemas.xmlsoap.org/soap/envelope/}Body/*[1]')
		# Refresh the token for later use
		auth.create_token('contacts', uuid, token = token, lifetime = 24 * 60 * 60)
	else:
		action = None
	
	return action, user

def _get_tag_localname(elm):
	import lxml
	return lxml.etree.QName(elm.tag).localname

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
