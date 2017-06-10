from urllib.parse import unquote
from aiohttp import web

import settings

LOGIN_PATH = '/login'

def create_app():
	app = web.Application()
	app.router.add_get('/nexus-mock', handle_nexus)
	app.router.add_post('/NotRST.srf', handle_not_rst)
	app.router.add_get(LOGIN_PATH, handle_login)
	app.router.add_route('*', '/{path:.*}', handle_other)
	return app

async def handle_nexus(req):
	return web.Response(status = 200, headers = {
		'PassportURLs': 'DALogin=https://{}{}'.format(settings.LOGIN_HOST, LOGIN_PATH),
	})

async def handle_login(req):
	email, pwd = _extract_pp_credentials(req.headers.get('Authorization'))
	token = _login(email, pwd)
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
	token = _login(email, pwd)
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

def _login(email, pwd):
	from db import Session, User, Auth
	from util.hash import hasher
	with Session() as sess:
		user = sess.query(User).filter(User.email == email).one_or_none()
		if user is None: return None
		if not hasher.verify(pwd, user.password): return None
		return Auth.CreateToken(user.email)

async def handle_other(req):
	print("UNKNOWN REQUEST:", req.method, req.host + req.path_qs)
	#print(req.headers)
	#body = await req.read()
	#if body:
	#	print("body {")
	#	print(body)
	#	print("}")
	#else:
	#	print("body {}")
	return web.Response(status = 404)

PP = 'Passport1.4 '
