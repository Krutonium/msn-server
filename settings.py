class Service:
	def __init__(self, host, port):
		self.host = host
		self.port = port

NB = Service('messenger-0001.now.im', 1863)
SB = [
	Service('messenger-0001.now.im', 1864),
]

LOGIN_HOST = 'messenger-0001.now.im'
DEBUG = True
DEV_ACCEPT_ALL_LOGIN_TOKENS = False

try:
	from settings_local import *
except ImportError:
	raise Exception("Please create settings_local.py")
