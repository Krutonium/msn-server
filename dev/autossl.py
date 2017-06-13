import os
from os.path import exists
import sys
import ssl
import subprocess
import tempfile
import contextlib

CERT_DIR = 'dev/cert'
CERT_ROOT_CA = 'DO_NOT_TRUST_DevEscargotRoot'

def perform_checks():
	if not check_has_openssl():
		print('*', file = sys.stderr)
		print('*', "Can't run openssl; did you install it and is it in your $PATH?", file = sys.stderr)
		print('*', file = sys.stderr)
		return False
	
	if not autovivify_root_ca():
		print('*', file = sys.stderr)
		print('*', "New root CA '{}/{}' created.".format(CERT_DIR, CERT_ROOT_CA), file = sys.stderr)
		print('*', "Please remove the old one (if exists) and install this one.", file = sys.stderr)
		print('*', file = sys.stderr)
		return False
	
	return True

def create_context():
	ssl_context = ssl.create_default_context(purpose = ssl.Purpose.CLIENT_AUTH)
	
	cache = {}
	def servername_callback(socket, domain, ssl_context):
		if domain not in cache:
			ctxt = ssl.create_default_context(purpose = ssl.Purpose.CLIENT_AUTH)
			pem, key = autovivify_certificate(domain)
			ctxt.load_cert_chain(pem, keyfile = key)
			cache[domain] = ctxt
		socket.context = cache[domain]
	
	ssl_context.set_servername_callback(servername_callback)
	return ssl_context

def autovivify_certificate(domain):
	root_pem, root_key = autovivify_root_ca()
	
	f_base = '{}/{}'.format(CERT_DIR, domain)
	f_pem = '{}.pem'.format(f_base)
	f_key = '{}.key'.format(f_base)
	
	if not exists(f_pem):
		f_csr = '{}.csr'.format(f_base)
		run_openssl('genrsa', '-out', f_key, 2048)
		with make_san_config(domain) as configfile:
			run_openssl('req', '-new', '-key', f_key, '-out', f_csr,
				'-subj', "/CN={}".format(domain),
				'-config', configfile,
			)
			run_openssl('x509', '-req', '-in', f_csr, '-CA', root_pem, '-CAkey', root_key,
				'-CAcreateserial', '-out', f_pem, '-days', 30, '-sha256',
				'-extensions', 'v3_req', '-extfile', configfile,
			)
	
	return f_pem, f_key

@contextlib.contextmanager
def make_san_config(domain):
	with tempfile.NamedTemporaryFile('w') as fp:
		with open('dev/openssl.cnf') as fh:
			fp.write(fh.read())
		fp.write('[alt_names]\nDNS.1 = {}\n'.format(domain))
		fp.flush()
		yield fp.name

def autovivify_root_ca():
	os.makedirs(CERT_DIR, exist_ok = True)
	
	f_base = '{}/{}'.format(CERT_DIR, CERT_ROOT_CA)
	f_key = '{}.key'.format(f_base)
	f_pem = '{}.pem'.format(f_base)
	
	if exists(f_key):
		return f_pem, f_key
	
	f_crt = '{}.crt'.format(f_base)
	
	run_openssl('genrsa', '-out', f_key, 2048)
	run_openssl('req', '-x509', '-new', '-nodes',
		'-key', f_key, '-sha256', '-days', 30, '-out', f_pem,
		'-subj', '/CN={}'.format(CERT_ROOT_CA)
	)
	run_openssl('x509', '-outform', 'der', '-in', f_pem, '-out', f_crt)
	
	return None

def check_has_openssl():
	try:
		run_openssl('version')
	except OSError:
		return False
	return True

def run_openssl(*args):
	subprocess.check_call(['openssl'] + [str(a) for a in args],
		stdout = subprocess.DEVNULL, stderr = subprocess.DEVNULL
	)
