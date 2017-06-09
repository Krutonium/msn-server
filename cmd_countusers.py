import db

def main():
	query = '''
		SELECT DATE(u.date_created), COUNT(*)
		FROM t_user u
		GROUP BY DATE(u.date_created)
		ORDER BY DATE(u.date_created)
	'''
	with db.Session() as sess:
		for date, count in sess.execute(query):
			print('{:10} {:4}'.format(str(date), count))

if __name__ == '__main__':
	main()
