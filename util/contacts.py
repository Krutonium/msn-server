from util.msnp import MSNPException, Err
from models import Group, Contact, UserStatus, Lst

MAX_GROUP_NAME_LENGTH = 61

class ContactsService:
	def __init__(self, nb, user):
		self.nb = nb
		self.user = user
	
	def add_contact(self, contact_uuid, lst, name):
		ctc_head = self.nb.load_user_record(contact_uuid)
		if ctc_head is None:
			raise MSNPException(Err.InvalidPrincipal)
		user = self.user
		ctc = self._add_to_list(user, ctc_head, lst, name)
		if lst is Lst.FL:
			# FL needs a matching RL on the contact
			self._add_to_list(ctc_head, user, Lst.RL, user.status.name)
			self.nb.notify_reverse_add(ctc_head, user)
		self.nb.sync_contact_statuses()
		return ctc, ctc_head
	
	def remove_contact(self, contact_uuid, lst):
		user = self.user
		ctc = user.detail.contacts.get(contact_uuid)
		if ctc is None:
			raise MSNPException(Err.PrincipalNotOnList)
		if lst is Lst.FL:
			# Remove from FL
			self._remove_from_list(user, ctc.head, Lst.FL)
			# Remove matching RL
			self._remove_from_list(ctc.head, user, Lst.RL)
		else:
			assert lst is not Lst.RL
			ctc.lists &= ~lst
	
	def add_group(self, name):
		if len(name) > MAX_GROUP_NAME_LENGTH:
			raise MSNPException(Err.GroupNameTooLong)
		user = self.user
		group = Group(_gen_group_id(user.detail), name)
		user.detail.groups[group.id] = group
		self.nb.mark_modified(user)
		return group
	
	def edit_group(self, group_id, name):
		g = self.user.detail.groups.get(group_id)
		if g is None:
			raise MSNPException(Err.GroupInvalid)
		if len(name) > MAX_GROUP_NAME_LENGTH:
			raise MSNPException(Err.GroupNameTooLong)
		g.name = name
		self.nb.mark_modified(self.user)
	
	def remove_group(self, group_id):
		if group_id == '0':
			raise MSNPException(Err.GroupZeroUnremovable)
		user = self.user
		try:
			del user.detail.groups[group_id]
		except KeyError:
			raise MSNPException(Err.GroupInvalid)
		for ctc in user.detail.contacts.values():
			ctc.groups.discard(group_id)
		self.nb.mark_modified(self.user)
	
	def add_group_contact(self, group_id, contact_uuid):
		if group_id == '0': return
		detail = self.user.detail
		if group_id not in detail.groups:
			raise MSNPException(Err.GroupInvalid)
		ctc = detail.contacts.get(contact_uuid)
		if ctc is None:
			raise MSNPException(Err.InvalidPrincipal)
		if group_id in ctc.groups:
			raise MSNPException(Err.PrincipalOnList)
		ctc.groups.add(group_id)
		self.nb.mark_modified(self.user)
	
	def remove_group_contact(self, group_id, contact_uuid):
		detail = self.user.detail
		ctc = detail.contacts.get(contact_uuid)
		if ctc is None:
			raise MSNPException(Err.PrincipalNotOnList)
		if group_id not in detail.groups and group_id != '0':
			raise MSNPException(Err.GroupInvalid)
		try:
			ctc.groups.remove(group_id)
		except KeyError:
			if group_id == '0':
				raise MSNPException(Err.PrincipalNotInGroup)
		self.nb.mark_modified(self.user)
	
	def _add_to_list(self, user, ctc_head, lst, name):
		# Add `ctc_head` to `user`'s `lst`
		detail = self.nb.load_detail(user)
		contacts = detail.contacts
		if ctc_head.uuid not in contacts:
			contacts[ctc_head.uuid] = Contact(ctc_head, set(), 0, UserStatus(name))
		ctc = contacts.get(ctc_head.uuid)
		if ctc.status.name is None:
			ctc.status.name = name
		ctc.lists |= lst
		self.nb.mark_modified(user, detail = detail)
		return ctc
	
	def _remove_from_list(self, user, ctc_head, lst):
		# Remove `ctc_head` from `user`'s `lst`
		detail = self.nb.load_detail(user)
		contacts = detail.contacts
		ctc = contacts.get(ctc_head.uuid)
		if ctc is None: return
		ctc.lists &= ~lst
		if not ctc.lists:
			del contacts[ctc_head.uuid]
		self.nb.mark_modified(user, detail = detail)

def _gen_group_id(detail):
	id = 1
	s = str(id)
	while s in detail.groups:
		id += 1
		s = str(id)
	return s
