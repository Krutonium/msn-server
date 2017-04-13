import hashlib
import hmac
import random
import base64

class PBKDF2PasswordHasher:
	algorithm = 'pbkdf2_sha256'
	iterations = 24000
	digest = hashlib.sha256
	separator = '$'
	
	@classmethod
	def encode(cls, password, salt = None, iterations = None):
		assert password is not None
		
		if not salt:
			salt = cls.salt()
		assert cls.separator not in salt
		
		if not iterations:
			iterations = cls.iterations
		hash = hashlib.pbkdf2_hmac(cls.digest().name, password.encode(), salt.encode(), iterations, None)
		hash = base64.b64encode(hash).decode('ascii').strip()
		return cls.separator.join((cls.algorithm, str(iterations), salt, hash))
	
	@classmethod
	def verify(cls, password, encoded):
		try:
			algorithm, iterations, salt, hash = encoded.split(cls.separator, 3)
		except ValueError:
			return False
		assert algorithm == cls.algorithm
		encoded_2 = cls.encode(password, salt, int(iterations))
		return hmac.compare_digest(encoded, encoded_2)
	
	@classmethod
	def salt(cls, length = 15):
		seed_chars = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
		return ''.join(random.choice(seed_chars) for i in range(length))

hasher = PBKDF2PasswordHasher
