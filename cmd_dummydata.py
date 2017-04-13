from util.hash import hasher
from ctrl_nb import Lst
from db import Base, Session, User, Auth, engine

Base.metadata.create_all(engine)

with Session() as sess:
	sess.query(User).delete()
	sess.query(Auth).delete()
	
	sess.add(User(
		uuid = '00000000-0000-0000-0002-000000000001',
		email = 'foo@hotmail.com',
		verified = True,
		name = "~~Foo~~",
		message = "Ahoy!",
		password = hasher.encode('foopass'),
		settings = {},
		groups = [
			{ 'uuid': '00000000-0000-0000-0001-000000000001', "name": "Space Group" },
			{ 'uuid': '00000000-0000-0000-0001-000000000002', "name": "GroupA" },
			{ 'uuid': '00000000-0000-0000-0001-000000000003', "name": "GroupZ" },
		],
		contacts = [
			{
				'uuid': '00000000-0000-0000-0002-000000000002',
				'name': "Bob Ross 1",
				'message': "The Joy of Painting Rules!!!1",
				'lists': (Lst.FL | Lst.AL),
				'groups': ['00000000-0000-0000-0001-000000000002', '00000000-0000-0000-0001-000000000003'],
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
