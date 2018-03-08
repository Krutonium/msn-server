import db

with db.Session() as sess:
	sess.execute('''
		ALTER TABLE t_user ADD COLUMN front_data TEXT NOT NULL DEFAULT ''
	''')
	sess.execute('''
		UPDATE t_user
		SET front_data = ('{"msn":{"pw_md5":"' || password_md5 || '"}}')
		WHERE password_md5 != ''
	''')
