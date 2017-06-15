def main():
	from functools import partial
	import asyncio
	from util.misc import AIOHTTPRunner
	from util.auth import AuthService
	import ctrl_nb, ctrl_sb, ctrl_auth, settings
	from dev import autossl
	
	if not autossl.perform_checks():
		return
	
	ssl_context = autossl.create_context()
	auth_service = AuthService()
	loop = asyncio.get_event_loop()
	
	nb = ctrl_nb.NB(loop, auth_service, settings.SB)
	sb = ctrl_sb.SB(auth_service)
	a_auth_https = AIOHTTPRunner(ctrl_auth.create_app(auth_service))
	a_auth_http = AIOHTTPRunner(ctrl_auth.create_app(auth_service))
	
	servers = loop.run_until_complete(asyncio.gather(
		loop.create_server(a_auth_https.setup(loop), '0.0.0.0', 443, ssl = ssl_context),
		loop.create_server(a_auth_http.setup(loop), '0.0.0.0', 80),
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
	a_auth_https.teardown(loop)
	a_auth_http.teardown(loop)
	loop.close()

if __name__ == '__main__':
	main()
