from db import Session, User

with Session() as sess:
	users = list(sess.query(User).all())
	
	for u in users:
		uuid_to_id = {}
		for g in u.groups:
			uuid_to_id[g['uuid']] = str(len(uuid_to_id) + 1)
		u.groups = [
			{ 'name': g['name'], 'id': uuid_to_id[g['uuid']] }
			for g in u.groups
		]
		for c in u.contacts:
			c['groups'] = [uuid_to_id[uuid] for uuid in c['groups'] if uuid in uuid_to_id]
		# make copy so sqlalchemy detects change
		u.contacts = list(u.contacts)
		sess.add(u)
