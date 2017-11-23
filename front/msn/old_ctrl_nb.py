class NB:
	def notify_call(self, caller_uuid, callee_email, sbsess_id):
		caller = self._user_by_uuid.get(caller_uuid)
		if caller is None: return Err.InternalServerError
		if caller.detail is None: return Err.InternalServerError
		callee_uuid = self._user_service.get_uuid(callee_email)
		if callee_uuid is None: return Err.InternalServerError
		ctc = caller.detail.contacts.get(callee_uuid)
		if ctc is None: return Err.InternalServerError
		if ctc.status.is_offlineish(): return Err.PrincipalNotOnline
		ctc_ncs = self._ncs.get_ncs_by_user(ctc.head)
		if not ctc_ncs: return Err.PrincipalNotOnline
		for ctc_nc in ctc_ncs:
			token = self._auth_service.create_token('sb/cal', { 'uuid': ctc.head.uuid, 'dialect': ctc_nc.dialect })
			ctc_nc.notify_ring(sbsess_id, token, caller)
	
	def notify_reverse_add(self, user1, user2):
		# `user2` was added to `user1`'s RL
		if user1 == user2: return
		for nc in self._ncs.get_ncs_by_user(user1):
			nc.notify_add_rl(user2)

class NBConn:
	def notify_ring(self, sbsess_id, token, caller):
		sb = self.nb.get_sbservice()
		extra = ()
		if self.dialect >= 13:
			extra = ('U', 'messenger.hotmail.com')
		if self.dialect >= 14:
			extra += (1,)
		self.writer.write('RNG', sbsess_id, '{}:{}'.format(sb.host, sb.port), 'CKI', token, caller.email, caller.status.name, *extra)
	
	def notify_add_rl(self, user):
		name = (user.status.name or user.email)
		if self.dialect < 10:
			self.writer.write('ADD', 0, Lst.RL.name, user.email, name)
		else:
			self.writer.write('ADC', 0, Lst.RL.name, 'N={}'.format(user.email), 'F={}'.format(name))
