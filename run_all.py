def main():
	from functools import partial
	import asyncio
	import ctrl_nb, ctrl_sb, settings, serv_auth
	
	loop = asyncio.get_event_loop()
	
	nb = ctrl_nb.NB(loop, settings.SB)
	sb = ctrl_sb.SB()
	a_auth = AIOHTTPRunner(serv_auth.create_app())
	
	servers = loop.run_until_complete(asyncio.gather(
		loop.create_server(a_auth.setup(loop), '127.0.0.1', 8081),
		loop.create_server(partial(ctrl_nb.NBConn, nb), '0.0.0.0', settings.NB.port),
		loop.create_server(partial(ctrl_sb.SBConn, sb, nb), '0.0.0.0', settings.SB[0].port),
	))
	
	for server in servers:
		print("Serving on {}".format(server.sockets[0].getsockname()))
	try:
		loop.run_forever()
	except KeyboardInterrupt:
		pass
	
	for server in servers:
		server.close()
	loop.run_until_complete(asyncio.gather(
		server.wait_closed() for server in servers
	))
	a_auth.teardown(loop)
	loop.close()

class AIOHTTPRunner:
	def __init__(self, app):
		self.app = app
		self.handler = None
	
	def setup(self, loop):
		from aiohttp.log import access_logger
		self.handler = self.app.make_handler(loop = loop, access_log = access_logger)
		loop.run_until_complete(self.app.startup())
		return self.handler
	
	def teardown(self, loop, shutdown_timeout = 60):
		loop.run_until_complete(self.app.shutdown())
		loop.run_until_complete(self.handler.shutdown(shutdown_timeout))
		loop.run_until_complete(self.app.cleanup())

if __name__ == '__main__':
	main()
