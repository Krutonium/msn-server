from aiohttp import web
from markupsafe import Markup

import util.misc
import time

YAHOO_TMPL_DIR = 'front/ymsg/tmpl'

def register(app):
	app['jinja_env_yahoo'] = util.misc.create_jinja_env(YAHOO_TMPL_DIR, None)
	
	app.router.add_route('*', '/', handle_insider)
	app.router.add_get('/ycontent/', handle_insider_ycontent)
	app.router.add_static('/c/msg', YAHOO_TMPL_DIR + '/c/msg')

async def handle_insider_ycontent(req):
	query = req.query
	
	config_xml = []
	if len(query) != 0:
		for query_xml in query.keys():
			# 'intl', 'os', and 'ver' are NOT queries to retrieve config XML files; 'getwc' and 'getgp' are unsure of their use; 'ab2' and all related query strings are used for the address book, which isn't implemented as of now
			unused_queries = (
				'intl', 'os', 'ver',
				'getwc', 'getgp', 'ab2',
				'fname', 'lname', 'yid',
				'nname', 'email', 'hphone',
				'wphone', 'mphone', 'pp',
				'ee', 'ow', 'id',
			)
			
			if query_xml in unused_queries: continue
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

def render(req, tmpl_name, ctxt = None, status = 200):
	if tmpl_name.endswith('.xml'):
		content_type = 'text/xml'
	else:
		content_type = 'text/html'
	tmpl = req.app['jinja_env_yahoo'].get_template(tmpl_name)
	content = tmpl.render(**(ctxt or {})).replace('\n', '\r\n')
	return web.Response(status = status, content_type = content_type, text = content)
