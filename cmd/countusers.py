import db

def main():
	user_count_msg = '''
{} Users:
{:10} {:4} {}'''
	query = '''
		SELECT DATE(u.date_created), COUNT(CASE WHEN u.type IN ({}, {}) THEN 1 ELSE NULL END), SUM(CASE WHEN u.date_login IS NULL THEN 1 ELSE 0 END)
		FROM t_user u
		GROUP BY DATE(u.date_created)
		ORDER BY DATE(u.date_created)
	'''.format(db.TYPE_ESCARGOT, db.TYPE_LIVE)
	y_query = '''
		SELECT DATE(u_y.date_created), COUNT(CASE WHEN u_y.type IS {} THEN 1 ELSE null END), SUM(CASE WHEN u_y.date_login IS NULL THEN 1 ELSE 0 END)
		FROM t_user_ymsgr u_y
		GROUP BY DATE(u_y.date_created)
		ORDER BY DATE(u_y.date_created)
	'''.format(db.TYPE_YAHOO)
	with db.Session() as sess:
		# Get MSN users first
		for date, count, zombies in sess.execute(query):
			zombiebars = int(10 * (zombies / count + 0.04))
			print(user_count_msg.format('MSN', str(date), count, ('*' * zombiebars + '.' * (10 - zombiebars))))
		# Now get the Yahoo user count
		for date_yahoo, y_count, yahoozombies in sess.execute(y_query):
			yahoozombiebars = int(10 * (yahoozombies / y_count + 0.04))
			print(user_count_msg.format('Yahoo', str(date_yahoo), y_count, ('*' * yahoozombiebars + '.' * (10 - yahoozombiebars))))

if __name__ == '__main__':
	main()
