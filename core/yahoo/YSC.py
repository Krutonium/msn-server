from typing import Dict
import secrets

_session_clearing = {} # type: Dict[str, int]

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
		if len(_session_clearing) > 1:
			for id_other in _session_clearing.values():
				if id_other == id: self.gen_session_id()
		
		return id
	
	def retreive_session_id(self, user):
		return _session_clearing[user]