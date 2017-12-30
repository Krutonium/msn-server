from typing import Optional, Callable

import asyncio
import struct

from core.backend import Backend
from util.misc import Logger
from .yahoo_lib.Y64 import Y64Encode
from .challenge import generate_challenge_v1, verify_challenge_v1

from .ymsg import YMSGCtrlBase

def register(loop, backend):
	from util.misc import ProtocolRunner
	# Use YMSGCtrlBase as a filler for now
	backend.add_runner(ProtocolRunner('0.0.0.0', 5050, ListenerYMSG, args = ['YH', backend, YMSGCtrlBase]))

class ListenerYMSG(asyncio.Protocol):
    logger: Logger
    backend: Backend
    controller: YMSGCtrlBase
    transport: Optional[asyncio.WriteTransport]
    
	def __init__(self, logger_prefix: str, backend: Backend, controller_factory: Callable[[Logger, str, Backend], YMSGCtrlBase]) -> None:
		super().__init__()
		self.logger = Logger(logger_prefix, self)
		self.backend = backend
		self.controller = controller_factory(self.logger, 'direct', backend)
		self.controller.close_callback = self._on_close
		self.transport = None
    
	def connection_made(self, transport: asyncio.BaseTrasport) -> None:
	    assert isinstance(transport, asyncio.WriteTransport)
		self.transport = transport
		self.logger.log_connect()
	
	def connection_lost(self, exc: Exception) -> None:
	    self.controller.close()
		self.logger.log_disconnect()
		self.transport = None
	
	def data_received(self, data) -> None:
	    transport = self.transport
	    assert transport is not None
	    self.controller.transport = None
	    self.controller.data_received(transport, data)
	    transport.write(self.controller.flush())
	    self.controller.transport = transport
	    
	    # TODO: Move this chunk of code to it's own library
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
				is_resp_correct = verify_challenge_v1(email, self.challenge, resp_6, resp_96)
				if is_resp_correct:
					# Implement friends/cookies packet later
					pass
			
			print(kvs)
			print("session_id", session_id)
		
		self.logger.info("unknown", service)
		self.transport.write(_encode_ymsg(YMSGService.LogOff, 0, 0))
		self.transport.close()

# TODO: Remove decode, encode, and PRE/SEP constants after 'ymsg.py' is fully implemented.

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
