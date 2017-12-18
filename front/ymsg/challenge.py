from hashlib import md5
from uuid import uuid4

from db import Session, UserYahoo as DBUser_Yahoo
from .yahoo_lib.Y64 import Y64Encode

# V1 challenge definitions

CHECKSUM_POS = [7, 9, 15, 1, 3, 7, 9, 15]

USERNAME = 0
PASSWORD = 1
CHALLENGE = 2

STRING_ORDER = (
	(PASSWORD, USERNAME, CHALLENGE),
	(USERNAME, CHALLENGE, PASSWORD),
	(CHALLENGE, PASSWORD, USERNAME),
	(USERNAME, PASSWORD, CHALLENGE),
	(PASSWORD, CHALLENGE, USERNAME),
	(PASSWORD, USERNAME, CHALLENGE),
	(USERNAME, CHALLENGE, PASSWORD),
	(CHALLENGE, PASSWORD, USERNAME)
)

def generate_challenge_v1():
	# Yahoo64-encode the raw 16 bytes of a UUID
	return Y64Encode(uuid4().bytes)

def verify_challenge_v1(user_y, chal, resp_6, resp_96):
	# Yahoo! clients tend to remove "@yahoo.com" if a user logs in, but it might not check for other domains; double check for that
	if user.find('@') == -1:
	    email = user_y + '@yahoo.com'
	else:
	    email = user_y
	
	    with Session() as sess:
	        dbuser = sess.query(DBUser_Yahoo).filter(DBUser_Yahoo.email == email).one_or_none()
	        if dbuser is None: return False
	        # Retreive Yahoo64-encoded MD5 hash of the user's password from the database
	        # NOTE: The MD5 hash of the is literally unsalted. Good grief, Yahoo!
	        pass_md5 = dbuser.password_md5
	        # Retreive MD5-crypt(3)'d hash of the user's password from the database
	        pass_md5crypt = dbuser.password_md5crypt
	
	pass_hashes = [pass_md5, Y64Encode(md5(pass_md5crypt.encode()).digest())]
	
	mode = ord(original_chal[15]) % 8
	
	# Note that the "checksum" is not a static character
	CHECKSUM = original_chal[ord(original_chal[CHALLENGE_CONST_VARS.CHECKSUM_POS[mode]]) % 16]
	
	resp6_md5 = md5()
	resp6_md5.update(CHECKSUM)
	resp6_md5.update(_chal_combine(user_y, pass_hashes[0], chal, mode))
	resp_6_server = Y64Encode(resp6_md5.digest())
	
	resp96_md5 = md5()
	resp96_md5.update(CHECKSUM)
	resp96_md5.update(chal_combine(user_y, pass_hashes[1], chal, mode))
	resp_96_server = Y64Encode(resp96_md5.digest())
	
	# TODO: Only the first response string generated on the server side is correct for some odd reason; either YMSG10's response function is slightly modified or something is wrong.
	
	if resp_6 == resp_6_server or resp_96 == resp_6_server:
		return True
	else:
		return False

def _chal_combine(username, passwd, chal, mode):
	out = ''
	cred_arr = [username, passwd, chal]
	
	for i in range(0, 2):
		out += cred_arr[CHALLENGE_CONST_VARS.STRING_ORDER[mode][i]]
	
	return out
