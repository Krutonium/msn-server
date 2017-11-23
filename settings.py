DB = 'sqlite:///msn.sqlite'
DEBUG = True

try:
	from settings_local import *
except ImportError as ex:
	raise Exception("Please create settings_local.py") from ex
