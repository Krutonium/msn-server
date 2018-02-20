from aiohttp import web
from markupsafe import Markup

import util.misc
import time

YAHOO_TMPL_DIR = 'front/ymsg/tmpl'

def register(app):
	app['jinja_env_yahoo'] = util.misc.create_jinja_env(YAHOO_TMPL_DIR, None)
	
	app.router.add_route('*', '/', handle_insider)
	app.router.add_route('*', '/ycontent/', handle_insider_ycontent)
	app.router.add_static('/c/msg', YAHOO_TMPL_DIR + '/c/msg')

async def handle_insider_ycontent(req):
	query = req.query
	print(query)
	config_xml = []
	if len(query) != 0:
		for query_xml in query.keys():
			# 'intl' and 'os' are NOT queries to retreive config XML files, ignore
			if query_xml in ('intl', 'os', 'ver', 'getwc', 'getgp'): continue
			tmpl = req.app['jinja_env_yahoo'].get_template('Yinsider/Ycontent/Ycontent.' + query_xml + '.xml')
			config_xml.append(tmpl.render())
	
	config_xml = '\n'.join(config_xml)
	
	return render(req, 'Yinsider/Ycontent/Ycontent.xml', {
		'epoch': round(time.time()),
		'configxml': Markup(config_xml),
	})

async def handle_insider(req):
	tmpl = req.app['jinja_env_yahoo'].get_template('Yinsider/Yinsider_content/insider_content.html')
	
	return render(req, 'Yinsider/Yinsider.html', {
		'insidercontent': Markup(tmpl.render())
	})

async def handle_banad(req):
	query = req.query
	
	return render(req, 'c/msg/banad.html', {
		'spaceid': (query.get('spaceid') if query.get('spaceid') is not None else '0 robot')
	})

def render(req, tmpl_name, ctxt = None, status = 200):
	if tmpl_name.endswith('.xml'):
		content_type = 'text/xml'
	else:
		content_type = 'text/html'
	tmpl = req.app['jinja_env_yahoo'].get_template(tmpl_name)
	content = tmpl.render(**(ctxt or {})).replace('\n', '\r\n')
	return web.Response(status = status, content_type = content_type, text = content)