from typing import Dict, Optional, List, Tuple
from datetime import datetime

from util.hash import hasher, hasher_md5, hasher_md5crypt

from .db import Session, User as DBUser, OIM as DBOIM
from .models import User, Contact, UserStatus, UserDetail, Group, OIMMetadata

class UserService:
	_cache_by_uuid: Dict[str, Optional[User]]
	
	def __init__(self) -> None:
		self._cache_by_uuid = {}
	
	def login(self, email: str, pwd: str) -> Optional[str]:
		with Session() as sess:
			dbuser = sess.query(DBUser).filter(DBUser.email == email).one_or_none()
			if dbuser is None: return None
			if not hasher.verify(pwd, dbuser.password): return None
			return dbuser.uuid
	
	def msn_login_md5(self, email: str, md5_hash: str) -> Optional[str]:
		with Session() as sess:
			dbuser = sess.query(DBUser).filter(DBUser.email == email).one_or_none()
			if dbuser is None: return None
			if not hasher_md5.verify_hash(md5_hash, dbuser.get_front_data('msn', 'pw_md5') or ''): return None
			return dbuser.uuid
	
	def msn_get_md5_salt(self, email: str) -> Optional[str]:
		with Session() as sess:
			dbuser = sess.query(DBUser).filter(DBUser.email == email).one_or_none()
			if dbuser is None: return None
			pw_md5 = dbuser.get_front_data('msn', 'pw_md5')
		if pw_md5 is None: return None
		return hasher.extract_salt(pw_md5)
	
	def yahoo_get_md5_password(self, uuid: str) -> Optional[bytes]:
		with Session() as sess:
			dbuser = sess.query(DBUser).filter(DBUser.uuid == uuid).one_or_none()
			if dbuser is None: return None
			return hasher_md5.extract_hash(dbuser.get_front_data('ymsg', 'pw_md5_unsalted') or '')
	
	def yahoo_get_md5crypt_password(self, uuid: str) -> Optional[bytes]:
		with Session() as sess:
			dbuser = sess.query(DBUser).filter(DBUser.uuid == uuid).one_or_none()
			if dbuser is None: return None
			return hasher_md5crypt.extract_hash(dbuser.get_front_data('ymsg', 'pw_md5crypt') or '')
	
	def update_date_login(self, uuid: str) -> None:
		with Session() as sess:
			sess.query(DBUser).filter(DBUser.uuid == uuid).update({
				'date_login': datetime.utcnow(),
			})
	
	def get_uuid(self, email: str) -> Optional[str]:
		with Session() as sess:
			tmp = sess.query(DBUser.uuid).filter(DBUser.email == email).one_or_none()
			return tmp and tmp[0]
	
	def get(self, uuid: str) -> Optional[User]:
		if uuid is None: return None
		if uuid not in self._cache_by_uuid:
			self._cache_by_uuid[uuid] = self._get_uncached(uuid)
		return self._cache_by_uuid[uuid]
	
	def _get_uncached(self, uuid: str) -> Optional[User]:
		with Session() as sess:
			dbuser = sess.query(DBUser).filter(DBUser.uuid == uuid).one_or_none()
			if dbuser is None: return None
			status = UserStatus(dbuser.name, dbuser.message)
			return User(dbuser.uuid, dbuser.email, dbuser.verified, status, dbuser.date_created)
	
	def get_detail(self, uuid: str) -> Optional[UserDetail]:
		with Session() as sess:
			dbuser = sess.query(DBUser).filter(DBUser.uuid == uuid).one_or_none()
			if dbuser is None: return None
			detail = UserDetail(dbuser.settings)
			for g in dbuser.groups:
				grp = Group(**g)
				detail.groups[grp.id] = grp
			for c in dbuser.contacts:
				ctc_head = self.get(c['uuid'])
				if ctc_head is None: continue
				status = UserStatus(c['name'], c['message'])
				ctc = Contact(
					ctc_head, set(c['groups']), c['lists'], status,
					is_messenger_user = c.get('is_messenger_user'),
				)
				detail.contacts[ctc.head.uuid] = ctc
		return detail
	
	def get_oim_batch(self, to_member_name: str) -> Tuple[OIMMetadata]:
		tmp_oims = []
		with Session() as sess:
			dboim = sess.query(DBOIM).filter(DBOIM.to_member_name == to_member_name)
			if dboim is None: return None
			for oim in dboim:
				if not oim.is_read: tmp_oims.append(OIMMetadata(
					oim.run_id, oim.oim_num, oim.from_member_name, oim.from_member_friendly,
					oim.to_member_name, oim.oim_sent, len(oim.content),
				))
		return tuple(tmp_oims)
	
	def get_oim_single(self, to_member_name: str, run_id: str) -> Tuple[OIMMetadata]:
		with Session() as sess:
			dboim = sess.query(DBOIM).filter(DBOIM.to_member_name == to_member_name).filter(DBOIM.run_id == run_id).one_or_none()
			if dboim is None: return None
		return (OIMMetadata(
					dboim.run_id, dboim.oim_num, dboim.from_member_name, dboim.from_member_friendly,
					dboim.to_member_name, dboim.oim_sent, len(dboim.content),
				),)
	
	def get_oim_message_by_uuid(self, to_member_name: str, run_id: str, markAsRead: Optional[bool] = None) -> str:
		with Session() as sess:
			dboim = sess.query(DBOIM).filter(DBOIM.to_member_name == to_member_name).filter(DBOIM.run_id == run_id).one_or_none()
			if dboim is None: return None
			msg_content = dboim.content
			if isinstance(markAsRead, bool) and markAsRead:
				dboim.is_read = int(markAsRead)
				sess.add(dboim)
		return msg_content
	
	def save_oim(self, run_id: str, seq_num: int, content: str, from_member: str, from_member_friendly: str, recipient: str, sent: datetime) -> None:
		with Session() as sess:
			dboim = sess.query(DBOIM).filter(DBOIM.run_id == run_id).one_or_none()
			if dboim is None:
				dboim = DBOIM(
					run_id = run_id, oim_num = seq_num, from_member_name = from_member, from_member_friendly = from_member_friendly,
					to_member_name = recipient, oim_sent = sent, content = content, is_read = 0,
				)
			else:
				dboim.oim_num = seq_num
				dboim.from_member_friendly = from_member_friendly
				dboim.oim_sent = sent
				dboim.content = content
				dboim.is_read = 0
			sess.add(dboim)
	
	def delete_oim(self, run_id: str) -> bool:
		with Session() as sess:
			dboim = sess.query(DBOIM).filter(DBOIM.run_id == run_id).one_or_none()
			if dboim is None: return False
			
			sess.delete(dboim)
			sess.commit()
		return True
	
	def save_batch(self, to_save: List[Tuple[User, UserDetail]]) -> None:
		with Session() as sess:
			for user, detail in to_save:
				dbuser = sess.query(DBUser).filter(DBUser.uuid == user.uuid).one()
				dbuser.name = user.status.name
				dbuser.message = user.status.message
				dbuser.settings = detail.settings
				dbuser.groups = [{
					'id': g.id, 'name': g.name,
					'is_favorite': g.is_favorite,
				} for g in detail.groups.values()]
				dbuser.contacts = [{
					'uuid': c.head.uuid, 'name': c.status.name, 'message': c.status.message,
					'lists': c.lists, 'groups': list(c.groups),
					'is_messenger_user': c.is_messenger_user,
				} for c in detail.contacts.values()]
				sess.add(dbuser)
