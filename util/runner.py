def run_everything(*, http_stuff = None, nb_port = None, sb_services = None, devmode = False):
	from functools import partial
	import asyncio
	from util.misc import AIOHTTPRunner, Logger
	from util.user import UserService
	from util.auth import AuthService
	from util.conn import MSNPConn
	import ctrl_nb, ctrl_sb, ctrl_auth
	
	user_service = UserService()
	auth_service = AuthService()
	loop = asyncio.get_event_loop()
	
	nb = ctrl_nb.NB(user_service, auth_service, sb_services)
	sbs = [
		(sb_service.port, ctrl_sb.SB(user_service, auth_service))
		for sb_service in sb_services
	]
	a_auth_http = AIOHTTPRunner(ctrl_auth.create_app(user_service, auth_service))
	
	coros = [
		loop.create_server(a_auth_http.setup(), http_stuff[0], http_stuff[1]),
		loop.create_server(partial(MSNPConn, Logger('nb'), partial(ctrl_nb.NBConn, nb)), '0.0.0.0', nb_port),
		*(
			loop.create_server(partial(MSNPConn, Logger('sb'), partial(ctrl_sb.SBConn, sb, nb)), '0.0.0.0', port)
			for port, sb in sbs
		)
	]
	
	if devmode:
		from dev import autossl
		if not autossl.perform_checks():
			return
		ssl_context = autossl.create_context()
		a_auth_https = AIOHTTPRunner(ctrl_auth.create_app(user_service, auth_service))
		coros.append(loop.create_server(a_auth_https.setup(), '0.0.0.0', 443, ssl = ssl_context))
	else:
		a_auth_https = None
	
	servers = loop.run_until_complete(asyncio.gather(*coros))
	
	for server in servers:
		print("Serving on {}".format(server.sockets[0].getsockname()))
	try:
		loop.run_forever()
	except KeyboardInterrupt:
		pass
	
	for server in servers:
		server.close()
	loop.run_until_complete(asyncio.gather(*(
		server.wait_closed() for server in servers
	)))
	if a_auth_https:
		a_auth_https.teardown(loop)
	a_auth_http.teardown(loop)
	loop.close()
