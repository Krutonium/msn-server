import hashlib
import secrets
import random
import base64
import binascii
from typing import Dict

class Hasher:
	# Can't leave it as None or mypy will complain
	algorithm = 'unknown'
	separator = '$'
	
	@classmethod
	def encode(cls, password, salt = None, *stuff):
		assert password is not None
		if not salt:
			salt = gen_salt()
		assert cls.separator not in salt
		(hash, *other_stuff) = cls._encode_impl(password, salt, *stuff)
		hash = base64.b64encode(hash).decode('ascii').strip()
		return cls.separator.join([cls.algorithm] + other_stuff + [salt, hash])
	
	@classmethod
	def encode_unsalted(cls, password, *stuff):
	    # This function is specifically for Yahoo!
		assert password is not None
		(hash, *other_stuff) = cls._encode_impl_unsalted(password, *stuff)
		hash = base64.b64encode(hash).decode('ascii').strip()
		return cls.separator.join([cls.algorithm] + other_stuff + [hash])
	
	@classmethod
	def extract_salt(cls, encoded):
		try: (*_, salt, _) = encoded.split(cls.separator)
		except ValueError: return None
		return salt
	
	@classmethod
	def verify(cls, password, encoded):
		try: (algorithm, *stuff, salt, hash) = encoded.split(cls.separator)
		except ValueError: return False
		
		try: hasher = cls._HASHERS[algorithm]
		except KeyError: return False
		
		assert algorithm == hasher.algorithm
		encoded_2 = hasher.encode(password, salt, *stuff)
		return secrets.compare_digest(encoded, encoded_2)
	
	_HASHERS = {} # type: Dict[str, type]

class PBKDF2PasswordHasher(Hasher):
	algorithm = 'pbkdf2_sha256'
	iterations = 24000
	
	@classmethod
	def _encode_impl(cls, password, salt, iterations = None):
		if not iterations:
			iterations = cls.iterations
		iterations = int(iterations)
		hash = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), iterations, None)
		return (hash, str(iterations))
Hasher._HASHERS[PBKDF2PasswordHasher.algorithm] = PBKDF2PasswordHasher

class MD5PasswordHasher(Hasher):
	algorithm = 'md5'
	digest = hashlib.md5
	
	@classmethod
	def _encode_impl(cls, password, salt):
		md5 = hashlib.md5()
		md5.update((salt + password).encode('utf-8'))
		return (md5.digest(),)
	
	@classmethod
	def _encode_impl_unsalted(cls, password):
        # This function is specifically for Yahoo!
		md5 = hashlib.md5()
		md5.update(password.encode('utf-8'))
		return (md5.digest(),)
	
	@classmethod
	def extract_hash(cls, encoded):
        # This function is specifically for Yahoo!
        try:
            (_, hash) = encoded.split(cls.separator)
        except ValueError:
            return False
        return hash
	
	@classmethod
	def verify_hash(cls, hash_1, encoded):
        try:
            (_, _, hash) = encoded.split(cls.separator)
        except ValueError:
            # This is for unsalted Yahoo! hashes
            try:
                (_, hash) = encoded.split(cls.separator)
            except ValueError:
                return False
		hash = binascii.hexlify(base64.b64decode(hash)).decode('ascii')
		return secrets.compare_digest(hash_1, hash)
Hasher._HASHERS[MD5PasswordHasher.algorithm] = MD5PasswordHasher

def gen_salt(length = 15):
	return secrets.token_hex(length)[:length]

hasher = PBKDF2PasswordHasher
hasher_md5 = MD5PasswordHasher
