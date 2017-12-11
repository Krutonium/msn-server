def main(*, devmode = False):
	import asyncio
	from core.backend import Backend
	import front.msn
	import front.ymsg
	
	if devmode:
		http_port = 80
	else:
		http_port = 8081
	
	loop = asyncio.get_event_loop()
	backend = Backend(loop)
	front.msn.register(loop, backend, http_port = http_port, devmode = devmode)
	front.ymsg.register(loop, backend)
	backend.run_forever()

if __name__ == '__main__':
	main()
