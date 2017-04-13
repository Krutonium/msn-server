import db

with db.Session() as sess:
	for u in sess.query(db.User).all():
		print(u.email)
