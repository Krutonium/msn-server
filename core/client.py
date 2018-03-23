from typing import Dict, Tuple, Any

class Client:
	__slots__ = ('program', 'version', 'via', '_tuple', '_hash')
	
	program: str
	version: str
	via: str
	_tuple: Tuple[str, str, str]
	_hash: int
	
	@classmethod
	def FromJSON(cls, json: Dict[str, str]) -> 'Client':
		return Client(json['program'], json['version'], json.get('via') or 'direct')
	
	@classmethod
	def ToJSON(cls, client: 'Client') -> Dict[str, str]:
		return {
			'program': client.program,
			'version': client.version,
			'via': client.via,
		}
	
	def __init__(self, program: str, version: str, via: str) -> None:
		self.program = program
		self.version = version
		self.via = via
		self._tuple = (program, version, via)
		self._hash = hash(self._tuple)
	
	def __setattr__(self, attr: str, value: Any) -> Any:
		if getattr(self, '_hash', None) is None:
			super().__setattr__(attr, value)
			return
		raise AttributeError("Immutable")
	
	def __eq__(self, other: Any) -> bool:
		if not isinstance(other, Client):
			return False
		return self._tuple == other._tuple
	
	def __hash__(self) -> int:
		return self._hash
