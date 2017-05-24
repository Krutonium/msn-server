import db

with db.Session() as sess:
	sess.execute('ALTER TABLE t_user ADD COLUMN type INTEGER NOT NULL DEFAULT 1')
	sess.execute('ALTER TABLE t_user ADD COLUMN date_created DATETIME')
	sess.execute('ALTER TABLE t_user ADD COLUMN date_login DATETIME')
