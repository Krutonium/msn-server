from typing import Dict, Optional
from aiohttp import web

from util.misc import Logger, gen_uuid
from front.msn.msnp import MSNPCtrl

def register(app):
	# TODO: How to clean these out when they're closed?
	app['gateway_sessions'] = {}
	app.router.add_route('OPTIONS', '/gateway/gateway.dll', handle_http_gateway_options)
	app.router.add_post('/gateway/gateway.dll', handle_http_gateway)

class GatewaySession:
	__slots__ = ('logger', 'hostname', 'controller')
	
	logger: Logger
	hostname: str
	controller: MSNPCtrl
	
	def __init__(self, logger: Logger, hostname: str, controller: MSNPCtrl) -> None:
		self.logger = logger
		self.hostname = hostname
		self.controller = controller

async def handle_http_gateway_options(req):
	return web.Response(status = 200, headers = {
		'Access-Control-Allow-Origin': '*',
		'Access-Control-Allow-Methods': 'POST',
		'Access-Control-Allow-Headers': 'Content-Type',
		'Access-Control-Expose-Headers': 'X-MSN-Messenger',
		'Access-Control-Max-Age': '86400',
	})

async def handle_http_gateway(req):
	from front.msn.msnp_ns import MSNPCtrlNS
	from front.msn.msnp_sb import MSNPCtrlSB
	
	query = req.query
	session_id = query.get('SessionID')
	backend = req.app['backend']
	gateway_sessions = req.app['gateway_sessions'] # type: Dict[str, GatewaySession]
	
	if not session_id:
		# Create new GatewaySession
		server_type = query.get('Server')
		server_ip = query.get('IP')
		session_id = gen_uuid()
		
		logger = Logger('GW-{}'.format(server_type), session_id)
		
		if server_type == 'NS':
			controller = MSNPCtrlNS(logger, 'gw', backend) # type: MSNPCtrl
		else:
			controller = MSNPCtrlSB(logger, backend)
		
		gateway_sessions[session_id] = GatewaySession(logger, server_ip, controller)
	gwsess = gateway_sessions.get(session_id)
	if gwsess is None:
		return web.Response(status = 400, text = '')
	
	gwsess.logger.log_connect()
	gwsess.controller.data_received(await req.body())
	gwsess.logger.log_disconnect()
	body = gwsess.controller.flush()
	
	return web.Response(headers = {
		'Access-Control-Allow-Origin': '*',
		'Access-Control-Allow-Methods': 'POST',
		'Access-Control-Expose-Headers': 'X-MSN-Messenger',
		'X-MSN-Messenger': 'SessionID={}; GW-IP={}'.format(session_id, gwsess.hostname),
		'Content-Type': 'application/x-msn-messenger',
	}, body = body)
