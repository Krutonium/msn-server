from typing import Optional, Any, Dict, Tuple
from datetime import datetime, timedelta
from pytz import timezone
from urllib.parse import unquote
import lxml
import secrets
import base64
import os
import time
from markupsafe import Markup
from aiohttp import web

import settings
from core import models
from core.http import render
from core.backend import Backend, BackendSession
from .misc import gen_mail_data
import util.misc

LOGIN_PATH = '/login'
TMPL_DIR = 'front/msn/tmpl'
PP = 'Passport1.4 '

def register(app: web.Application) -> None:
	util.misc.add_to_jinja_env(app, 'msn', TMPL_DIR, globals = {
		'date_format': _date_format,
		'cid_format': _cid_format,
		'bool_to_str': _bool_to_str,
		'contact_is_favorite': _contact_is_favorite,
	})
	
	# MSN >= 5
	app.router.add_get('/nexus-mock', handle_nexus)
	app.router.add_get('/rdr/pprdr.asp', handle_nexus)
	app.router.add_get(LOGIN_PATH, handle_login)
	
	# MSN >= 6
	app.router.add_get('/Config/MsgrConfig.asmx', handle_msgrconfig)
	app.router.add_post('/Config/MsgrConfig.asmx', handle_msgrconfig)
	
	# MSN >= 7.5
	app.router.add_route('OPTIONS', '/NotRST.srf', handle_not_rst)
	app.router.add_post('/NotRST.srf', handle_not_rst)
	app.router.add_post('/RST.srf', handle_rst)
	app.router.add_post('/RST2', handle_rst)
	app.router.add_post('/RST2.srf', handle_rst)
	
	# MSN 8.1.0178
	app.router.add_post('/abservice/SharingService.asmx', handle_abservice)
	app.router.add_post('/abservice/abservice.asmx', handle_abservice)
	app.router.add_post('/storageservice/SchematizedStore.asmx', handle_storageservice)
	app.router.add_get('/storage/usertile/{uuid}/static', handle_usertile)
	app.router.add_get('/storage/usertile/{uuid}/small', lambda req: handle_usertile(req, small = True))
	app.router.add_post('/rsi/rsi.asmx', handle_rsi)
	app.router.add_post('/OimWS/oim.asmx', handle_oim)
	
	# Misc
	app.router.add_get('/etc/debug', handle_debug)

async def handle_abservice(req: web.Request) -> web.Response:
	header, action, ns_sess, token = await _preprocess_soap(req)
	if ns_sess is None:
		return web.Response(status = 403, text = '')
	action_str = _get_tag_localname(action)
	if _find_element(action, 'deltasOnly'):
		return render(req, 'msn:abservice/Fault.fullsync.xml', { 'faultactor': action_str })
	now_str = datetime.utcnow().isoformat()[0:19] + 'Z'
	user = ns_sess.user
	detail = user.detail
	cachekey = secrets.token_urlsafe(172)
	
	#print(_xml_to_string(action))
	backend = req.app['backend']
	
	try:
		if action_str == 'FindMembership':
			return render(req, 'msn:sharing/FindMembershipResponse.xml', {
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
			contact_uuid = backend.util_get_uuid_from_email(email)
			backend.me_contact_add(ns_sess, contact_uuid, lst, email)
			return render(req, 'msn:sharing/AddMemberResponse.xml')
		if action_str == 'DeleteMember':
			lst = models.Lst.Parse(str(_find_element(action, 'MemberRole')))
			email = _find_element(action, 'PassportName')
			if email:
				contact_uuid = backend.util_get_uuid_from_email(email)
			else:
				contact_uuid = str(_find_element(action, 'MembershipId')).split('/')[1]
			backend.me_contact_remove(ns_sess, contact_uuid, lst)
			return render(req, 'msn:sharing/DeleteMemberResponse.xml')
		
		if action_str == 'ABFindAll':
			return render(req, 'msn:abservice/ABFindAllResponse.xml', {
				'cachekey': cachekey,
				'host': settings.LOGIN_HOST,
				'user': user,
				'detail': detail,
				'Lst': models.Lst,
				'list': list,
				'now': now_str,
			})
		if action_str == 'ABFindContactsPaged':
			return render(req, 'msn:abservice/ABFindContactsPagedResponse.xml', {
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
			contact_uuid = backend.util_get_uuid_from_email(email)
			backend.me_contact_add(ns_sess, contact_uuid, models.Lst.FL, email)
			return render(req, 'msn:abservice/ABContactAddResponse.xml', {
				'cachekey': cachekey,
				'host': settings.LOGIN_HOST,
			})
		if action_str == 'ABContactDelete':
			contact_uuid = _find_element(action, 'contactId')
			backend.me_contact_remove(ns_sess, contact_uuid, models.Lst.FL)
			return render(req, 'msn:abservice/ABContactDeleteResponse.xml', {
				'cachekey': cachekey,
				'host': settings.LOGIN_HOST,
			})
		if action_str == 'ABContactUpdate':
			contact_uuid = _find_element(action, 'contactId')
			is_messenger_user = _find_element(action, 'isMessengerUser')
			backend.me_contact_edit(ns_sess, contact_uuid, is_messenger_user = is_messenger_user)
			return render(req, 'msn:abservice/ABContactUpdateResponse.xml', {
				'cachekey': cachekey,
				'host': settings.LOGIN_HOST,
			})
		if action_str == 'ABGroupAdd':
			name = _find_element(action, 'name')
			is_favorite = _find_element(action, 'IsFavorite')
			group = backend.me_group_add(ns_sess, name, is_favorite = is_favorite)
			return render(req, 'msn:abservice/ABGroupAddResponse.xml', {
				'cachekey': cachekey,
				'host': settings.LOGIN_HOST,
				'group_id': group.id,
			})
		if action_str == 'ABGroupUpdate':
			group_id = str(_find_element(action, 'groupId'))
			name = _find_element(action, 'name')
			is_favorite = _find_element(action, 'IsFavorite')
			backend.me_group_edit(ns_sess, group_id, name, is_favorite = is_favorite)
			return render(req, 'msn:abservice/ABGroupUpdateResponse.xml', {
				'cachekey': cachekey,
				'host': settings.LOGIN_HOST,
			})
		if action_str == 'ABGroupDelete':
			group_id = str(_find_element(action, 'guid'))
			backend.me_group_remove(ns_sess, group_id)
			return render(req, 'msn:abservice/ABGroupDeleteResponse.xml', {
				'cachekey': cachekey,
				'host': settings.LOGIN_HOST,
			})
		if action_str == 'ABGroupContactAdd':
			group_id = str(_find_element(action, 'guid'))
			contact_uuid = _find_element(action, 'contactId')
			backend.me_group_contact_add(ns_sess, group_id, contact_uuid)
			return render(req, 'msn:abservice/ABGroupContactAddResponse.xml', {
				'cachekey': cachekey,
				'host': settings.LOGIN_HOST,
				'contact_uuid': contact_uuid,
			})
		if action_str == 'ABGroupContactDelete':
			group_id = str(_find_element(action, 'guid'))
			contact_uuid = _find_element(action, 'contactId')
			backend.me_group_contact_remove(ns_sess, group_id, contact_uuid)
			return render(req, 'msn:abservice/ABGroupContactDeleteResponse.xml', {
				'cachekey': cachekey,
				'host': settings.LOGIN_HOST,
			})
		if action_str in { 'UpdateDynamicItem' }:
			# TODO: UpdateDynamicItem
			return _unknown_soap(req, header, action, expected = True)
	except Exception as ex:
		import traceback
		return render(req, 'msn:Fault.generic.xml', {
			'exception': traceback.format_exc(),
		})
	
	return _unknown_soap(req, header, action)

async def handle_storageservice(req):
	header, action, bs, token = await _preprocess_soap(req)
	assert bs is not None
	action_str = _get_tag_localname(action)
	now_str = datetime.utcnow().isoformat()[0:19] + 'Z'
	timestamp = int(time.time())
	user = bs.user
	cachekey = secrets.token_urlsafe(172)
	
	cid = _cid_format(user.uuid)
	
	if action_str == 'GetProfile':
		return render(req, 'msn:storageservice/GetProfileResponse.xml', {
			'cachekey': cachekey,
			'cid': cid,
			'pptoken1': token,
			'user': user,
			'now': now_str,
			'timestamp': timestamp,
			'host': settings.STORAGE_HOST
		})
	if action_str == 'FindDocuments':
		# TODO: FindDocuments
		return render(req, 'msn:storageservice/FindDocumentsResponse.xml', {
			'cachekey': cachekey,
			'cid': cid,
			'pptoken1': token,
			'user': user,
		})
	if action_str == 'UpdateProfile':
		# TODO: UpdateProfile
		return render(req, 'msn:storageservice/UpdateProfileResponse.xml', {
			'cachekey': cachekey,
			'cid': cid,
			'pptoken1': token,
		})
	if action_str == 'DeleteRelationships':
		# TODO: DeleteRelationships
		return render(req, 'msn:storageservice/DeleteRelationshipsResponse.xml', {
			'cachekey': cachekey,
			'cid': cid,
			'pptoken1': token,
		})
	if action_str == 'CreateDocument':
		return handle_create_document(req, action, user, cid, token, timestamp)
	if action_str == 'CreateRelationships':
		# TODO: CreateRelationships
		return render(req, 'msn:storageservice/CreateRelationshipsResponse.xml', {
			'cachekey': cachekey,
			'cid': cid,
			'pptoken1': token,
		})
	if action_str in { 'ShareItem' }:
		# TODO: ShareItem
		return _unknown_soap(req, header, action, expected = True)
	return _unknown_soap(req, header, action)

async def handle_rsi(req: web.Request) -> web.Response:
	header, action, ns_sess, token = await _preprocess_soap_rsi(req)
	# Since valtron's MSIDCRL solution does not supply the ticket ('t='/'p='), we can't go any further with authentication or action supplication.
	# Keep the code for when we can get the original MSIDCRL DLL modified for Escargot use.
	# 
	# if token is None or ns_sess is None:
	# 	return render(req, 'msn:oim/Fault.validation.xml', status = 500)
	action_str = _get_tag_localname(action)
	# user = ns_sess.user
	# 
	# backend = req.app['backend']
	# 
	# 
	# 
	# if action_str == 'GetMetadata':
	# 	return render(req, 'msn:oim/GetMetadataResponse.xml', {
	# 		'md': gen_mail_data(user, backend, on_ns = False, e_node = False).decode('utf-8'),
	# 	})
	# if action_str == 'GetMessage':
	# 	oim_uuid = _find_element(action, 'messageId')
	# 	oim_markAsRead = _find_element(action, 'alsoMarkAsRead')
	# 	
	# 	oim_message = backend.user_service.get_oim_message_by_uuid(user.email, oim_uuid, oim_markAsRead)
	# 	
	# 	return render(req, 'msn:oim/GetMessageResponse.xml', {
	# 		'oim_data': message,
	# 	})
	# if action_str == 'DeleteMessages':
	# 	messageIds = action.findall('.//{*}messageId/{*}messageIds')
	# 	for messageId in messageIds:
	# 		isValidDeletion = backend.user_service.delete_oim(messageId)
	# 		if not isValidDeletion:
	# 			return render(req, 'msn:oim/Fault.validation.xml', status = 500)
	# 	
	# 	
	# 	ns_sess.evt.on_oim_deletion()
	# 	
	# 	return render(req, 'msn:oim/DeleteMessagesResponse.xml')
	# 
	# Return 'Fault.unsupported.xml' for now.
	
	return render(req, 'msn:Fault.unsupported.xml', { 'faultactor': action_str })

async def handle_oim(req: web.Request) -> web.Response:
	# However, the ticket is present when this service is used. Odd.
	
	header, body_msgtype, body_content, ns_sess, token = await _preprocess_soap_oimws(req)
	soapaction = req.headers.get('SOAPAction').strip('"')
	
	lockkey_result = header.find('.//{*}Ticket').get('lockkey')
	
	if ns_sess is None or lockkey_result == '':
		return render(req, 'msn:oim/Fault.authfailed.xml', {
			'owsns': ('http://messenger.msn.com/ws/2004/09/oim/' if soapaction.startswith('http://messenger.msn.com/ws/2004/09/oim/') else 'http://messenger.live.com/ws/2006/09/oim/'),
			'authTypeNode': (Markup('<TweenerChallenge xmlns="http://messenger.msn.com/ws/2004/09/oim/">ct=1,rver=1,wp=FS_40SEC_0_COMPACT,lc=1,id=1</TweenerChallenge>') if soapaction.startswith('http://messenger.msn.com/ws/2004/09/') else Markup('<SSOChallenge xmlns="http://messenger.live.com/ws/2006/09/oim/">?MBI_KEY_OLD</SSOChallenge>')),
		}, status = 500)
	
	backend = req.app['backend']
	user = ns_sess.user
	detail = user.detail
	assert detail is not None
	
	friendlyname = header.find('.//{*}From').get('friendlyName')
	email = header.find('.//{*}From').get('memberName')
	recipient = header.find('.//{*}To').get('memberName')
	
	recipient_uuid = backend.util_get_uuid_from_email(recipient)
	
	if email != user.email or recipient_uuid is None or not _is_on_al(recipient_uuid, detail):
		return render(req, 'msn:oim/Fault.unavailable.xml', {
			'owsns': ('http://messenger.msn.com/ws/2004/09/oim/' if soapaction.startswith('http://messenger.msn.com/ws/2004/09/oim/') else 'http://messenger.live.com/ws/2006/09/oim/'),
		}, status = 500)
	
	peername = req.transport.get_extra_info('peername')
	if peername:
		host = peername[0]
	else:
		host = '127.0.0.1'
	
	oim_msg_seq = _find_element(header, 'Sequence/MessageNumber')
	
	oim_sent_date = datetime.utcnow()
	oim_sent_date_email = oim_sent_date.astimezone(timezone('US/Pacific'))
	
	oim_content = body_content.strip().replace('\n', '\r\n')
	
	oim_run_id_start = (oim_content.find('X-OIM-Run-Id: ') + 15)
	oim_run_id_stop = oim_run_id_start + 36
	oim_run_id = oim_content[oim_run_id_start:oim_run_id_stop]
	
	oim_header_body = oim_content.split('\r\n\r\n')
	oim_header_body[0] = OIM_HEADER_PRE.format(
		pst1 = oim_sent_date_email.strftime('%a, %d %b %Y %H:%M:%S -0800'), friendly = friendlyname,
		sender = user.email, recipient = recipient, ip = host,
	) + oim_header_body[0]
	oim_header_body[0] += OIM_HEADER_REST.format(
		utc = oim_sent_date.strftime('%d %b %Y %H:%M:%S.%f')[:25] + ' (UTC)', ft = _datetime_to_filetime(oim_sent_date),
		pst2 = oim_sent_date_email.strftime('%d %b %Y %H:%M:%S -0800'),
	)
	
	oim_content = '\r\n\r\n'.join(oim_header_body)
	
	backend.user_service.save_oim(oim_run_id, int(oim_msg_seq), oim_content, user.email, friendlyname, recipient, oim_sent_date)
	ns_sess.me_contact_notify_oim(recipient_uuid, oim_run_id)
	
	return render(req, 'msn:oim/StoreResponse.xml', {
		'seq': oim_msg_seq,
		'owsns': ('http://messenger.msn.com/ws/2004/09/oim/' if soapaction.startswith('http://messenger.msn.com/ws/2004/09/oim/') else 'http://messenger.live.com/ws/2006/09/oim/'),
	})

def _is_on_al(uuid: str, detail: models.UserDetail):
	contact = detail.contacts.get(uuid)
	if detail.settings.get('BLP', 'AL') is 'AL' and (contact is None or contact.lists != models.Lst.BL):
		return True
	elif detail.settings.get('BLP', 'AL') is 'BL' and contact is not None and contact.lists != models.Lst.BL:
		return True
	return False

def _unknown_soap(req: web.Request, header: Any, action: Any, *, expected: bool = False) -> web.Response:
	action_str = _get_tag_localname(action)
	if not expected and settings.DEBUG:
		print("Unknown SOAP:", action_str)
		print(_xml_to_string(header))
		print(_xml_to_string(action))
	return render(req, 'msn:Fault.unsupported.xml', { 'faultactor': action_str })

def _datetime_to_filetime(dt_time: datetime) -> str:
	filetime_result = round(((dt_time.timestamp() * 10000000) + 116444736000000000) + (dt_time.microsecond * 10))
	
	# (DWORD)ll
	filetime_high = filetime_result & 0xFFFFFFFF
	filetime_low = filetime_result >> 32
	
	filetime_high_hex = hex(filetime_high)[2:]
	filetime_high_hex = '0' * (8 % len(filetime_high_hex)) + filetime_high_hex
	filetime_low_hex = hex(filetime_low)[2:]
	filetime_low_hex = '0' * (8 % len(filetime_low_hex)) + filetime_low_hex
	
	return filetime_high_hex.upper() + ':' + filetime_low_hex.upper()

def _xml_to_string(xml: Any) -> str:
	return lxml.etree.tostring(xml, pretty_print = True).decode('utf-8')

async def _preprocess_soap(req: web.Request) -> Tuple[Any, Any, Optional[BackendSession], str]:
	from lxml.objectify import fromstring as parse_xml
	
	body = await req.read()
	root = parse_xml(body)
	
	token = _find_element(root, 'TicketToken')
	if token[0:2] == 't=':
		token = token[2:22]
	
	backend_sess = req.app['backend'].util_get_sess_by_token(token)
	
	header = _find_element(root, 'Header')
	action = _find_element(root, 'Body/*[1]')
	
	return header, action, backend_sess, token

async def _preprocess_soap_rsi(req: web.Request) -> Tuple[Any, Any, Optional[BackendSession], str]:
	from lxml.objectify import fromstring as parse_xml
	
	body = await req.read()
	root = parse_xml(body)
	
	token_tag = root.find('.//{*}PassportCookie/{*}*[1]')
	if _get_tag_localname(token_tag) is not 't':
		token = None
	# TODO: Due to valtron's MSIDCRL DLL not supplying the ticket on certain SOAP services, ignore ticket for now.
	# Also, either implement the functions for those services or patch the original MSIDCRL.
	token = token_tag.text
	if token is not None and token[0:2] == 't=':
		token = token[2:22]
	
	backend_sess = req.app['backend'].util_get_sess_by_token(token)
	
	header = _find_element(root, 'Header')
	action = _find_element(root, 'Body/*[1]')
	
	return header, action, backend_sess, token

async def _preprocess_soap_oimws(req: web.Request) -> Tuple[Any, Any, Any, Optional[BackendSession], str]:
	from lxml.objectify import fromstring as parse_xml
	
	body = await req.read()
	root = parse_xml(body)
	
	token = root.find('.//{*}Ticket').get('passport')
	if token[0:2] == 't=':
		token = token[2:22]
	
	backend_sess = req.app['backend'].util_get_sess_by_token(token)
	
	header = _find_element(root, 'Header')
	body_msgtype = _find_element(root, 'Body/MessageType')
	body_content = _find_element(root, 'Body/Content')
	
	return header, body_msgtype, body_content, backend_sess, token

def _get_tag_localname(elm: Any) -> str:
	return lxml.etree.QName(elm.tag).localname

def _find_element(xml: Any, query: str) -> Any:
	thing = xml.find('.//{*}' + query.replace('/', '/{*}'))
	if isinstance(thing, lxml.objectify.StringElement):
		thing = str(thing)
	elif isinstance(thing, lxml.objectify.BoolElement):
		thing = bool(thing)
	return thing

async def handle_msgrconfig(req):
	msgr_config = _get_msgr_config()
	return web.Response(status = 200, content_type = 'text/xml', text = msgr_config)

def _get_msgr_config() -> str:
	with open(TMPL_DIR + '/MsgrConfigEnvelope.xml') as fh:
		envelope = fh.read()
	with open(TMPL_DIR + '/MsgrConfig.xml') as fh:
		config = fh.read()
	return envelope.format(MsgrConfig = config)

async def handle_nexus(req):
	return web.Response(status = 200, headers = {
		'PassportURLs': 'DALogin=https://{}{}'.format(settings.LOGIN_HOST, LOGIN_PATH),
	})

async def handle_login(req):
	tmp = _extract_pp_credentials(req.headers.get('Authorization'))
	if tmp is None:
		token = None
	else:
		email, pwd = tmp
		token = _login(req, email, pwd)
	if token is None:
		return web.Response(status = 401, headers = {
			'WWW-Authenticate': '{}da-status=failed'.format(PP),
		})
	return web.Response(status = 200, headers = {
		'Authentication-Info': '{}da-status=success,from-PP=\'{}\''.format(PP, token),
	})

async def handle_not_rst(req):
	if req.method == 'OPTIONS':
		return web.Response(status = 200, headers = {
			'Access-Control-Allow-Origin': '*',
			'Access-Control-Allow-Methods': 'POST',
			'Access-Control-Allow-Headers': 'X-User, X-Password, Content-Type',
			'Access-Control-Expose-Headers': 'X-Token',
			'Access-Control-Max-Age': '86400',
		})
	
	email = req.headers.get('X-User')
	pwd = req.headers.get('X-Password')
	token = _login(req, email, pwd)
	headers = {
		'Access-Control-Allow-Origin': '*',
		'Access-Control-Allow-Methods': 'POST',
		'Access-Control-Expose-Headers': 'X-Token',
	}
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
	now = datetime.utcnow()
	timez = now.isoformat()[0:19] + 'Z'
	
	if token is not None:
		tomorrowz = (now + timedelta(days = 1)).isoformat()[0:19] + 'Z'
		
		# load PUID and CID, assume them to be the same for our purposes
		cid = _cid_format(req.app['backend'].util_get_uuid_from_email(email))
		
		peername = req.transport.get_extra_info('peername')
		if peername:
			host = peername[0]
		else:
			host = '127.0.0.1'
		
		# get list of requested domains
		domains = root.findall('.//{*}Address')
		domains.pop(0) # ignore Passport token request
		
		tmpl = req.app['jinja_env'].get_template('msn:RST/RST.token.xml')
		# collect tokens for requested domains
		tokenxmls = [tmpl.render(
			i = i + 1,
			domain = domain,
			timez = timez,
			tomorrowz = tomorrowz,
			pptoken1 = token,
		) for i, domain in enumerate(domains)]
		
		tmpl = req.app['jinja_env'].get_template('msn:RST/RST.xml')
		return web.Response(
			status = 200,
			content_type = 'text/xml',
			text = tmpl.render(
				puidhex = cid.upper(),
				timez = timez,
				tomorrowz = tomorrowz,
				cid = cid,
				email = email,
				firstname = "John",
				lastname = "Doe",
				ip = host,
				pptoken1 = token,
				tokenxml = Markup(''.join(tokenxmls)),
			),
		)
	
	return render(req, 'msn:RST/RST.error.xml', {
		'timez': timez,
	}, status = 403)

def _get_storage_path(uuid: str) -> str:
	return 'storage/dp/{}/{}'.format(uuid[0:1], uuid[0:2])

def handle_create_document(req: web.Request, action: Any, user: models.User, cid: str, token: str, timestamp: int) -> web.Request:
	from PIL import Image
	
	# get image data
	name = _find_element(action, 'Name')
	streamtype = _find_element(action, 'DocumentStreamType')
	
	if streamtype == 'UserTileStatic':
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
	
	return render(req, 'msn:storageservice/CreateDocumentResponse.xml', {
		'user': user,
		'cid': cid,
		'pptoken1': token,
		'timestamp': timestamp,
	})

async def handle_usertile(req: web.Request, small: bool = False) -> web.Response:
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

async def handle_debug(req):
	return render(req, 'msn:debug.html')

def _extract_pp_credentials(auth_str: str) -> Optional[Tuple[str, str]]:
	if auth_str is None:
		return None
	assert auth_str.startswith(PP)
	auth = {}
	for part in auth_str[len(PP):].split(','):
		parts = part.split('=', 1)
		if len(parts) == 2:
			auth[unquote(parts[0])] = unquote(parts[1])
	email = auth['sign-in']
	pwd = auth['pwd']
	return email, pwd

def _login(req, email: str, pwd: str) -> Optional[str]:
	backend = req.app['backend'] # type: Backend
	uuid = backend.user_service.login(email, pwd)
	if uuid is None: return None
	return backend.auth_service.create_token('nb/login', uuid)

def _date_format(d: Optional[datetime]) -> Optional[str]:
	if d is None: return None
	return d.isoformat()[0:19] + 'Z'

def _cid_format(uuid: str, *, decimal: bool = False) -> str:
	cid = (uuid[0:8] + uuid[28:36])[::-1].lower()
	
	if not decimal:
		return cid
	
	# convert to decimal string
	return str(int(cid, 16))

def _bool_to_str(b: bool) -> str:
	return 'true' if b else 'false'

def _contact_is_favorite(user_detail: models.UserDetail, ctc: models.Contact) -> bool:
	groups = user_detail.groups
	for group_id in ctc.groups:
		if group_id not in groups: continue
		if groups[group_id].is_favorite: return True
	return False

OIM_HEADER_PRE = '''X-Message-Info: cwRBnLifKNE8dVZlNj6AiX8142B67OTjG9BFMLMyzuui1H4Xx7m3NQ==
Received: from OIM-SSI02.phx.gbl ([65.54.237.206]) by oim1-f1.hotmail.com with Microsoft SMTPSVC(6.0.3790.211);
	 {pst1}
Received: from mail pickup service by OIM-SSI02.phx.gbl with Microsoft SMTPSVC;
	 {pst1}
From: {friendly} <{sender}>
To: {recipient}
Subject: 
X-OIM-originatingSource: {ip}
X-OIMProxy: MSNMSGR
'''

OIM_HEADER_REST = '''
Message-ID: <OIM-SSI02zDv60gxapz00061a8b@OIM-SSI02.phx.gbl>
X-OriginalArrivalTime: {utc} FILETIME=[{ft}]
Date: {pst2}
Return-Path: ndr@oim.messenger.msn.com'''
