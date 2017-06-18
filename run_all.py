def main():
	from util import runner
	import settings
	runner.run_everything(
		http_stuff = ('127.0.0.1', 8081),
		nb_port = settings.NB.port,
		sb_services = settings.SB,
	)

if __name__ == '__main__':
	main()
