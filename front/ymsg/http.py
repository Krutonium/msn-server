from typing import Any, Dict, Optional
from aiohttp import web
from markupsafe import Markup

import util.misc
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
	
	# Yahoo!'s redirect service (rd.yahoo.com)
	app.router.add_get('/messenger/search/', handle_rd_yahoo)
	app.router.add_get('/messenger/client/', handle_rd_yahoo)

async def handle_insider_ycontent(req):
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

async def handle_insider(req):
	tmpl = req.app['jinja_env'].get_template('ymsg:Yinsider/Yinsider_content/insider_content.html')
	
	return render(req, 'ymsg:Yinsider/Yinsider.html', {
		'insidercontent': Markup(tmpl.render()),
	})

async def handle_chat_banad(req):
	query = req.query
	
	return render(req, 'ymsg:c/msg/banad.html', {
		'spaceid': (0 if not query.get('spaceid') else query.get('spaceid')),
	})

async def handle_chat_tabad(req):
	query = req.query
	
	return render(req, 'ymsg:c/msg/adsmall.html', {
		'adtitle': 'banner ad',
		'spaceid': (0 if not query.get('spaceid') else query.get('spaceid')),
	})

async def handle_chat_alertad(req):
	query = req.query
	
	return render(req, 'ymsg:c/msg/adsmall.html', {
		'adtitle': 'alert ad usmsgr',
		'spaceid': (0 if not query.get('spaceid') else query.get('spaceid')),
	})

async def handle_chat_notice(req):
	return render(req, 'ymsg:c/msg/chat.html')

async def handle_rd_yahoo(req):
	return web.Response(status = 302, headers = {
		'Location': req.query_string,
	})

def render(req: web.Request, tmpl_name: str, ctxt: Optional[Dict[str, Any]] = None, status: int = 200) -> web.Response:
	if tmpl_name.endswith('.xml'):
		content_type = 'text/xml'
	else:
		content_type = 'text/html'
	tmpl = req.app['jinja_env'].get_template(tmpl_name)
	content = tmpl.render(**(ctxt or {})).replace('\n', '\r\n')
	return web.Response(status = status, content_type = content_type, text = content)
