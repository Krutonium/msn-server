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
		raw_data = data
		
		assert data[:4] == b'YMSG'
		assert len(data) >= 20
		header = data[4:20]
		data = data[20:]
		(version, pkt_len, service, status, session_id) = struct.unpack('!B3xHHII', header)
		
		assert len(data) == pkt_len
		
		if service == YMSGService.Verify:
			msg = b'YMSG' + header
			self.logger.info('<<<', msg)
			self.transport.write(msg)
			return
		
		if service == YMSGService.Auth:
			email = data[3:-2]
			self.logger.info("email", email)
		else:
			self.logger.info("unknown", service)
		
		self.transport.write(b'YMSG')
		self.transport.write(struct.pack('!B3xHHII', version, 0, YMSGService.LogOff, 0, 0))
		self.transport.close()

class YMSGService:
	LogOn = 0x01
	LogOff = 0x02
	Verify = 0x4c
	AuthResp = 0x54
	Auth = 0x57
