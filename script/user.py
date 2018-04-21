import argparse
from util import misc, hash
from core.db import Session, User

def main() -> None:
	parser = argparse.ArgumentParser(description = "Create user/change password.")
	parser.add_argument('email', help = "email of new/existing user")
	parser.add_argument('password')
	parser.add_argument(
		'--old-msn', dest = 'support_old_msn', action = 'store_const',
		const = True, default = False, help = "old MSN support"
	)
	parser.add_argument(
		'--yahoo', dest = 'support_yahoo', action = 'store_const',
		const = True, default = False, help = "Yahoo! support"
	)
	args = parser.parse_args()
	
	email = args.email
	pw = args.password
	
	with Session() as sess:
		user = sess.query(User).filter(User.email == email).one_or_none()
		if user is None:
			print("Creating new user...")
			user = User(
				uuid = misc.gen_uuid(), email = email, verified = False,
				name = email, message = '',
				settings = {}, groups = {}, contacts = {},
			)
			if support_yahoo: user.set_front_data('ymsg', 'aliases', [])
		else:
			print("User exists, changing password...")
		set_passwords(user, pw, support_old_msn = args.support_old_msn, support_yahoo = args.support_yahoo)
		sess.add(user)
	
	print("Done.")

def set_passwords(user: User, pw: str, *, support_old_msn: bool = False, support_yahoo: bool = False) -> None:
	user.password = hash.hasher.encode(pw)
	
	if support_old_msn:
		pw_md5 = hash.hasher_md5.encode(pw)
		user.set_front_data('msn', 'pw_md5', pw_md5)
	
	if support_yahoo:
		pw_md5_unsalted = hash.hasher_md5.encode(pw, salt = '')
		user.set_front_data('ymsg', 'pw_md5_unsalted', pw_md5_unsalted)
		
		pw_md5crypt = hash.hasher_md5crypt.encode(pw, salt = '$1$_2S43d5f')
		user.set_front_data('ymsg', 'pw_md5crypt', pw_md5crypt)

if __name__ == '__main__':
	main()
