def main():
	from functools import partial
	import asyncio
	import ctrl_nb, ctrl_sb, settings
	
	loop = asyncio.get_event_loop()
	nb = ctrl_nb.NB(loop, settings.SB)
	sb = ctrl_sb.SB()
	servers = loop.run_until_complete(asyncio.gather(
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
	loop.close()

if __name__ == '__main__':
	main()
