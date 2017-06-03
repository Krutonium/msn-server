# MSN Server

This is an MSN server. Support is planned for all MSN Messenger clients (1.0 through 7.5), and maybe some earlier WLM client.

See [escargot.log1p.xyz](https://escargot.log1p.xyz) for how to connect.


## Support status

Currently, MSNP2 through MSNP12 are implemented. It's been tested and works with MSN 1 through MSN 7.5, with some caveats:

- Because of MSNP limitations, if you want to log in to MSN < 5, you have to store an MD5-encoded password (`User.password_md5`)
- Because of technical (hopefully temporary) limitations, the same applies to MSN 7.5; also, only 7.5.0322 and 7.5.0324 work
- MSN 6.1 not supported (no idea why it doesn't log in); 6.0/6.2 are fine

## Developers

For local development:

- in your `HOSTS`, add `127.0.0.1 dev-msnp.escargot.log1p.xyz`
- set MSN Switcher to Development mode. This makes MSN use `dev-nexus.escargot.log1p.xyz` which pretends to accept any email/password.
- set `DEV_ACCEPT_ALL_LOGIN_TOKENS = True` in `settings_local.py` to bypass token verification
- run `serv_msnp.py`
