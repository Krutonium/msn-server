import sys
import db

def main(arg = None):
	emails = sys.argv[2:]
	if not emails:
		print("Nothing to do.")
		return
	print("Deleting {} accounts:".format(len(emails)))
	for e in emails:
		print('=>', e)
	ans = input("Are you sure? (y/N) ")
	if ans.lower() != 'y':
		print("Operation cancelled.")
		return
	print("Deleting.")
	
	with db.Session() as sess:
		if sys.argv[1] != "--yahoo":
			users = sess.query(db.User).filter(db.User.email.in_(emails))
			uuids = { u.uuid for u in users }
			print("delete msn account", len(uuids))
			users.delete(synchronize_session = False)
			for u in sess.query(db.User).all():
				if _remove_from_contacts(u, uuids):
					sess.add(u)
		else:
			yahoo_users = sess.query(db.UserYahoo).filter(db.UserYahoo.email.in_(emails))
			uuids = { u.uuid for u in yahoo_users }
			print("delete yahoo account", len(uuids))
			yahoo_users.delete(synchronize_session = False)
			for u_y in sess.query(db.UserYahoo).all():
				if _remove_from_yahoo_contacts(u_y, uuids):
					sess.add(u_y)
		sess.flush()

def _remove_from_contacts(user, uuids):
	none_found = True
	for c in user.contacts:
		if c['uuid'] in uuids:
			none_found = False
			break
	if none_found: return False
	user.contacts = [
		c for c in user.contacts if c['uuid'] not in uuids
	]
	print("contacts", user.email)

def _remove_from_yahoo_contacts(user_yahoo, uuids):
	none_found = True
	for c in user_yahoo.contacts:
		if c['uuid'] in uuids:
			none_found = False
			break
	if none_found: return False
	user_yahoo.contacts = [
		c for c in user_yahoo.contacts if c['uuid'] not in uuids
	]
	print("contacts", user_yahoo.email)

if __name__ == '__main__':
	main()
