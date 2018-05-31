from typing import Any, Dict, List, Optional, Tuple
from aiohttp import web
import asyncio
from markupsafe import Markup
from urllib.parse import unquote, unquote_plus
import os
import datetime
import shutil

from core.backend import Backend, BackendSession
import util.misc
from .ymsg_ctrl import _decode_ymsg
from .misc import YMSGService, yahoo_id_to_uuid, yahoo_id
import time

YAHOO_TMPL_DIR = 'front/ymsg/tmpl'

def register(app: web.Application) -> None:
	util.misc.add_to_jinja_env(app, 'ymsg', YAHOO_TMPL_DIR)
	
	# Yahoo! Insider
	# TODO: `*` routes need to also match on host
	app.router.add_route('*', '/', handle_insider)
	app.router.add_get('/ycontent/', handle_insider_ycontent)
	
	# Yahoo! Chat/Ads
	app.router.add_route('*', '/c/msg/banad.html', handle_chat_banad)
	app.router.add_route('*', '/c/msg/tabs.html', handle_chat_tabad)
	app.router.add_route('*', '/c/msg/chat.html', handle_chat_notice)
	app.router.add_route('*', '/c/msg/alerts.html', handle_chat_alertad)
	app.router.add_static('/c/msg/chat_img', YAHOO_TMPL_DIR + '/c/msg/chat_img')
	app.router.add_static('/c/msg/ad_img', YAHOO_TMPL_DIR + '/c/msg/ad_img')
	
	# Yahoo!'s redirector to cookie-based services
	app.router.add_route('*', '/config/reset_cookies', handle_cookies_redirect)
	
	# Yahoo! Messenger alias service
	app.router.add_route('*', '/config/edit_identity', handle_yahoo_alias_service)
	app.router.add_post('/config/alias/cgi/create_alias', handle_yahoo_alias_create)
	app.router.add_post('/config/alias/cgi/delete_alias', handle_yahoo_alias_delete)
	app.router.add_static('/config/alias/img', YAHOO_TMPL_DIR + '/yh_config/alias/img')
	app.router.add_static('/config/alias/css', YAHOO_TMPL_DIR + '/yh_config/alias/css')
	
	# Yahoo!'s redirect service (rd.yahoo.com)
	app.router.add_get('/messenger/search/', handle_rd_yahoo)
	app.router.add_get('/messenger/client/', handle_rd_yahoo)
	
	# Yahoo HTTP file transfer fallback
	app.router.add_post('/notifyft', handle_ft_http)
	app.router.add_route('*', '/tmp/file/{file_uuid}/{filename}', handle_yahoo_filedl)

async def handle_insider_ycontent(req: web.Request) -> web.Response:
	config_xml = []
	for query_xml in req.query.keys():
		# Ignore any `chatroom_##########` requests for now
		if query_xml in UNUSED_QUERIES or query_xml.startswith('chatroom_'): continue
		tmpl = req.app['jinja_env'].get_template('ymsg:Yinsider/Ycontent/Ycontent.' + query_xml + '.xml')
		config_xml.append(tmpl.render())
	
	return render(req, 'ymsg:Yinsider/Ycontent/Ycontent.xml', {
		'epoch': round(time.time()),
		'configxml': Markup('\n'.join(config_xml)),
	})

# 'intl', 'os', and 'ver' are NOT queries to retrieve config XML files;
# 'getwc' and 'getgp' are unsure of their use;
# 'ab2' and all related query strings are used for the address book, which isn't implemented as of now
UNUSED_QUERIES = {
	'intl', 'os', 'ver',
	'getwc', 'getgp', 'ab2',
	'fname', 'lname', 'yid',
	'nname', 'email', 'hphone',
	'wphone', 'mphone', 'pp',
	'ee', 'ow', 'id',
}

async def handle_insider(req: web.Request) -> web.Response:
	tmpl = req.app['jinja_env'].get_template('ymsg:Yinsider/Yinsider_content/insider_content.html')
	
	return render(req, 'ymsg:Yinsider/Yinsider.html', {
		'insidercontent': Markup(tmpl.render()),
	})

async def handle_chat_banad(req: web.Request) -> web.Response:
	query = req.query
	
	return render(req, 'ymsg:c/msg/banad.html', {
		'spaceid': (query.get('spaceid') or 0),
	})

async def handle_chat_tabad(req: web.Request) -> web.Response:
	query = req.query
	
	return render(req, 'ymsg:c/msg/adsmall.html', {
		'adtitle': 'banner ad',
		'spaceid': (query.get('spaceid') or 0),
	})

async def handle_chat_alertad(req: web.Request) -> web.Response:
	query = req.query
	
	return render(req, 'ymsg:c/msg/adsmall.html', {
		'adtitle': 'alert ad usmsgr',
		'spaceid': (query.get('spaceid') or 0),
	})

async def handle_chat_notice(req: web.Request) -> web.Response:
	return render(req, 'ymsg:c/msg/chat.html')

async def handle_rd_yahoo(req: web.Request) -> web.Response:
	return web.HTTPFound(req.query_string.replace(' ', '+'))

async def handle_cookies_redirect(req: web.Request) -> web.Response:
	# Retreive the `Y` and `T` cookies.
	
	query = req.query
	backend = req.app['backend']
	
	y_cookie = query.get('.y')
	t_cookie = query.get('.t')
	
	(yahoo_id, bs) = _parse_cookies(req, backend, y = y_cookie[2:], t = t_cookie[2:])
	if bs is None or yahoo_id is None:
		raise web.HTTPInternalServerError
	
	redir_to = query.get('.done')
	
	return _redir_with_auth_cookies(redir_to, y_cookie[2:], t_cookie[2:], backend)

async def handle_yahoo_alias_service(req: web.Request) -> web.Response:
	backend = req.app['backend']
	query = req.query
	
	(yahoo_id, bs) = _parse_cookies(req, backend)
	
	if yahoo_id != query.get('.l') or bs is None:
		raise web.HTTPInternalServerError
	
	aliases = backend.user_service.yahoo_get_aliases(bs.user.uuid)
	
	if aliases is not None and len(aliases) > 0:
		tmpl = req.app['jinja_env'].get_template('ymsg:yh_config/alias/aliascmdbrd.aliasentry.html')
		alias_tags = [tmpl.render(alias = alias) for alias in aliases]
	else:
		tmpl = req.app['jinja_env'].get_template('ymsg:yh_config/alias/aliascmdbrd.noalias.html')
		alias_tags = [tmpl.render()]
	
	return render(req, 'ymsg:yh_config/alias/aliascmdbrd.html', {
		'y_cookie': req.cookies.get('Y'),
		't_cookie': req.cookies.get('T'),
		'alias_tags': Markup(''.join(alias_tags)),
		'main_yid': query.get('.l'),
	})

async def handle_yahoo_alias_create(req: web.Request) -> web.Response:
	body = await req.read()
	
	backend = req.app['backend']
	params = _parse_urlencoded(body)
	
	(id, bs) = _parse_cookies(req, backend, y = params['Y'], t = params['T'])
	
	if id != params['id'] or bs is None:
		raise web.HTTPInternalServerError
	
	for bs_other in bs.backend._sc.iter_sessions():
		alias_list = backend.user_service.yahoo_get_aliases(bs_other.user.uuid) or []
		if params['alias_new'] == yahoo_id(bs_other.user.email) or params['alias_new'] in alias_list:
			return _redir_with_auth_cookies('/config/edit_identity?.done=http://messenger.yahoo.com/&.l=' + params['id'] + '&.err=taken', params['Y'], params['T'], backend)
	
	backend.user_service.yahoo_add_alias(bs.user.uuid, params['alias_new'])
	# TODO: RN, we send an `IDActivate` to the client when an alias is created. Is this the appropriate action?
	bs.evt.ymsg_on_notify_alias_activate(params['alias_new'])
	
	return _redir_with_auth_cookies('/config/edit_identity?.done=http://messenger.yahoo.com/&.l=' + params['id'] + '&.succeed=', params['Y'], params['T'], backend)

async def handle_yahoo_alias_delete(req: web.Request) -> web.Response:
	return web.HTTPOk()

def _parse_urlencoded(body: bytes) -> Dict[str, Any]:
	param_dict = {}
	
	for param in body.decode().split('&'):
		param_two = param.split('=')
		for i in range(1, len(param_two), 2): param_dict[param_two[i - 1]] = unquote(param_two[i])
	
	return param_dict

def _redir_with_auth_cookies(loc: str, y: str, t: str, backend: Backend) -> web.Response:
	resp = web.Response(status = 302, headers = {
		'Location': loc,
	})
	
	y_expiry = datetime.datetime.utcfromtimestamp(backend.auth_service.get_token_expiry('ymsg/cookie', y)).strftime('%a, %d %b %Y %H:%M:%S GMT')
	t_expiry = datetime.datetime.utcfromtimestamp(backend.auth_service.get_token_expiry('ymsg/cookie', t)).strftime('%a, %d %b %Y %H:%M:%S GMT') 
	
	# TODO: Replace '.yahoo.com' with '.log1p.xyz' when patched Yahoo! Messenger files are released.
	resp.set_cookie('Y', y, path = '/', expires = y_expiry, domain = '.yahoo.com')
	resp.set_cookie('T', t, path = '/', expires = t_expiry, domain = '.yahoo.com')
	
	return resp

async def handle_ft_http(req: web.Request) -> web.Response:
	body = await req.read()
	
	# Look for incomplete key-value field `29`
	stream_loc = body.find(b'29\xC0\x80')
	stream = body[(stream_loc + 4):]
	
	# Parse the rest of the YMSG packet
	raw_ymsg_data = body[:stream_loc]
	
	# Now change the length field as fit to get the YMSG parser to gobble it up
	import struct
	
	raw_ymsg_part_pre = raw_ymsg_data[0:8]
	raw_ymsg_part_post = raw_ymsg_data[10:]
	
	raw_ymsg_data = raw_ymsg_part_pre + struct.pack('!H', len(raw_ymsg_part_post[10:])) + raw_ymsg_part_post
	
	backend = req.app['backend']
	
	try:
		y_ft_pkt = _decode_ymsg(raw_ymsg_data)
	except Exception:
		raise web.HTTPInternalServerError
	
	try:
		# check version and vendorId
		if y_ft_pkt[1] > 16 or y_ft_pkt[2] not in (0, 100):
			raise web.HTTPInternalServerError
	except Exception:
		raise web.HTTPInternalServerError
	
	if y_ft_pkt[0] is not YMSGService.FileTransfer:
		raise web.HTTPInternalServerError
	
	ymsg_data = y_ft_pkt[5]
	
	yahoo_id_sender = ymsg_data.get('0') or ''
	(yahoo_id, bs) = _parse_cookies(req, backend, yahoo_id_sender)
	if bs is None or (yahoo_id != yahoo_id_sender or not yahoo_id_to_uuid(None, backend, yahoo_id)):
		raise web.HTTPInternalServerError
	
	yahoo_id_recipient = ymsg_data.get('5') or ''
	recipient_uuid = yahoo_id_to_uuid(bs, backend, yahoo_id_recipient)
	if recipient_uuid is None:
		raise web.HTTPInternalServerError
	
	message = ymsg_data.get('14') or ''
	
	file_path = ymsg_data.get('27')
	file_len = ymsg_data.get('28') or 0
	
	if file_path is None or len(stream) != int(file_len) or len(stream) > (2 * (1000 ** 3)):
		raise web.HTTPInternalServerError
	
	filename = file_path.split('\\').pop()
	
	path = _get_tmp_file_storage_path()
	
	if not os.path.exists(path):
		os.makedirs(path)
	
	file_tmp_path = '{path}/{file}'.format(
		path = path,
		file = unquote_plus(filename),
	)
	
	f = open(file_tmp_path, 'wb')
	f.write(stream)
	f.close()
	
	upload_time = time.time()
	
	req.app.loop.create_task(_store_tmp_file_until_expiry(path))
	
	# Sending HTTP FT acknowledgement crahes Yahoo! Messenger, and ultimately freezes the computer. Ignore for now.
	# bs.evt.ymsg_on_upload_file_ft(yahoo_id_recipient, message)
	
	for bs_other in bs.backend._sc.iter_sessions():
		if bs_other.user.uuid == recipient_uuid:
			bs_other.evt.ymsg_on_sent_ft_http(yahoo_id_sender, file_tmp_path[12:], upload_time, message)
	
	raise web.HTTPOk

async def _store_tmp_file_until_expiry(file_storage_path: str) -> None:
	await asyncio.sleep(3600)
	# When an hour passes, delete the file unless it has already been deleted from downloading it
	if os.path.exists(file_storage_path):
		shutil.rmtree(file_storage_path, ignore_errors = True)

async def handle_yahoo_filedl(req: web.Request) -> web.Response:
	file_uuid = req.match_info['file_uuid']
	file_storage_path = _get_tmp_file_storage_path(uuid = file_uuid)
	
	try:
		filename = req.match_info['filename']
		file_path = os.path.join(file_storage_path, unquote_plus(filename))
		
		with open(file_path, 'rb') as file:
			file_stream = file.read()
			file.close()
			shutil.rmtree(file_storage_path, ignore_errors = True)
			return web.HTTPOk(body = file_stream)
	except FileNotFoundError:
		raise web.HTTPNotFound

def _get_tmp_file_storage_path(uuid: Optional[str] = None) -> str:
	return 'storage/yfs/{}'.format(util.misc.gen_uuid() if uuid is None else uuid)

def _parse_cookies(req: web.Request, backend: Backend, y: Optional[str] = None, t: Optional[str] = None) -> Tuple[Optional[str], Optional[BackendSession]]:
	cookies = req.cookies
	
	if None in (y,t):
		y_cookie = cookies.get('Y')
		t_cookie = cookies.get('T')
	else:
		y_cookie = y
		t_cookie = t
	
	return (backend.auth_service.get_token('ymsg/cookie', y_cookie), backend.auth_service.get_token('ymsg/cookie', t_cookie))

def render(req: web.Request, tmpl_name: str, ctxt: Optional[Dict[str, Any]] = None, status: int = 200) -> web.Response:
	if tmpl_name.endswith('.xml'):
		content_type = 'text/xml'
	else:
		content_type = 'text/html'
	tmpl = req.app['jinja_env'].get_template(tmpl_name)
	content = tmpl.render(**(ctxt or {})).replace('\n', '\r\n')
	return web.Response(status = status, content_type = content_type, text = content)
