import asyncio
import struct

from util.misc import Logger
from .yahoo_lib.Y64 import Y64Encode
from .challenge import generate_challenge_v1, verify_challenge_v1

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
		self.challenge = None
    
	def connection_made(self, transport):
		self.transport = transport
		self.logger = Logger(self.logger_prefix, transport)
		self.logger.log_connect()
	
	def connection_lost(self, exc):
		self.logger.log_disconnect()
		self.logger = None
		self.transport = None
		self.challenge = None
	
	def data_received(self, data):
		self.logger.info('>>>', data)
		
		(version, vendor_id, service, status, session_id, kvs) = _decode_ymsg(data)
		
		if service == YMSGService.Verify:
			msg = _encode_ymsg(service, status, session_id)
			self.logger.info('<<<', msg)
			self.transport.write(msg)
			return
		if service == YMSGService.Auth:
			session_id = 1239999999
			email = kvs[1]
			auth_dict = {1: email}
			if version in (9, 10):
			    self.challenge = generate_challenge_v1()
			    auth_dict[94] = self.challenge
			elif version in (11,):
				# Implement V2 challenge string generation later
				auth_dict[94] = ''
				auth_dict[13] = 1
			msg = _encode_ymsg(YMSGService.Auth, status, session_id, auth_dict)
			self.logger.info('<<<', msg)
			self.transport.write(msg)
			return
		if service == YMSGService.AuthResp:
		    session_id = 1239999999
		    email = kvs[0]
		    if kvs[1] != email and kvs[2] != "1":
		        print('auth_resp failed')
		        self.transport.write(_encode_ymsg(YMSGService.LogOff, 0, 0))
		        self.transport.close()
		    resp_6 = kvs[6]
		    resp_96 = kvs[96]
		    if version in (9, 10):
		        is_resp_correct = verify_challenge_v1(email, self.challenge)
		        if is_resp_correct:
		            # Implement friends/cookies packet later
            
			print(kvs)
			print("session_id", session_id)
		
		self.logger.info("unknown", service)
		self.transport.write(_encode_ymsg(YMSGService.LogOff, 0, 0))
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

def _encode_ymsg(service, status, session_id, kvs = None):
	payload_list = []
	if kvs:
		for k, v in kvs.items():
			payload_list.extend([str(k).encode('utf-8'), SEP, str(v).encode('utf-8'), SEP])
	payload = b''.join(payload_list)
	data = PRE
	# version number and vendor id are replaced with 0x00000000
	data += b'\x00\x00\x00\x00'
	data += struct.pack('!HHII', len(payload), service, status, session_id)
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
