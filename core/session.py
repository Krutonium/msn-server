class Session:
	def __init__(self, state):
		self.closed = False
		self.user = None
		self.token = None
		self.state = state
	
	def send_event(self, outgoing_event):
		raise NotImplementedError('Session.send_event')
	
	def close(self):
		raise NotImplementedError

class PersistentSession(Session):
	def __init__(self, state, writer, transport):
		super().__init__(state)
		self.writer = writer
		self.transport = transport
	
	def send_event(self, outgoing_event):
		self.writer.write(outgoing_event)
	
	def get_peername(self):
		return self.transport.get_extra_info('peername')
	
	def flush(self):
		self.transport.write(self.writer.flush())
	
	def close(self):
		if self.closed:
			return
		self.transport.close()
		self.closed = True

class PollingSession(Session):
	def __init__(self, state):
		super().__init__(state)
		self.queue = [] # type: List[OutgoingEvent]
	
	def send_event(self, outgoing_event):
		self.queue.append(outgoing_event)
	
	def pull_events(self):
		q = self.queue
		self.queue = []
		return q
