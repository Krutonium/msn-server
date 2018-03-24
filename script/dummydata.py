from typing import Dict, List, Any
from uuid import uuid4

from core.models import Lst
from core.db import Base, Session, User, engine

from script.user import set_passwords

def main() -> None:
	U = []
	
	for domain in ['example.com', 'yahoo.com', 'hotmail.com']:
		d = domain[0]
		for i in range(1, 5 + 1):
			name = "T{}{}".format(i, d)
			user = create_user('{}@{}'.format(name.lower(), domain), '123456', name, "{} msg".format(name))
			U.append(user)
	
	for i in range(5):
		name = "Bot{}".format(i)
		user = create_user('{}@bot.log1p.xyz'.format(name.lower()), '123456', name, "{} msg".format(name))
		U.append(user)
	
	for i, u in enumerate(U):
		contacts_by_group: Dict[str, List[User]] = {}
		
		x = randomish(u)
		for j in range(x % 4):
			contacts_by_group["" if j == 0 else "U{}G{}".format(i, j)] = []
		group_names = list(contacts_by_group.keys())
		for uc in U:
			y = x ^ randomish(uc)
			for k, group_name in enumerate(group_names):
				z = y ^ k
				if z % 2 < 1:
					contacts_by_group[group_name].append(uc)
		
		set_contacts(u, contacts_by_group)
	
	for u in U:
		u.contacts = list(u.contacts.values())
	
	Base.metadata.create_all(engine)
	with Session() as sess:
		sess.query(User).delete()
		sess.add_all(U)

def create_user(email: str, pw: str, name: str, message: str) -> User:
	user = User(
		uuid = str(uuid4()), email = email, verified = True,
		name = name, message = message, contacts = {}, groups = [],
		settings = {},
	)
	set_passwords(user, pw, support_old_msn = True, support_yahoo = True)
	return user

def set_contacts(user: User, contacts_by_group: Dict[str, List[User]]) -> None:
	user.contacts = {}
	user.groups = []
	
	for i, (group_name, group_users) in enumerate(contacts_by_group.items()):
		group_id = str(i + 1)
		if group_name:
			user.groups.append({ 'id': group_id, 'name': group_name })
		for u in group_users:
			contact = add_contact_twosided(user, u)
			if group_name:
				contact['groups'].append(group_id)

def randomish(u: User) -> int:
	return int(u.uuid[:8], 16)

def add_contact_twosided(user: User, user_contact: User) -> Dict[str, Any]:
	contact = add_contact_onesided(user, user_contact, Lst.AL | Lst.FL)
	add_contact_onesided(user_contact, user, Lst.RL)
	return contact

def add_contact_onesided(user: User, user_contact: User, lst: Lst) -> Dict[str, Any]:
	if user_contact.uuid not in user.contacts:
		user.contacts[user_contact.uuid] = {
			'uuid': user_contact.uuid, 'name': user_contact.name,
			'message': user_contact.message, 'lists': Lst.Empty, 'groups': [],
		}
	contact = user.contacts[user_contact.uuid]
	contact['lists'] |= lst
	return contact

if __name__ == '__main__':
	main()
