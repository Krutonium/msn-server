def main(*, devmode: bool = False) -> None:
	import asyncio
	from core.backend import Backend
	from core import http
	import settings
	
	loop = asyncio.get_event_loop()
	backend = Backend(loop)
	http_app = http.register(loop, backend, devmode = devmode)
	
	if settings.ENABLE_FRONT_MSN:
		import front.msn
		front.msn.register(loop, backend, http_app)
	if settings.ENABLE_FRONT_YMSG:
		import front.ymsg
		front.ymsg.register(loop, backend, http_app)
	if settings.ENABLE_FRONT_IRC:
		import front.irc
		front.irc.register(loop, backend)
	if settings.ENABLE_FRONT_BOT:
		import front.bot
		front.bot.register(loop, backend)
	
	if devmode:
		if settings.ENABLE_FRONT_DEVBOTS:
			import front.devbots
			front.devbots.register(loop, backend)
	
	backend.run_forever()

if __name__ == '__main__':
	main()
