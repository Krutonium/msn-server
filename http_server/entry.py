def register(loop, backend, *, http_port = None, devmode = False, msn_http = False, yahoo_http = False):
	from util.misc import AIOHTTPRunner
	from .http_server import create_app
	
	assert http_port, "Please specify `http_port`."
	
	if devmode:
		http_host = '0.0.0.0'
	else:
		http_host = '127.0.0.1'
	
	backend.add_runner(AIOHTTPRunner(http_host, http_port, create_app(loop, backend, msn_http, yahoo_http)))
	if devmode:
		from dev import autossl
		ssl_context = autossl.create_context()
		backend.add_runner(AIOHTTPRunner(http_host, 443, create_app(loop, backend, msn_http, yahoo_http), ssl = ssl_context))