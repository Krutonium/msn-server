DB = 'sqlite:///escargot.sqlite'
STATS_DB = 'sqlite:///stats.sqlite'
LOGIN_HOST = 'm1.escargot.log1p.xyz'
YAHOO_FT_DL_HOST = 'http://yflstre.log1p.xyz'
STORAGE_HOST = LOGIN_HOST
# While not necessary for debugging, it is recommended you change the `SYSBOARD_PASS` variable for security reasons.
SYSBOARD_PASS = 'root'

DEBUG = False
DEBUG_MSNP = False
DEBUG_HTTP_REQUEST = False
DEBUG_HTTP_REQUEST_FULL = False
DEBUG_SYSBOARD = True

ENABLE_FRONT_MSN = True
ENABLE_FRONT_YMSG = False
ENABLE_FRONT_IRC = False
ENABLE_FRONT_BOT = False
ENABLE_FRONT_DEVBOTS = False

try:
	from settings_local import *
except ImportError as ex:
	raise Exception("Please create settings_local.py") from ex
