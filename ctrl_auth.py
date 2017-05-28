from urllib.parse import unquote
from aiohttp import web

import settings

LOGIN_PATH = '/login'

def create_app():
	app = web.Application()
	app.router.add_get('/nexus-mock', handle_nexus)
	app.router.add_get('/nexusdevel', handle_nexus)
	app.router.add_get('/rdr/pprdr.asp', handle_nexus)
	app.router.add_post('/RST.srf', handle_rst)
	app.router.add_post('/NotRST.srf', handle_not_rst)
	app.router.add_get(LOGIN_PATH, handle_login)
	app.router.add_route('*', '/{path:.*}', handle_other)
	return app

async def handle_nexus(req):
	if req.host == settings.DEV_NEXUS or req.path == '/nexusdevel':
		return web.Response(status = 200, headers = {
			'PassportURLs': 'DALogin=https://{}{}'.format(settings.DEV_NEXUS, LOGIN_PATH),
		})
	return web.Response(status = 200, headers = {
		'PassportURLs': 'DALogin=https://{}{}'.format(settings.LOGIN_HOST, LOGIN_PATH),
	})

async def handle_login(req):
	if req.host == settings.DEV_NEXUS:
		token = 'fake-dev-login-token'
	else:
		email, pwd = _extract_pp_credentials(req.headers.get('Authorization'))
		token = _login(email, pwd)
	if token is None:
		return web.Response(status = 401, headers = {
			'WWW-Authenticate': '{}da-status=failed'.format(PP),
		})
	return web.Response(status = 200, headers = {
		'Authentication-Info': '{}da-status=success,from-PP=\'{}\''.format(PP, token),
	})

async def handle_not_rst(req):
	email = req.headers.get('X-User')
	pwd = req.headers.get('X-Password')
	token = _login_impl(email, pwd)
	headers = {}
	if token is not None:
		headers['X-Token'] = token
	return Response(status = 200, headers = headers)

async def handle_rst(req):
	if req.host == settings.DEV_NEXUS:
		token = 'fake-dev-login-token'
	else:
		d = await req.read()
		email, pwd = _extract_rst_credentials(d.decode('utf-8'))
		token = _login(email, pwd)
	if token is None:
		resp = RESPONSE_RST_FAIL
	else:
		resp = RESPONSE_RST_SUCCESS.replace('$$TOKEN$$', token)
	return web.Response(status = 200, body = resp.encode('utf-8'))

def _extract_rst_credentials(d):
	import re
	import html
	m = re.search(r'<wsse:Username>([^<]+)</wsse:Username><wsse:Password>([^<]+)</wsse:Password>', d)
	if not m:
		return None, None
	(email, pwd) = m.group(1, 2)
	return html.unescape(email), html.unescape(pwd)

RESPONSE_RST_SUCCESS = '''<?xml version="1.0" encoding="utf-8" ?><S:Envelope xmlns:S="http://schemas.xmlsoap.org/soap/envelope/"><S:Header><psf:pp xmlns:psf="http://schemas.microsoft.com/Passport/SoapServices/SOAPFault"><psf:serverVersion>1</psf:serverVersion><psf:PUID>0006BFFD98E04B20</psf:PUID><psf:configVersion>16.000.26889.00</psf:configVersion><psf:uiVersion>3.100.2179.0</psf:uiVersion><psf:mobileConfigVersion>16.000.26208.0</psf:mobileConfigVersion><psf:authstate>0x48803</psf:authstate><psf:reqstatus>0x0</psf:reqstatus><psf:serverInfo Path="Live1" RollingUpgradeState="ExclusiveNew" LocVersion="0" ServerTime="2017-05-26T03:04:57Z">BL2IDSLGN3C029 2017.05.03.14.15.02</psf:serverInfo><psf:cookies/><psf:browserCookies><psf:browserCookie Name="MH" URL="http://www.msn.com">MSFT; path=/; domain=.msn.com; expires=Wed, 30-Dec-2037 16:00:00 GMT</psf:browserCookie><psf:browserCookie Name="MHW" URL="http://www.msn.com">; path=/; domain=.msn.com; expires=Thu, 30-Oct-1980 16:00:00 GMT</psf:browserCookie><psf:browserCookie Name="MH" URL="http://www.live.com">MSFT; path=/; domain=.live.com; expires=Wed, 30-Dec-2037 16:00:00 GMT</psf:browserCookie><psf:browserCookie Name="MHW" URL="http://www.live.com">; path=/; domain=.live.com; expires=Thu, 30-Oct-1980 16:00:00 GMT</psf:browserCookie></psf:browserCookies><psf:credProperties><psf:credProperty Name="MainBrandID">MSFT</psf:credProperty><psf:credProperty Name="BrandIDList"></psf:credProperty><psf:credProperty Name="IsWinLiveUser">true</psf:credProperty><psf:credProperty Name="CID">e0ac0c0873f21eff</psf:credProperty><psf:credProperty Name="AuthMembername">muteinvert@hotmail.com</psf:credProperty><psf:credProperty Name="Country">CA</psf:credProperty><psf:credProperty Name="Language">2057</psf:credProperty><psf:credProperty Name="FirstName">M&#x00FC;te</psf:credProperty><psf:credProperty Name="LastName">Invert</psf:credProperty><psf:credProperty Name="ChildFlags">00000001</psf:credProperty><psf:credProperty Name="Flags">40100443</psf:credProperty><psf:credProperty Name="FlagsV2">00000000</psf:credProperty><psf:credProperty Name="IP">199.126.55.228</psf:credProperty><psf:credProperty Name="FamilyID">0023000080844BE7</psf:credProperty><psf:credProperty Name="AssociatedForStrongAuth">0</psf:credProperty></psf:credProperties><psf:extProperties><psf:extProperty Name="ANON" Expiry="Tue, 12-Dec-2017 11:04:56 GMT" Domains="bing.com;atdmt.com" IgnoreRememberMe="false">A=DB4ECAD118F991710D6FF9D7FFFFFFFF&amp;E=13ac&amp;W=1</psf:extProperty><psf:extProperty Name="NAP" Expiry="Sun, 03-Sep-2017 10:04:56 GMT" Domains="bing.com;atdmt.com" IgnoreRememberMe="false">V=1.9&amp;E=1352&amp;C=gni7TBoej6UxbPccLW6irlZOvgIX6bWiee2vD6KfsWxM81eFgnN1Xg&amp;W=1</psf:extProperty><psf:extProperty Name="LastUsedCredType">1</psf:extProperty><psf:extProperty Name="WebCredType">1</psf:extProperty><psf:extProperty Name="CID">e0ac0c0873f21eff</psf:extProperty></psf:extProperties><psf:response/></psf:pp></S:Header><S:Body><wst:RequestSecurityTokenResponseCollection xmlns:S="http://schemas.xmlsoap.org/soap/envelope/" xmlns:wst="http://schemas.xmlsoap.org/ws/2004/04/trust" xmlns:wsse="http://schemas.xmlsoap.org/ws/2003/06/secext" xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd" xmlns:saml="urn:oasis:names:tc:SAML:1.0:assertion" xmlns:wsp="http://schemas.xmlsoap.org/ws/2002/12/policy" xmlns:psf="http://schemas.microsoft.com/Passport/SoapServices/SOAPFault"><wst:RequestSecurityTokenResponse><wst:TokenType>urn:passport:legacy</wst:TokenType><wsp:AppliesTo xmlns:wsa="http://schemas.xmlsoap.org/ws/2004/03/addressing"><wsa:EndpointReference><wsa:Address>http://Passport.NET/tb</wsa:Address></wsa:EndpointReference></wsp:AppliesTo><wst:LifeTime><wsu:Created>2017-05-26T03:04:57Z</wsu:Created><wsu:Expires>2017-05-27T03:04:56Z</wsu:Expires></wst:LifeTime><wst:RequestedSecurityToken><EncryptedData xmlns="http://www.w3.org/2001/04/xmlenc#" Id="BinaryDAToken0" Type="http://www.w3.org/2001/04/xmlenc#Element"><EncryptionMethod Algorithm="http://www.w3.org/2001/04/xmlenc#tripledes-cbc"></EncryptionMethod><ds:KeyInfo xmlns:ds="http://www.w3.org/2000/09/xmldsig#"><ds:KeyName>http://Passport.NET/STS</ds:KeyName></ds:KeyInfo><CipherData><CipherValue></CipherValue></CipherData></EncryptedData></wst:RequestedSecurityToken><wst:RequestedTokenReference><wsse:KeyIdentifier ValueType="urn:passport"></wsse:KeyIdentifier><wsse:Reference URI="#BinaryDAToken0"></wsse:Reference></wst:RequestedTokenReference><wst:RequestedProofToken><wst:BinarySecret>/5I1jNFuwXJDfez9nyy6wGsTPlEm13z/</wst:BinarySecret></wst:RequestedProofToken></wst:RequestSecurityTokenResponse><wst:RequestSecurityTokenResponse><wst:TokenType>urn:passport:legacy</wst:TokenType><wsp:AppliesTo xmlns:wsa="http://schemas.xmlsoap.org/ws/2004/03/addressing"><wsa:EndpointReference><wsa:Address>messenger.msn.com</wsa:Address></wsa:EndpointReference></wsp:AppliesTo><wst:LifeTime><wsu:Created>2017-05-26T03:04:57Z</wsu:Created><wsu:Expires>2017-05-26T03:13:16Z</wsu:Expires></wst:LifeTime><wst:RequestedSecurityToken><wsse:BinarySecurityToken Id="PPToken1">$$TOKEN$$</wsse:BinarySecurityToken></wst:RequestedSecurityToken><wst:RequestedTokenReference><wsse:KeyIdentifier ValueType="urn:passport"></wsse:KeyIdentifier><wsse:Reference URI="#PPToken1"></wsse:Reference></wst:RequestedTokenReference></wst:RequestSecurityTokenResponse></wst:RequestSecurityTokenResponseCollection></S:Body></S:Envelope>'''
RESPONSE_RST_FAIL = '''<?xml version="1.0" encoding="utf-8" ?><S:Envelope xmlns:S="http://schemas.xmlsoap.org/soap/envelope/" xmlns:wsse="http://schemas.xmlsoap.org/ws/2003/06/secext" xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd" xmlns:psf="http://schemas.microsoft.com/Passport/SoapServices/SOAPFault"><S:Header><psf:pp xmlns:psf="http://schemas.microsoft.com/Passport/SoapServices/SOAPFault"><psf:serverVersion>1</psf:serverVersion><psf:authstate>0x80048800</psf:authstate><psf:reqstatus>0x80048823</psf:reqstatus><psf:serverInfo Path="Live1" RollingUpgradeState="ExclusiveNew" LocVersion="0" ServerTime="2017-05-26T03:02:40Z">BL2IDSLGN3C039 2017.05.03.14.15.02</psf:serverInfo><psf:cookies/><psf:response/></psf:pp></S:Header><S:Fault><faultcode>wsse:FailedAuthentication</faultcode><faultstring>Authentication Failure</faultstring></S:Fault></S:Envelope>'''

def _extract_pp_credentials(auth_str):
	if auth_str is None:
		return None, None
	assert auth_str.startswith(PP)
	auth = {}
	for part in auth_str[len(PP):].split(','):
		parts = part.split('=', 1)
		if len(parts) == 2:
			auth[unquote(parts[0])] = unquote(parts[1])
	email = auth['sign-in']
	pwd = auth['pwd']
	return email, pwd

def _login(email, pwd):
	from db import Session, User, Auth
	from util.hash import hasher
	with Session() as sess:
		user = sess.query(User).filter(User.email == email).one_or_none()
		if user is None: return None
		if not hasher.verify(pwd, user.password): return None
		return Auth.CreateToken(user.email)

async def handle_other(req):
	print("UNKNOWN REQUEST:", req.method, req.host + req.path_qs)
	#print(req.headers)
	#body = await req.read()
	#if body:
	#	print("body {")
	#	print(body)
	#	print("}")
	#else:
	#	print("body {}")
	return web.Response(status = 404)

PP = 'Passport1.4 '
