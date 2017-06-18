def main():
	from util import runner
	import settings
	runner.run_everything(
		http_stuff = ('0.0.0.0', 80),
		nb_port = settings.NB.port,
		sb_services = [settings.Service('127.0.0.1', settings.SB[0].port)],
		devmode = True,
	)

if __name__ == '__main__':
	main()
