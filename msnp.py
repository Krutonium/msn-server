import io
from urllib.parse import quote, unquote
from lxml.objectify import fromstring as parse_xml

from settings import DEBUG

class MSNPWriter:
	def __init__(self, logger, transport):
		self.logger = logger
		self.transport = transport
		self._buf = io.BytesIO()
		self._group_depth = 0
	
	def __enter__(self):
		self._group_depth += 1
		return self
	
	def __exit__(self, *exc):
		self._group_depth -= 1
		self._flush()
	
	def write(self, *m):
		data = None
		m = list(m)
		if m[0] == 'UBX':
			m[-1] = '<Data><PSM>{}</PSM><CurrentMedia>{}</CurrentMedia></Data>'.format(
				m[-1]['PSM'] or '', m[-1]['CurrentMedia'] or ''
			).encode('utf-8')
		elif m[0] in ('ILN', 'NLN', 'CHG'):
			m[-1] = urlescape_msnobj(m[-1])
		if isinstance(m[-1], bytes):
			data = m[-1]
			m = list(m)
			m[-1] = len(data)
		m = tuple(str(x).replace(' ', '%20') for x in m if x is not None)
		self.logger.info('<<<', *m)
		w = self._buf.write
		w(' '.join(m).encode('utf-8'))
		w(b'\r\n')
		if data is not None:
			w(data)
		self._flush()
	
	def _flush(self):
		if self._group_depth > 0: return
		data = self._buf.getvalue()
		if data:
			self.transport.write(data)
			self._buf = io.BytesIO()

class MSNPReader:
	def __init__(self, logger, data):
		self.logger = logger
		self._data = data
		self._i = 0
	
	def __iter__(self):
		return self
	
	def __next__(self):
		if self._i >= len(self._data):
			raise StopIteration
		return self._read_msnp()
	
	def _read_msnp(self):
		i = self._i
		d = self._data
		e = d.index(b'\n', i)
		assert e >= 0
		self._i = e + 1
		m = d[i:e].decode('utf-8').strip()
		assert len(m) > 1
		m = m.split()
		self.logger.info('>>>', *m)
		m = [unquote(x) for x in m]
		if m[0] in ('UUX', 'MSG'):
			m[-1] = self._read_raw(int(m[-1]))
			if m[0] == 'UUX':
				m[-1] = parse_uux(m[-1])
		return m
	
	def _read_raw(self, n):
		i = self._i
		e = i + n
		assert e <= len(self._data)
		self._i += n
		return self._data[i:e]

def parse_uux(data):
	elm = parse_xml(data.decode('utf-8'))
	return {
		'PSM': str(elm.find('PSM')),
		'CurrentMedia': str(elm.find('CurrentMedia')),
	}

def urlescape_msnobj(msnobj):
	if msnobj is None: return None
	return quote(msnobj, safe = '')

class Logger:
	def __init__(self, prefix):
		self.prefix = prefix
		self.transport = None
	
	def info(self, *args):
		if DEBUG:
			print(self._name(), *args)
	
	def log_connect(self, transport):
		self.transport = transport
		self.info("con")
	
	def log_disconnect(self):
		self.info("dis")
		self.transport = None
	
	def _name(self):
		name = ''
		if self.transport:
			(_, port) = self.transport.get_extra_info('peername')
			name += '{}'.format(port)
		name += ' ' + self.prefix
		return name

def decode_email(email_pw):
	# MSNP < 8 uses MD5 auth, whose server-side can't
	# be implemented without the plaintext pw.
	# As a workaround, this server requires such clients
	# (or rather, users thereof) to pass the pw inside the email,
	# like so: email|password@example.com
	try: j = email_pw.rindex('@')
	except ValueError: return (email_pw, None)
	try: i = email_pw.index('|')
	except ValueError: return (email_pw, None)
	if i >= j: return (email_pw, None)
	email = email_pw[:i] + email_pw[j:]
	pw = email_pw[i+1:j]
	return (email, pw)
