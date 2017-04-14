from aiohttp import web
from ctrl_auth import create_app

def main():
	app = create_app()
	web.run_app(app, port = 80)

if __name__ == '__main__':
	main()
