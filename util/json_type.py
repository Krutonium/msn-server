from sqlalchemy import types
import json


class StringyJSON(types.TypeDecorator): # type: ignore
	impl = types.TEXT
	
	def process_bind_param(self, value, dialect):
		if value is None:
			return None
		return json.dumps(value)
	
	def process_result_value(self, value, dialect):
		if value is None:
			return None
		if value == '':
			return None
		return json.loads(value)

JSONType = StringyJSON
