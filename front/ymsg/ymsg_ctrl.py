import io
from abc import ABCMeta, abstractmethod
import asyncio
from typing import Dict, List, Tuple, Any, Optional, Callable
from multidict import MultiDict
import binascii
import struct
import time

from util.misc import Logger

class YMSGCtrlBase(metaclass = ABCMeta):
	__slots__ = ('logger', 'decoder', 'encoder', 'peername', 'closed', 'close_callback', 'transport')
	
	logger: Logger
	decoder: 'YMSGDecoder'
	encoder: 'YMSGEncoder'
	peername: Tuple[str, int]
	close_callback: Optional[Callable[[], None]]
	closed: bool
	transport: Optional[asyncio.WriteTransport]
	
	def __init__(self, logger: Logger) -> None:
		self.logger = logger
		self.decoder = YMSGDecoder()
		self.encoder = YMSGEncoder(logger)
		self.peername = ('0.0.0.0', 5050)
		self.closed = False
		self.close_callback = None
	
	def data_received(self, transport: asyncio.BaseTransport, data: bytes) -> None:
		self.peername = transport.get_extra_info('peername')
		
		self.logger.info('>>>', data)
		
		if find_count_PRE(data) > 1:
			pkt_list = sep_cluster(data, find_count_PRE(data) - 1)
			for pack in pkt_list:
				self.receive_event(pack)
		else:
			self.receive_event(data)
	
	def receive_event(self, pkt):
		for y in self.decoder.data_received(pkt):
			
			# Escargot's MSN and Yahoo functions have similar name structures
			# MSN: "_m_CMD"
			# Yahoo: "_y_[hex version of service; a bit nicer than using the service number]"
			
			try:
				# check version and vendorId
				if y[1] > 16 or y[2] not in (0, 100):
					return
				f = getattr(self, '_y_{}'.format(binascii.hexlify(struct.pack('!H', y[0])).decode()))
				f(*y[1:])
			except Exception as ex:
				self.logger.error(ex)
	
	def send_reply(self, *y) -> None:
		self.encoder.encode(y)
		transport = self.transport
		if transport is not None:
			transport.write(self.flush())
	
	def send_reply_multiple(self, *y_lists) -> None:
		for y_list in y_lists:
			self.encoder.encode(y_list[0:])
		transport = self.transport
		if transport is not None:
			transport.write(self.flush())
	
	def flush(self) -> bytes:
		return self.encoder.flush()
	
	def close(self, duplicate = False) -> None:
		if self.closed: return
		self.closed = True
		
		if self.close_callback:
			self.close_callback()
		if not duplicate:
			self._on_close()
	
	@abstractmethod
	def _on_close(self) -> None: pass

class YMSGEncoder:
	def __init__(self, logger) -> None:
		self._logger = logger
		self._buf = io.BytesIO()
	
	def encode(self, y):
		y = list(y)
		# y = List[service, status, session_id, kvs]
		
		w = self._buf.write
		w(PRE)
		# version number and vendor id are replaced with 0x00000000
		w(b'\x00\x00\x00\x00')
		
		payload_list = []
		kvs = y[3]
		
		if kvs:
			for k, v in kvs.items():
				payload_list.extend([str(k).encode('utf-8'), SEP, str(v).encode('utf-8'), SEP])
		payload = b''.join(payload_list)
		w(struct.pack('!HHII', len(payload), y[0], y[1], y[2]))
		w(payload)
	
	def flush(self):
		data = self._buf.getvalue()
		self._logger.info('<<<', data)
		if data:
			self._buf = io.BytesIO()
		return data

class YMSGDecoder:
	def __init__(self):
		self._data = b''
	
	def data_received(self, data):
		self._data = data
		
		y = self._ymsg_read()
		if y is None: return
		yield y
	
	def _ymsg_read(self):
		try:
			(version, vendor_id, service, status, session_id, kvs) = _decode_ymsg(self._data)
		except AssertionError:
			return None
		except Exception:
				print("ERR _ymsg_read", self._data)
				raise
		
		y = [service, version, vendor_id, status, session_id, kvs]
		return y

def _decode_ymsg(data) -> Tuple[int, int, int, int, int, Dict[str, Optional[str]]]:
	assert data[:4] == PRE
	assert len(data) >= 20
	header = data[4:20]
	payload = data[20:]
	if header[0] == b'\x00':
		struct_fmt = '!xB'
	else:
		struct_fmt = '!Bx'
	
	struct_fmt += 'HHHII'
	(version, vendor_id, pkt_len, service, status, session_id) = struct.unpack(struct_fmt, header)
	assert len(payload) == pkt_len
	parts = payload.split(SEP)
	kvs = MultiDict()
	for i in range(1, len(parts), 2):
		kvs.add(str(parts[i-1].decode()), parts[i].decode('utf-8'))
	return version, vendor_id, service, status, session_id, kvs

def sep_cluster(data, length):
	pos = 0
	cluster_pack = []
	
	for i in range(0, length):
		length_post_PRE = 20 + struct.unpack('!H', data[(pos + 8):(pos + 10)])[0]
		cluster_pack.append(data[pos:length_post_PRE])
		pos = length_post_PRE
	
	return cluster_pack

PRE = b'YMSG'
SEP = b'\xC0\x80'

def find_count_PRE(source):
	how_many = 0
	pos = 0
	
	while True:
		pos = source.find(PRE, pos)
		if pos == -1:
			break
		else:
			how_many += 1
			length = struct.unpack('!H', source[(pos + 8):(pos + 10)])[0]
			pos += (20 + length)
	
	if how_many == 0:
		return -1
	else:
		return how_many