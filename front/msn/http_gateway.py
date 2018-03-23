from typing import Dict, Optional
import time
import asyncio
from aiohttp import web

from util.misc import Logger, gen_uuid
from front.msn.msnp import MSNPCtrl

def register(loop: asyncio.AbstractEventLoop, app: web.Application) -> None:
	gateway_sessions = {} # type: Dict[str, GatewaySession]
	app['gateway_sessions'] = gateway_sessions
	app.router.add_route('OPTIONS', '/gateway/gateway.dll', handle_http_gateway_options)
	app.router.add_post('/gateway/gateway.dll', handle_http_gateway)
	
	loop.create_task(_clean_gateway_sessions(gateway_sessions))

async def _clean_gateway_sessions(gateway_sessions: Dict[str, 'GatewaySession']) -> None:
	while True:
		await asyncio.sleep(10)
		now = time.time()
		closed = []
		for session_id, gwsess in gateway_sessions.items():
			if gwsess.time_last_connect + gwsess.timeout <= now:
				gwsess.controller.close()
				closed.append(session_id)
		for session_id in closed:
			del gateway_sessions[session_id]

class GatewaySession:
	__slots__ = ('logger', 'hostname', 'controller', 'timeout', 'time_last_connect')
	
	logger: Logger
	hostname: str
	controller: MSNPCtrl
	timeout: float
	time_last_connect: float
	
	def __init__(self, logger: Logger, hostname: str, controller: MSNPCtrl, now: float) -> None:
		self.logger = logger
		self.hostname = hostname
		self.controller = controller
		self.timeout = 60
		self.time_last_connect = now
	
	def _on_close(self) -> None:
		self.time_last_connect = 0

async def handle_http_gateway_options(req):
	return web.Response(status = 200, headers = {
		'Access-Control-Allow-Origin': '*',
		'Access-Control-Allow-Methods': 'POST',
		'Access-Control-Allow-Headers': 'Content-Type',
		'Access-Control-Expose-Headers': 'X-MSN-Messenger',
		'Access-Control-Max-Age': '86400',
	})

async def handle_http_gateway(req):
	query = req.query
	session_id = query.get('SessionID')
	backend = req.app['backend']
	gateway_sessions = req.app['gateway_sessions'] # type: Dict[str, GatewaySession]
	now = time.time()
	
	if not session_id:
		from front.msn.msnp_ns import MSNPCtrlNS
		from front.msn.msnp_sb import MSNPCtrlSB
		
		# Create new GatewaySession
		server_type = query.get('Server')
		server_ip = query.get('IP')
		session_id = gen_uuid()
		
		logger = Logger('GW-{}'.format(server_type), session_id)
		
		if server_type == 'NS':
			controller = MSNPCtrlNS(logger, 'gw', backend) # type: MSNPCtrl
		else:
			controller = MSNPCtrlSB(logger, 'gw', backend)
		
		tmp = GatewaySession(logger, server_ip, controller, now)
		controller.close_callback = tmp._on_close
		gateway_sessions[session_id] = tmp
	gwsess = gateway_sessions.get(session_id)
	if gwsess is None:
		return web.Response(status = 400, text = '')
	
	gwsess.logger.log_connect()
	gwsess.controller.data_received(req.transport, await req.body())
	gwsess.logger.log_disconnect()
	body = gwsess.controller.flush()
	
	return web.Response(headers = {
		'Access-Control-Allow-Origin': '*',
		'Access-Control-Allow-Methods': 'POST',
		'Access-Control-Expose-Headers': 'X-MSN-Messenger',
		'X-MSN-Messenger': 'SessionID={}; GW-IP={}'.format(session_id, gwsess.hostname),
		'Content-Type': 'application/x-msn-messenger',
	}, body = body)
