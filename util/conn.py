import asyncio

from util.msnp import MSNPReader, MSNPWriter

class MSNPConn(asyncio.Protocol):
	def __init__(self, logger, impl_factory):
		self.logger = logger
		self.impl_factory = impl_factory
	
	def connection_made(self, transport):
		self.logger.log_connect(transport)
		self.transport = transport
		self.writer = MSNPWriter(self.logger, transport)
		self.reader = MSNPReader(self.logger)
		self._impl = self.impl_factory(self.writer)
	
	def connection_lost(self, exc):
		self.logger.log_disconnect()
		self._impl.connection_lost()
		self._impl = None
	
	def data_received(self, data):
		impl = self._impl
		QUIT = impl.STATE_QUIT
		with self.writer:
			for m in self.reader.data_received(data):
				handler = getattr(impl, '_{}_{}'.format(impl.state, m[0].lower()), None)
				if handler is None:
					self._generic_cmd(m)
				else:
					handler(*m[1:])
				if impl.state is QUIT:
					break
		if impl.state is QUIT:
			self.transport.close()
	
	def _generic_cmd(self, m):
		impl = self._impl
		if m[0] == 'OUT':
			impl.state = impl.STATE_QUIT
			return
		self.logger.info("unknown (state = {}): {}".format(impl.state, m))