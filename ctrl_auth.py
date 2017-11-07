from datetime import datetime, timedelta
from urllib.parse import unquote
import lxml
import jinja2
import secrets
import base64
import os
import time
from aiohttp import web
from random import random
from PIL import Image

import settings
import models
from util.msnp import MSNPException

LOGIN_PATH = '/login'

def create_app(nb, user_service):
	app = web.Application()
	app['nb'] = nb
	app['user_service'] = user_service
	app['jinja_env'] = jinja2.Environment(
		loader = jinja2.FileSystemLoader('tmpl'),
		autoescape = jinja2.select_autoescape(default = True),
	)
	
	# MSN >= 5
	app.router.add_get('/nexus-mock', handle_nexus)
	app.router.add_get('/rdr/pprdr.asp', handle_nexus)
	app.router.add_get(LOGIN_PATH, handle_login)
	
	# MSN >= 6
	app.router.add_get('/Config/MsgrConfig.asmx', handle_msgrconfig)
	app.router.add_post('/Config/MsgrConfig.asmx', handle_msgrconfig)
	
	# MSN >= 7.5
	app.router.add_post('/NotRST.srf', handle_not_rst)
	app.router.add_post('/RST.srf', handle_rst)
	app.router.add_post('/RST2', handle_rst)
	app.router.add_post('/RST2.srf', handle_rst)
	
	# MSN 8.1.0178
	app.router.add_post('/abservice/SharingService.asmx', handle_abservice)
	app.router.add_post('/abservice/abservice.asmx', handle_abservice)
	app.router.add_post('/storageservice/SchematizedStore.asmx', handle_storageservice)
	app.router.add_get('/storage/usertile/{uuid}/static', handle_usertile)
	app.router.add_get('/storage/usertile/{uuid}/small', lambda req: handle_usertile(req, small=True))

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

async def handle_abservice(req):
	#import pdb; pdb.set_trace()
	header, action, nc, token = await _preprocess_soap(req)
	if nc is None:
		return web.Response(status = 403, text = '')
	action_str = _get_tag_localname(action)
	if _find_element(action, 'deltasOnly'):
		return render(req, 'abservice/Fault.fullsync.xml', { 'faultactor': action_str })
	now_str = datetime.utcnow().isoformat()[0:19] + 'Z'
	user = nc.user
	detail = user.detail
	cachekey = secrets.token_urlsafe(172)
	
	#_print_xml(action)
	user_service = req.app['user_service']
	
	try:
		if action_str == 'FindMembership':
			return render(req, 'sharing/FindMembershipResponse.xml', {
				'cachekey': cachekey,
				'host': settings.LOGIN_HOST,
				'user': user,
				'detail': detail,
				'lists': [models.Lst.AL, models.Lst.BL, models.Lst.RL, models.Lst.PL],
				'now': now_str,
			})
		if action_str == 'AddMember':
			lst = models.Lst.Parse(str(_find_element(action, 'MemberRole')))
			email = _find_element(action, 'PassportName')
			contact_uuid = user_service.get_uuid(email)
			nc._contacts.add_contact(contact_uuid, lst, email)
			return render(req, 'sharing/AddMemberResponse.xml')
		if action_str == 'DeleteMember':
			lst = models.Lst.Parse(str(_find_element(action, 'MemberRole')))
			email = _find_element(action, 'PassportName')
			if email:
				contact_uuid = user_service.get_uuid(email)
			else:
				contact_uuid = str(_find_element(action, 'MembershipId')).split('/')[1]
			nc._contacts.remove_contact(contact_uuid, lst)
			return render(req, 'sharing/DeleteMemberResponse.xml')
		
		if action_str == 'ABFindAll':
			return render(req, 'abservice/ABFindAllResponse.xml', {
				'cachekey': cachekey,
				'host': settings.LOGIN_HOST,
				'user': user,
				'detail': detail,
				'Lst': models.Lst,
				'list': list,
				'now': now_str,
			})
		if action_str == 'ABContactAdd':
			email = _find_element(action, 'passportName')
			contact_uuid = user_service.get_uuid(email)
			nc._contacts.add_contact(contact_uuid, models.Lst.FL, email)
			return render(req, 'abservice/ABContactAddResponse.xml', {
				'cachekey': cachekey,
				'host': settings.LOGIN_HOST,
			})
		if action_str == 'ABContactDelete':
			contact_uuid = _find_element(action, 'contactId')
			nc._contacts.remove_contact(contact_uuid, models.Lst.FL)
			return render(req, 'abservice/ABContactDeleteResponse.xml', {
				'cachekey': cachekey,
				'host': settings.LOGIN_HOST,
			})
		if action_str == 'ABContactUpdate':
			contact_uuid = _find_element(action, 'contactId')
			is_messenger_user = _find_element(action, 'isMessengerUser')
			# TODO: isFavorite is probably passed here in later WLM
			nc._contacts.edit_contact(contact_uuid, is_messenger_user = is_messenger_user)
			return render(req, 'abservice/ABContactUpdateResponse.xml', {
				'cachekey': cachekey,
				'host': settings.LOGIN_HOST,
			})
		if action_str == 'ABGroupAdd':
			name = _find_element(action, 'name')
			group = nc._contacts.add_group(name)
			return render(req, 'abservice/ABGroupAddResponse.xml', {
				'cachekey': cachekey,
				'host': settings.LOGIN_HOST,
				'group_id': group.id,
			})
		if action_str == 'ABGroupUpdate':
			group_id = str(_find_element(action, 'groupId'))
			name = _find_element(action, 'name')
			nc._contacts.edit_group(group_id, name)
			return render(req, 'abservice/ABGroupUpdateResponse.xml', {
				'cachekey': cachekey,
				'host': settings.LOGIN_HOST,
			})
		if action_str == 'ABGroupDelete':
			group_id = str(_find_element(action, 'guid'))
			nc._contacts.remove_group(group_id)
			return render(req, 'abservice/ABGroupDeleteResponse.xml', {
				'cachekey': cachekey,
				'host': settings.LOGIN_HOST,
			})
		if action_str == 'ABGroupContactAdd':
			group_id = str(_find_element(action, 'guid'))
			contact_uuid = _find_element(action, 'contactId')
			nc._contacts.add_group_contact(group_id, contact_uuid)
			return render(req, 'abservice/ABGroupContactAddResponse.xml', {
				'cachekey': cachekey,
				'host': settings.LOGIN_HOST,
				'contact_uuid': contact_uuid,
			})
		if action_str == 'ABGroupContactDelete':
			group_id = str(_find_element(action, 'guid'))
			contact_uuid = _find_element(action, 'contactId')
			nc._contacts.remove_group_contact(group_id, contact_uuid)
			return render(req, 'abservice/ABGroupContactDeleteResponse.xml', {
				'cachekey': cachekey,
				'host': settings.LOGIN_HOST,
			})
		if action_str in { 'UpdateDynamicItem' }:
			# TODO
			return _unknown_soap(req, header, action, expected = True)
	except MSNPException:
		return render(req, 'Fault.generic.xml')
	
	return _unknown_soap(req, header, action)

async def handle_storageservice(req):
	header, action, nc, token = await _preprocess_soap(req)
	action_str = _get_tag_localname(action)
	now_str = datetime.utcnow().isoformat()[0:19] + 'Z'
	timestamp = time.time()
	user = nc.user
	cachekey = secrets.token_urlsafe(172)
	
	user_service = req.app['user_service']
	cid = user_service.get_cid(user.email)
	
	if action_str == 'GetProfile':
		return render(req, 'storageservice/GetProfileResponse.xml', {
			'cachekey': cachekey,
			'cid': cid,
			'pptoken1': token,
			'user': user,
			'now': now_str,
			'timestamp': timestamp,
			'host': settings.STORAGE_HOST
		})
	if action_str == 'FindDocuments':
		# TODO
		return render(req, 'storageservice/FindDocumentsResponse.xml', {
			'cachekey': cachekey,
			'cid': cid,
			'pptoken1': token,
			'user': user,
		})
	if action_str == 'UpdateProfile':
		# TODO
		return render(req, 'storageservice/UpdateProfileResponse.xml', {
			'cachekey': cachekey,
			'cid': cid,
			'pptoken1': token,
		})
	if action_str == 'DeleteRelationships':
		# TODO
		return render(req, 'storageservice/DeleteRelationshipsResponse.xml', {
			'cachekey': cachekey,
			'cid': cid,
			'pptoken1': token,
		})
	if action_str == 'CreateDocument':
		return await handle_create_document(req, action, user, cid, token, timestamp)
	if action_str == 'CreateRelationships':
		# TODO
		return render(req, 'storageservice/CreateRelationshipsResponse.xml', {
			'cachekey': cachekey,
			'cid': cid,
			'pptoken1': token,
		})
	if action_str in { 'ShareItem' }:
		# TODO
		return _unknown_soap(req, header, action, expected = True)
	return _unknown_soap(req, header, action)

def _unknown_soap(req, header, action, *, expected = False):
	action_str = _get_tag_localname(action)
	if not expected and settings.DEBUG:
		print("Unknown SOAP:", action_str)
		_print_xml(header)
		_print_xml(action)
	return render(req, 'Fault.unsupported.xml', { 'faultactor': action_str })

def _print_xml(xml):
	print(lxml.etree.tostring(xml, pretty_print = True).decode('utf-8'))

async def _preprocess_soap(req):
	from lxml.objectify import fromstring as parse_xml
	
	body = await req.read()
	root = parse_xml(body)
	
	token = _find_element(root, 'TicketToken')
	if (token[0:2] == 't='):
		token = token[2:22]

	nc = req.app['nb'].get_nbconn(token)
	
	header = _find_element(root, 'Header')
	action = _find_element(root, 'Body/*[1]')
	
	return header, action, nc, token

def _get_tag_localname(elm):
	return lxml.etree.QName(elm.tag).localname

def _find_element(xml, query):
	thing = xml.find('.//{*}' + query.replace('/', '/{*}'))
	if isinstance(thing, lxml.objectify.StringElement):
		thing = str(thing)
	elif isinstance(thing, lxml.objectify.BoolElement):
		thing = bool(thing)
	return thing

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

async def handle_rst(req):
	from lxml.objectify import fromstring as parse_xml
	
	body = await req.read()
	root = parse_xml(body)
	
	email = _find_element(root, 'Username')
	pwd = _find_element(root, 'Password')

	if email is None or pwd is None:
		return web.Response(status = 400)
	
	token = _login(req, email, pwd)
	
	if token is not None:
		timez = datetime.utcnow().isoformat()[0:19] + 'Z'
		tomorrowz = (datetime.utcnow() + timedelta(days=1)).isoformat()[0:19] + 'Z'
		
		# load PUID and CID, assume them to be the same for our purposes
		user_service = req.app['user_service']
		cid = user_service.get_cid(email)
		
		peername = req.transport.get_extra_info('peername')
		host = '127.0.0.1'
		if peername is not None:
			host, _ = peername
		
		# get list of requested domains
		domains = root.findall('.//{*}Address')
		domains.pop(0) # ignore Passport token request
		
		tokenxml = ''
		tmpl = req.app['jinja_env'].get_template('RST/RST.token.xml')
		
		# collect tokens for requested domains
		for i in range(len(domains)):

			tokenxml += tmpl.render(
				domain = domains[i],
				timez = timez,
				tomorrowz = tomorrowz,
				i = i + 1,
				pptoken1 = token,
			)

		tmpl = req.app['jinja_env'].get_template('RST/RST.xml')
		return web.Response(
			status = 200,
			content_type = 'text/xml',
			text = tmpl.render(
				puidhex = cid.upper(),
				timez = timez,
				tomorrowz = tomorrowz,
				cid = cid,
				email = email,
				firstname = 'John', # we don't have those on file, do we
				lastname = 'Doe',
				ip = host,
				pptoken1 = token,
			).replace('{ tokenxml }', tokenxml)
		)
	
	return render(req, 'RST/RST.error.xml', {
		'timez': datetime.utcnow().isoformat()[0:19] + 'Z',
	}, status = 403)

def _get_storage_path(uuid):
	path = 'storage/dp/{u1}/{u2}'.format(
		u1=uuid[0:1],
		u2=uuid[0:2],
	)

	return path

async def handle_create_document(req, action, user, cid, token, timestamp):
	# get image data
	name = _find_element(action, 'Name')
	streamtype = _find_element(action, 'DocumentStreamType')

	if (streamtype == 'UserTileStatic'):
		mime = _find_element(action, 'MimeType')
		data = _find_element(action, 'Data')
		data = base64.b64decode(data)

		# store display picture as file
		path = _get_storage_path(user.uuid)

		if not os.path.exists(path):
			os.makedirs(path)

		image_path = '{path}/{uuid}.{mime}'.format(
			path = path,
			uuid = user.uuid,
			mime = mime
		)

		fp = open(image_path, 'wb')
		fp.write(data)
		fp.close()

		image = Image.open(image_path)
		thumb = image.resize((21, 21))

		thumb_path = '{path}/{uuid}_thumb.{mime}'.format(
			path=path,
			uuid=user.uuid,
			mime=mime
		)

		thumb.save(thumb_path)

	return render(req, 'storageservice/CreateDocumentResponse.xml', {
		'user': user,
		'cid': cid,
		'pptoken1': token,
		'timestamp': timestamp,
	})

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
	return req.app['nb'].pre_login(email, pwd)

async def handle_other(req):
	if settings.DEBUG:
		print("! Unknown: {} {}://{}{}".format(req.method, req.scheme, req.host, req.path_qs))
	return web.Response(status = 404, text = '')

def render(req, tmpl_name, ctxt = None, status = 200):
	if tmpl_name.endswith('.xml'):
		content_type = 'text/xml'
	else:
		content_type = 'text/html'
	tmpl = req.app['jinja_env'].get_template(tmpl_name)
	user_service = req.app['user_service']
	tmpl.globals['get_date_created'] = user_service.get_date_created
	tmpl.globals['get_cid'] = user_service.get_cid
	tmpl.globals['bool_to_str'] = _bool_to_str
	content = tmpl.render(**(ctxt or {}))
	return web.Response(status = status, content_type = content_type, text = content)

def _bool_to_str(b):
	return 'true' if b else 'false'

async def handle_usertile(req, small=False):
	uuid = req.match_info['uuid']
	storage_path = _get_storage_path(uuid)

	try:
		ext = os.listdir(storage_path)[0].split('.')[-1]

		if small:
			image_path = os.path.join(storage_path, "{uuid}_thumb.{ext}".format(**locals()))
		else:
			image_path = os.path.join(storage_path, "{uuid}.{ext}".format(**locals()))

		with open(image_path, 'rb') as file:
			return web.Response(status=200, content_type="image/{ext}".format(**locals()), body=file.read())
	except FileNotFoundError:
		raise web.HTTPNotFound

PP = 'Passport1.4 '
