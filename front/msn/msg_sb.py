from .misc import Err, MSNPHandlers

_handlers = MSNPHandlers()
apply = _handlers.apply

# State = Auth

@_handlers
def _m_usr(sess, trid, email, token):
	#>>> USR trid email@example.com token
	state = sess.state
	data = state.backend.login_xfr(sess, email, token)
	if data is None:
		sess.send_reply(Err.AuthFail, trid)
		return
	(chat, extra_data) = data
	dialect = extra_data['dialect']
	user = sess.user
	state.dialect = dialect
	state.chat = chat
	sess.send_reply('USR', trid, 'OK', user.email, user.status.name)

@_handlers
def _m_ans(sess, trid, email, token, sessid):
	#>>> ANS trid email@example.com token sessionid
	state = sess.state
	data = state.backend.login_cal(sess, email, token, sessid)
	if data is None:
		sess.send_reply(Err.AuthFail, trid)
		return
	(chat, extra_data) = data
	dialect = extra_data['dialect']
	state.dialect = dialect
	state.chat = chat
	state.capabilities = extra_data['capabilities']
	roster = [
		(sc, su) for (sc, su) in chat.get_roster(sess)
		if su != sess.user
	]
	l = len(roster)
	for i, (sc, su) in enumerate(roster):
		extra = ()
		if dialect >= 13:
			extra = (sc.state.capabilities,)
		sess.send_reply('IRO', trid, i + 1, l, su.email, su.status.name, *extra)
	sess.send_reply('ANS', trid, 'OK')

# State = Live

@_handlers
def _m_cal(sess, trid, callee_email):
	#>>> CAL trid email@example.com
	state = sess.state
	user = sess.user
	chat = state.chat
	try:
		state.backend.notify_call(user.uuid, callee_email, chat.id)
	except Exception as ex:
		sess.send_reply(Err.GetCodeForException(ex), trid)
	else:
		sess.send_reply('CAL', trid, 'RINGING', chat.id)

@_handlers
def _m_msg(sess, trid, ack, data):
	#>>> MSG trid [UNAD] len
	sess.state.chat.send_message_to_everyone(sess, data)
	
	# TODO: Implement ACK/NAK
	if ack == 'U':
		return
	any_failed = False
	if any_failed: # ADN
		sess.send_reply('NAK', trid)
	elif ack != 'N': # AD
		sess.send_reply('ACK', trid)
