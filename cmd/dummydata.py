import sys
from util.hash import hasher, hasher_md5, hasher_md5crypt
from core.models import Lst
from db import Base, Session, User, UserYahoo, engine
from uuid import uuid4

Base.metadata.create_all(engine)

with Session() as sess:
	if sys.argv[1] != "--yahoo":
		sess.query(User).delete()
	else:
		sess.query(UserYahoo).delete()

	uuid = [str(uuid4()), str(uuid4()), str(uuid4())]
	
	if sys.argv[1] != "--yahoo":
		sess.add(User(
			uuid = uuid[0],
			email = 'foo@hotmail.com',
			verified = True,
			name = "~~Foo~~",
			message = "Ahoy!",
			password = hasher.encode('foopass'),
			password_md5 = "cbbb9b3bc98eb2d52be3c223b7dadf35",
			settings = {},
			groups = [
				{ 'id': '1', "name": "Space Group" },
				{ 'id': '2', "name": "GroupA" },
				{ 'id': '3', "name": "GroupZ" },
			],
			contacts = [
				{
					'uuid': uuid[1],
					'name': "Bob Ross 1",
					'message': "The Joy of Painting Rules!!!1",
					'lists': (Lst.FL | Lst.AL),
					'groups': ['2', '3'],
				},
				{
					'uuid': uuid[2],
					'name': "Bob Ross 2",
					'message': "because everybody needs a friend",
					'lists': (Lst.RL),
					'groups': [],
				},
			],
		))
		sess.add(User(
			uuid = uuid[1],
			email = 'bob1@hotmail.com',
			verified = True,
			name = "Bob Ross 1",
			message = "The Joy of Painting Rules!!!1",
			password = hasher.encode('foopass'),
			password_md5 = "cbbb9b3bc98eb2d52be3c223b7dadf35",
			settings = {},
			groups = [],
			contacts = [],
		))
		sess.add(User(
			uuid = uuid[2],
			email = 'bob2@hotmail.com',
			verified = True,
			name = "Bob Ross 2",
			message = "because everybody needs a friend",
			password = hasher.encode('foopass'),
			password_md5 = "cbbb9b3bc98eb2d52be3c223b7dadf35",
			settings = {},
			groups = [],
			contacts = [
				{
					'uuid': uuid[0],
					'name': "Foo",
					'message': "Ahoy!",
					'lists': (Lst.FL | Lst.AL),
					'groups': [],
				},
			],
		))
	else:
		sess.add(UserYahoo(
			uuid = uuid[0],
			email = 'foo@yahoo.com',
			verified = True,
			yahoo_id = "foo",
			password_md5 = hasher_md5.encode_unsalted('yahfoopass'),
			password_md5crypt = hasher_md5crypt.encode('yahfoopass', '$1$_2S43d5f'),
			groups = [
				{ 'id': '1', "name": "BuckoGroupA" },
				{ 'id': '2', "name": "BuckoGroupB" },
			],
			contacts = [
				{
					'uuid': uuid[1],
					'yahoo_id': "bobross1",
					'groups': ['1'],
				},
				{
					'uuid': uuid[2],
					'yahoo_id': "bobross2",
					'groups': ['2'],
				},
			],
		))
		sess.add(UserYahoo(
			uuid = uuid[1],
			email = 'bobross1@yahoo.com',
			verified = True,
			yahoo_id = "bobross1",
			password_md5 = hasher_md5.encode_unsalted('yahfoopass'),
			password_md5crypt = hasher_md5crypt.encode('yahfoopass', '$1$_2S43d5f'),
			groups = [],
			contacts = [],
		))
		sess.add(UserYahoo(
			uuid = uuid[2],
			email = 'bobross2@yahoo.com',
			verified = True,
			yahoo_id = "bobross2",
			password_md5 = hasher_md5.encode_unsalted('yahfoopass'),
			password_md5crypt = hasher_md5crypt.encode('yahfoopass', '$1$_2S43d5f'),
			groups = [
				{ 'id': '1', "name": "Friends" },
			],
			contacts = [
				{
					'uuid': uuid[0],
					'yahoo_id': "foo",
					'groups': ['1'],
				},
			],
		))
