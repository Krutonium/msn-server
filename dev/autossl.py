import os
from os.path import exists
import sys
import ssl
import datetime

from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import hashes

CERT_DIR = 'dev/cert'
CERT_ROOT_CA = 'DO_NOT_TRUST_DevEscargotRoot'

def perform_checks():
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
			cert, key = autovivify_certificate(domain)
			ctxt.load_cert_chain(cert, keyfile = key)
			cache[domain] = ctxt
		socket.context = cache[domain]
	
	ssl_context.set_servername_callback(servername_callback)
	return ssl_context

def autovivify_certificate(domain):
	f_base = '{}/{}'.format(CERT_DIR, domain)
	f_crt = '{}.crt'.format(f_base)
	f_key = '{}.key'.format(f_base)
	
	if not exists(f_crt):
		key = create_key()
		csr = create_csr(key, domain)
		root_crt_filename, root_key_filename = autovivify_root_ca()
		root_crt = load_cert(root_crt_filename)
		root_key = load_key(root_key_filename)
		crt = sign_csr(csr, key, root_crt, root_key)
		save_key(key, f_key)
		save_cert(crt, f_crt)
	
	return f_crt, f_key

def autovivify_root_ca():
	os.makedirs(CERT_DIR, exist_ok = True)
	
	f_base = '{}/{}'.format(CERT_DIR, CERT_ROOT_CA)
	f_key = '{}.key'.format(f_base)
	f_crt = '{}.crt'.format(f_base)
	
	if exists(f_key) and exists(f_crt):
		return f_crt, f_key
	
	key = create_key()
	crt = create_selfsigned_cert(key, CERT_ROOT_CA)
	save_key(key, f_key)
	save_cert(crt, f_crt)
	
	return None

def create_key():
	return rsa.generate_private_key(
		public_exponent = 65537, key_size = 2048,
		backend = default_backend()
	)

def load_key(filename):
	backend = default_backend()
	with open(filename, 'rb') as fh:
		return serialization.load_pem_private_key(fh.read(), None, backend)

def save_key(key, filename):
	with open(filename, 'wb') as ff:
		ff.write(key.private_bytes(
			encoding = serialization.Encoding.PEM,
			format = serialization.PrivateFormat.TraditionalOpenSSL,
			encryption_algorithm = serialization.NoEncryption(),
		))

def create_csr(key, domain):
	csr = x509.CertificateSigningRequestBuilder()
	csr = csr.subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, domain)]))
	csr = csr.add_extension(x509.SubjectAlternativeName([x509.DNSName(domain)]), critical = False)
	csr = csr.sign(key, hashes.SHA256(), default_backend())
	return csr

def sign_csr(csr, key, root_crt, root_key, *, days = 30):
	cert = x509.CertificateBuilder()
	cert = cert.subject_name(csr.subject)
	for ext in csr.extensions:
		cert = cert.add_extension(ext.value, critical = ext.critical)
	cert = cert.issuer_name(root_crt.subject)
	cert = cert.public_key(key.public_key())
	cert = cert.serial_number(x509.random_serial_number())
	cert = cert.not_valid_before(datetime.datetime.utcnow())
	cert = cert.not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days = days))
	cert = cert.sign(root_key, hashes.SHA256(), default_backend())
	return cert

def create_selfsigned_cert(key, common_name, *, days = 30):
	subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
	cert = x509.CertificateBuilder()
	cert = cert.subject_name(subject)
	cert = cert.issuer_name(subject)
	cert = cert.public_key(key.public_key())
	cert = cert.serial_number(x509.random_serial_number())
	cert = cert.not_valid_before(datetime.datetime.utcnow())
	cert = cert.not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days = days))
	cert = cert.sign(key, hashes.SHA256(), default_backend())
	return cert

def load_cert(filename):
	backend = default_backend()
	with open(filename, 'rb') as fh:
		return x509.load_pem_x509_certificate(fh.read(), backend)

def save_cert(crt, filename):
	with open(filename, 'wb') as ff:
		ff.write(crt.public_bytes(serialization.Encoding.PEM))
