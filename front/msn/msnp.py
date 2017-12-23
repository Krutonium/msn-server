import io
from typing import List, Tuple, Any, Optional
from urllib.parse import unquote

from util.misc import Logger
from .misc import build_msnp_presence_notif

class MSNPCtrl:
	__slots__ = ('logger', 'reader', 'writer')
	
	logger: Logger
	reader: 'MSNPReader'
	writer: 'MSNPWriter'
	
	def __init__(self, logger: Logger) -> None:
		self.logger = logger
		self.reader = MSNPReader(logger)
		self.writer = MSNPWriter(logger)
	
	def data_received(self, data: bytes) -> None:
		for m in self.reader.data_received(data):
			try:
				f = getattr(self, '_m_{}'.format(m[0].lower()))
				f(*m[1:])
			except Exception as ex:
				self.logger.error(ex)
	
	def send_reply(self, *m):
		self.writer.write(m)
	
	def flush(self):
		return self.writer.flush()

class MSNPWriter:
	def __init__(self, logger) -> None:
		self._logger = logger
		self._buf = io.BytesIO()
	
	def write(self, m):
		m = list(m)
		data = None
		if isinstance(m[-1], bytes):
			data = m[-1]
			m[-1] = len(data)
		mt = tuple(str(x).replace(' ', '%20') for x in m if x is not None)
		_truncated_log(self._logger, '<<<', mt)
		w = self._buf.write
		w(' '.join(mt).encode('utf-8'))
		w(b'\r\n')
		if data is not None:
			w(data)
	
	def flush(self):
		data = self._buf.getvalue()
		if data:
			self._buf = io.BytesIO()
		return data

class MSNPReader:
	def __init__(self, logger):
		self.logger = logger
		self._data = b''
		self._i = 0
	
	def data_received(self, data):
		if self._data:
			self._data += data
		else:
			self._data = data
		while self._data:
			m = self._read_msnp()
			if m is None: break
			yield m
	
	def _read_msnp(self):
		try:
			m, body, e = _msnp_try_decode(self._data, self._i)
		except AssertionError:
			return None
		except Exception:
			print("ERR _read_msnp", self._i, self._data)
			raise
		
		self._data = self._data[e:]
		self._i = 0
		_truncated_log(self.logger, '>>>', m)
		m = [unquote(x) for x in m]
		if body:
			m.append(body)
		return m
	
	def _read_raw(self, n):
		i = self._i
		e = i + n
		assert e <= len(self._data)
		self._i += n
		return self._data[i:e]

def _msnp_try_decode(d, i) -> Tuple[List[Any], Optional[bytes], int]:
	# Try to parse an MSNP message from buffer `d` starting at index `i`
	# Returns (parsed message, end index)
	e = d.find(b'\n', i)
	assert e >= 0
	e += 1
	m = d[i:e].decode('utf-8').strip()
	assert len(m) > 1
	m = m.split()
	body = None
	if m[0] in _PAYLOAD_COMMANDS:
		n = int(m.pop())
		assert e+n <= len(d)
		body = d[e:e+n]
		e += n
	return m, body, e

_PAYLOAD_COMMANDS = {
	'UUX', 'MSG', 'ADL', 'FQY', 'RML', 'UUN'
}

def _truncated_log(logger, pre, m):
	if m[0] in ('UUX', 'MSG', 'ADL'):
		logger.info(pre, *m[:-1], len(m[-1]))
	elif m[0] in ('CHG', 'ILN', 'NLN') and 'msnobj' in m[-1]:
		logger.info(pre, *m[:-1], '<truncated>')
	else:
		logger.info(pre, *m)
