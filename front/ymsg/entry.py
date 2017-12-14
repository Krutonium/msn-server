import asyncio
import struct

from core.session import PersistentSession
from util.misc import Logger

def register(loop, backend):
	from util.misc import ProtocolRunner
	backend.add_runner(ProtocolRunner('0.0.0.0', 5050, ListenerYMSG, args = ['YH', backend]))

class ListenerYMSG(asyncio.Protocol):
	def __init__(self, logger_prefix, backend):
		super().__init__()
		self.logger_prefix = logger_prefix
		self.backend = backend
		self.transport = None
		self.logger = None
	
	def connection_made(self, transport):
		self.transport = transport
		self.logger = Logger(self.logger_prefix, transport)
		self.logger.log_connect()
	
	def connection_lost(self, exc):
		self.logger.log_disconnect()
		self.logger = None
		self.transport = None
	
	def data_received(self, data):
		self.logger.info('>>>', data)
		
		(version, vendor_id, service, status, session_id, kvs) = _decode_ymsg(data)
		
		if service == YMSGService.Verify:
			msg = _encode_ymsg(version, vendor_id, service, status, session_id)
			self.logger.info('<<<', msg)
			self.transport.write(msg)
			return
		if service == YMSGService.Auth:
			session_id = 1239999999
			email = kvs[1]
			msg = _encode_ymsg(version, vendor_id, YMSGService.Auth, status, session_id, {
				1: email,
				#94: 'g|i/p^h&z-d+2%v%x&j|e+(m^k-i%h*(s+8%a/u/x*(b-4*i%h^g^j|m^n-r*f+p+j)))',
				#94: 'g|i/p^h&z-d+2%v%x&j|e+m^k-i%h*s+8%a/u/x*b-4*i%h^g^j|m^n-r*f+p+',
				94: '',
				13: 0, # auth version, 0/1
			})
			self.logger.info('<<<', msg)
			self.transport.write(msg)
			return
		if service == YMSGService.AuthResp:
			print(kvs)
			print("session_id", session_id)
		
		self.logger.info("unknown", service)
		self.transport.write(_encode_ymsg(version, vendor_id, YMSGService.LogOff, 0, 0))
		self.transport.close()

def _decode_ymsg(data):
	assert data[:4] == PRE
	assert len(data) >= 20
	header = data[4:20]
	payload = data[20:]
	(version, vendor_id, pkt_len, service, status, session_id) = struct.unpack('!BxHHHII', header)
	assert len(payload) == pkt_len
	parts = payload.split(SEP)
	kvs = {}
	for i in range(1, len(parts), 2):
		kvs[int(parts[i-1])] = parts[i].decode('utf-8')
	return (version, vendor_id, service, status, session_id, kvs)

def _encode_ymsg(version, vendor_id, service, status, session_id, kvs = None):
	payload = []
	if kvs:
		for k, v in kvs.items():
			payload.extend([str(k).encode('utf-8'), SEP, str(v).encode('utf-8'), SEP])
	payload = b''.join(payload)
	data = PRE
	data += struct.pack('!BxHHHII', version, vendor_id, len(payload), service, status, session_id)
	data += payload
	return data

PRE = b'YMSG'
SEP = b'\xC0\x80'

class YMSGService:
	LogOn = 0x01
	LogOff = 0x02
	Verify = 0x4c
	AuthResp = 0x54
	Auth = 0x57
