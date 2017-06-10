# MSN Server

This is an MSN server. Support is planned for all MSN and WLM clients.

See [escargot.log1p.xyz](https://escargot.log1p.xyz) for how to connect.


## Support status

Currently, MSNP2 through MSNP12 are implemented. It's been tested and works with MSN 1 through MSN 7.5, with some caveats:

- Because of MSNP limitations, if you want to log in to MSN < 5, you have to store an MD5-encoded password (`User.password_md5`)

## Developers

For local development (on the MSNP stuff):

- in your `HOSTS`, add `127.0.0.1 m1.escargot.log1p.xyz`
- set `DEV_ACCEPT_ALL_LOGIN_TOKENS = True` in `settings_local.py` to bypass token verification
- run `serv_msn.py`

**Note** that MSN will still contact that real Escargot auth servers, so you need to log in with a real email/password, and also have that account in your local `msn.sqlite`.

To work on the auth stuff is more complicated and involves adding a ton more stuff to `HOSTS`, as well handling the fact that those requests are mostly HTTPS. (**TODO:** Document this at some point.)
