def main():
	from dev import autossl
	if not autossl.perform_checks():
		return
	
	import asyncio
	from core.unorganized import NotificationServer
	from core.user import UserService
	from core.auth import AuthService
	from util.misc import run_loop
	import front.msn
	
	user_service = UserService()
	auth_service = AuthService()
	
	loop = asyncio.get_event_loop()
	ns = NotificationServer(loop, user_service, auth_service)
	front.msn.register(loop, ns)
	run_loop(loop)

if __name__ == '__main__':
	main()
