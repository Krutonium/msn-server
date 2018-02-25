import argparse
from db import Session, User, UserYahoo
from util.misc import gen_uuid
from util import hash

def main():
	parser = argparse.ArgumentParser(description = "Create user/change password.")
	parser.add_argument('email', help = "email of new/existing user")
	parser.add_argument('password')
	parser.add_argument(
		'--old', dest = 'old_msn_support', action = 'store_const',
		const = True, default = False, help = "old MSN support"
	)
	parser.add_argument(
		'--yahoo', dest = 'yahoo_account', action = 'store_const',
		const = True, default = False, help = "add a Yahoo! account to the database"
	)
	args = parser.parse_args()
	
	email = args.email
	pw = args.password
	
	with Session() as sess:
		dbuser_type = (User if not args.yahoo_account else UserYahoo)
		user = sess.query(dbuser_type).filter(dbuser_type.email == email).one_or_none()
		if user is None:
			print("Creating new user...")
			if not args.yahoo_account:
				user = user_type_msn(email)
			else:
				user = user_type_yahoo(email)
		else:
			print("User exists, changing password...")
		_set_passwords(user, pw, args.old_msn_support, support_yahoo = args.yahoo_account)
		sess.add(user)
	
	print("Done.")

def user_type_msn(email):
	return User(
		uuid = gen_uuid(), email = email, verified = False,
		name = email, message = '',
		settings = {}, groups = {}, contacts = {},
	)

def user_type_yahoo(email):
	return UserYahoo(
		uuid = gen_uuid(), email = email, verified = False,
		yahoo_id = email[:email.find('@')], groups = {}, contacts = {},
	)

def _set_passwords(user, pw, support_old, support_yahoo = None):
	# Temporary condition. Remove when login.yahoo.com-based Yahoo! clients are implemented.
	if not support_yahoo: user.password = hash.hasher.encode(pw)
	
	if not support_yahoo:
		user.password_md5 = (hash.hasher_md5.encode(pw) if support_old else '')
	else:
		user.password_md5 = (hash.hasher_md5.encode_unsalted(pw))
		user.password_md5crypt = hash.hasher_md5crypt.encode(pw, '$1$_2S43d5f')

if __name__ == '__main__':
	main()
