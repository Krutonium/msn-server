from util.hash import hasher
from ctrl_nb import Lst
from db import Base, Session, User, engine

Base.metadata.create_all(engine)

with Session() as sess:
	sess.query(User).delete()
	
	sess.add(User(
		uuid = '00000000-0000-0000-0002-000000000001',
		email = 'foo@hotmail.com',
		verified = True,
		name = "~~Foo~~",
		message = "Ahoy!",
		password = hasher.encode('foopass'),
		settings = {},
		groups = [
			{ 'id': '1', "name": "Space Group" },
			{ 'id': '2', "name": "GroupA" },
			{ 'id': '3', "name": "GroupZ" },
		],
		contacts = [
			{
				'uuid': '00000000-0000-0000-0002-000000000002',
				'name': "Bob Ross 1",
				'message': "The Joy of Painting Rules!!!1",
				'lists': (Lst.FL | Lst.AL),
				'groups': ['2', '3'],
			},
			{
				'uuid': '00000000-0000-0000-0002-000000000003',
				'name': "Bob Ross 2",
				'message': "because everybody needs a friend",
				'lists': (Lst.RL),
				'groups': [],
			},
		],
	))
	sess.add(User(
		uuid = '00000000-0000-0000-0002-000000000002',
		email = 'bob1@hotmail.com',
		verified = True,
		name = "Bob Ross 1",
		message = "The Joy of Painting Rules!!!1",
		password = hasher.encode('foopass'),
		settings = {},
		groups = [],
		contacts = [],
	))
	sess.add(User(
		uuid = '00000000-0000-0000-0002-000000000003',
		email = 'bob2@hotmail.com',
		verified = True,
		name = "Bob Ross 2",
		message = "because everybody needs a friend",
		password = hasher.encode('foopass'),
		settings = {},
		groups = [],
		contacts = [
			{
				'uuid': '00000000-0000-0000-0002-000000000001',
				'name': "Foo",
				'message': "Ahoy!",
				'lists': (Lst.FL | Lst.AL),
				'groups': [],
			},
		],
	))
