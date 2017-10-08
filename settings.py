from models import Service

NB = Service('m1.escargot.log1p.xyz', 1863)
SB = [
	Service('m1.escargot.log1p.xyz', 1864),
	# Right now, code ignores any SBs after the first
]

DB = 'sqlite:///msn.sqlite'
LOGIN_HOST = 'm1.escargot.log1p.xyz'
DEBUG = True
STORAGE_HOST = 'm1.escargot.log1p.xyz'

try:
	from settings_local import *
except ImportError:
	raise Exception("Please create settings_local.py")
