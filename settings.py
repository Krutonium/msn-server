class Service:
	def __init__(self, host, port):
		self.host = host
		self.port = port

NB = Service('m1.escargot.log1p.xyz', 1863)
SB = [
	Service('m1.escargot.log1p.xyz', 1864),
]

LOGIN_HOST = 'm1.escargot.log1p.xyz'
DEBUG = True
DEV_ACCEPT_ALL_LOGIN_TOKENS = False

try:
	from settings_local import *
except ImportError:
	raise Exception("Please create settings_local.py")

if DEV_ACCEPT_ALL_LOGIN_TOKENS:
	NB = Service('localhost', 1863)
	SB = [
		Service('localhost', 1864),
	]
