def main(*, devmode = False):
	import asyncio
	from core.backend import Backend
	import front.msn
	import front.ymsg
	import front.bot
	import http_server
	import settings
	
	if devmode:
		http_port = 80
	else:
		http_port = 8081
	
	loop = asyncio.get_event_loop()
	backend = Backend(loop)
	msn_http_enable = False
	yahoo_http_enable = False
	if settings.ENABLE_FRONT_MSN:
		front.msn.register(loop, backend)
		msn_http_enable = True
	if settings.ENABLE_FRONT_YMSG:
		front.ymsg.register(loop, backend)
		yahoo_http_enable = True
	http_server.register(loop, backend, http_port = http_port, devmode = devmode, msn_http = msn_http_enable, yahoo_http = yahoo_http_enable)
	if settings.ENABLE_FRONT_BOT:
		front.bot.register(loop, backend)
	backend.run_forever()

if __name__ == '__main__':
	main()