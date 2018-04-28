import io
from abc import ABCMeta, abstractmethod
import asyncio
from typing import Dict, List, Tuple, Any, Optional, Callable, Iterable
from multidict import MultiDict
import binascii
import struct
import time

from util.misc import Logger

from .misc import YMSGStatus, YMSGService

KVS = Dict[str, str]

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
		#self.logger.info('>>>', data)
		
		n = find_count_PRE(data)
		if n > 1:
			for pack in sep_cluster(data, n):
				self.receive_event(pack)
		else:
			self.receive_event(data)
	
	def receive_event(self, pkt: bytes) -> None:
		for y in self.decoder.data_received(pkt):
			self.logger.info('>>>', y[0], y[3], y[4], y[5])
			
			try:
				# check version and vendorId
				if y[1] > 16 or y[2] not in (0, 100):
					return
				f = getattr(self, '_y_{}'.format(binascii.hexlify(struct.pack('!H', y[0])).decode()))
				f(*y[1:])
			except Exception as ex:
				self.logger.error(ex)
	
	def send_reply(self, service: YMSGService, status: YMSGStatus, session_id: int, kvs: Optional[KVS] = None) -> None:
		self.encoder.encode(service, status, session_id, kvs)
		transport = self.transport
		if transport is not None:
			transport.write(self.flush())
	
	def flush(self) -> bytes:
		return self.encoder.flush()
	
	def close(self) -> None:
		if self.closed: return
		self.closed = True
		
		if self.close_callback:
			self.close_callback()
		self._on_close()
	
	@abstractmethod
	def _on_close(self) -> None: pass

class YMSGEncoder:
	__slots__ = ('_logger', '_buf')
	
	_logger: Logger
	_buf: io.BytesIO
	
	def __init__(self, logger: Logger) -> None:
		self._logger = logger
		self._buf = io.BytesIO()
	
	def encode(self, service: YMSGService, status: YMSGStatus, session_id: int, kvs: Optional[KVS] = None) -> None:
		w = self._buf.write
		w(PRE)
		# version number and vendor id are replaced with 0x00000000
		w(b'\x00\x00\x00\x00')
		
		self._logger.info('<<<', service, status, session_id, kvs)
		
		payload_list = []
		if kvs is not None:
			for k, v in kvs.items():
				payload_list.extend([str(k).encode('utf-8'), SEP, str(v).encode('utf-8'), SEP])
		payload = b''.join(payload_list)
		# Have to call `int` on these because they might be an IntEnum, which
		# get `repr`'d to `EnumName.ValueName`. Grr.
		w(struct.pack('!HHII', len(payload), int(service), int(status), session_id))
		w(payload)
	
	def flush(self) -> bytes:
		data = self._buf.getvalue()
		if data:
			#self._logger.info('<<<', data)
			self._buf = io.BytesIO()
		return data

DecodedYMSG = Tuple[YMSGService, int, int, YMSGStatus, int, KVS]

class YMSGDecoder:
	__slots__ = ('_data')
	
	_data: bytes
	
	def __init__(self) -> None:
		self._data = b''
	
	def data_received(self, data: bytes) -> Iterable[DecodedYMSG]:
		# TODO: Shouldn't this +=?
		self._data = data
		
		y = self._ymsg_read()
		if y is None: return
		yield y
	
	def _ymsg_read(self) -> Optional[DecodedYMSG]:
		try:
			y = _decode_ymsg(self._data)
		except AssertionError:
			return None
		except Exception:
			print("ERR _ymsg_read", self._data)
			raise
		return y

def _decode_ymsg(data: bytes) -> DecodedYMSG:
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
	return YMSGService(service), version, vendor_id, YMSGStatus(status), session_id, kvs

def sep_cluster(data: bytes, length: int) -> List[bytes]:
	pos = 0
	cluster_pack = []
	
	for i in range(0, length):
		length_post_PRE = pos + (20 + struct.unpack('!H', data[(pos + 8):(pos + 10)])[0])
		cluster_pack.append(data[pos:length_post_PRE])
		pos = length_post_PRE
	
	return cluster_pack

PRE = b'YMSG'
SEP = b'\xC0\x80'

def find_count_PRE(source: bytes) -> int:
	how_many = 0
	pos = 0
	
	while True:
		pos = source.find(PRE, pos)
		if pos == -1:
			break
		how_many += 1
		length = struct.unpack('!H', source[(pos + 8):(pos + 10)])[0]
		pos += (20 + length)
	
	return how_many or -1
