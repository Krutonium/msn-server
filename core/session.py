from . import event

class Session:
	def __init__(self, state):
		self.closed = False
		self.user = None
		self.token = None
		self.state = state
	
	def data_received(self, data: bytes) -> None:
		state = self.state
		for incoming_event in state.reader.data_received(data):
			state.apply_incoming_event(incoming_event, sess)
	
	def send_event(self, outgoing_event):
		raise NotImplementedError('Session.send_event')
	
	def send_reply(self, *data):
		self.send_event(event.ReplyEvent(data))
	
	def close(self):
		raise NotImplementedError('Session.close')

class PersistentSession(Session):
	def __init__(self, state, writer, transport):
		super().__init__(state)
		self.writer = writer
		self.transport = transport
	
	def send_event(self, outgoing_event):
		self.writer.write(outgoing_event)
		self.transport.write(self.writer.flush())
	
	def get_peername(self):
		return self.transport.get_extra_info('peername')
	
	def close(self):
		if self.closed:
			return
		self.transport.close()
		self.closed = True

class PollingSession(Session):
	def __init__(self, state, logger, writer, hostname):
		super().__init__(state)
		self.logger = logger
		self.writer = writer
		self.hostname = hostname
		self.peername = None
		self.queue = [] # type: List[OutgoingEvent]
	
	def set_latest_peername(self, transport):
		self.peername = transport.get_extra_info('peername')
	
	def send_event(self, outgoing_event):
		self.queue.append(outgoing_event)
	
	def get_peername(self):
		return self.peername
	
	def flush(self):
		writer = self.writer
		for outgoing_event in self.queue:
			writer.write(outgoing_event)
		self.queue = []
		return writer.flush()
	
	def close(self):
		if self.closed:
			return
		self.closed = True

class SessionState:
	def __init__(self, reader):
		self.reader = reader
	
	def apply_incoming_event(self, incoming_event, sess: Session) -> None:
		raise NotImplementedError
	
	def on_connection_lost(self, sess: Session) -> None:
		raise NotImplementedError
