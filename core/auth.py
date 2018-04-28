from typing import Dict, List, Any, Optional
import bisect
from time import time as time_builtin
from functools import total_ordering
from util.hash import gen_salt

class AuthService:
	__slots__ = ('_time', '_ordered', '_bytoken', '_idxbase')
	
	_time: Any
	# Ordered by TokenData.expiry
	_ordered: List['TokenData']
	_bytoken: Dict[str, int]
	_idxbase: int
	
	@classmethod
	def GenTokenStr(cls) -> str:
		return gen_salt(20)
	
	def __init__(self, *, time: Optional[Any] = None) -> None:
		if time is None:
			time = time_builtin
		self._time = time
		self._ordered = []
		self._bytoken = {}
		self._idxbase = 0
	
	def create_token(self, purpose: str, data: Any, *, token: Optional[str] = None, lifetime: int = 30) -> str:
		self._remove_expired()
		td = TokenData(purpose, data, self._time() + lifetime, token = AuthService.GenTokenStr() if token is None else token)
		assert td.token not in self._bytoken
		idx = bisect.bisect_left(self._ordered, td)
		self._ordered.insert(idx, td)
		self._bytoken[td.token] = idx + self._idxbase
		return td.token
	
	def pop_token(self, purpose: str, token: str) -> Optional[Any]:
		self._remove_expired()
		idx = self._bytoken.pop(token, None)
		if idx is None: return None
		idx -= self._idxbase
		td = self._ordered[idx]
		if not td.validate(purpose, token, self._time()): return None
		return td.data
	
	def get_token(self, purpose: str, token: str) -> Optional[Any]:
		self._remove_expired()
		idx = self._bytoken.get(token)
		if idx is None: return None
		idx -= self._idxbase
		td = self._ordered[idx]
		if not td.validate(purpose, token, self._time()): return None
		return td.data
	
	def get_token_expiry(self, purpose: str, token: str) -> Optional[Any]:
		self._remove_expired()
		idx = self._bytoken.get(token)
		if idx is None: return None
		idx -= self._idxbase
		td = self._ordered[idx]
		if not td.validate(purpose, token, self._time()): return None
		return td.expiry
	
	def sysboard_retreive_last_valid_token(self, password: str) -> Optional[str]:
		self._remove_expired()
		for td in self._ordered:
			if td.purpose == 'sysboard/token' and td.data == password: return td.token
		return None
	
	def _remove_expired(self) -> None:
		if not self._ordered: return
		dummy = TokenData('', None, self._time(), '')
		idx = bisect.bisect(self._ordered, dummy)
		if idx < 1: return
		self._idxbase += idx
		for td in self._ordered[:idx]:
			self._bytoken.pop(td.token, None)
		self._ordered = self._ordered[idx:]

@total_ordering
class TokenData:
	__slots__ = ('token', 'purpose', 'data', 'expiry')
	
	token: str
	purpose: str
	data: Any
	expiry: int
	
	def __init__(self, purpose: str, data: Any, expiry: int, token: str) -> None:
		self.token = token
		self.purpose = purpose
		self.expiry = expiry
		self.data = data
	
	def __le__(self, other: 'TokenData') -> bool:
		return self.expiry <= other.expiry
	
	def validate(self, purpose: str, token: str, now: int) -> bool:
		if self.expiry <= now: return False
		if self.purpose != purpose: return False
		if self.token != token: return False
		return True
