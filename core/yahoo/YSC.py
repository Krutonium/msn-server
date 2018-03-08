from typing import Dict
import secrets

_session_clearing: Dict[str, int] = {}

class YahooSessionClearing:
	def __init__(self, user):
		self.dup = False
		if _session_clearing.get(user) is not None:
			self.dup = True
			return
		self.id = self.gen_session_id()
		_session_clearing[user] = self.id
	
	def pop_session(self, user):
		if _session_clearing[user] == self.id:
			del _session_clearing[user]
	
	def gen_session_id(self):
		id = secrets.randbelow(4294967294) + 1
		if id in _session_clearing.values():
			id = self.gen_session_id()
		return id
	
	def retrieve_session_id(self, user):
		return _session_clearing[user]
