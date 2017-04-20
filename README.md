# MSN Server

This is an MSN server. Support is planned for all MSN Messenger clients (1.0 through 7.5), and maybe some earlier WLM client.

See [escargot.log1p.xyz](https://escargot.log1p.xyz) for how to connect.


## Support status

Currently, MSNP2 and MSNP11 are implemented. It's been tested and works with MSN 1, MSN 2, and MSN 7.0.


## Developers

For local development:

- in your `HOSTS`, add `127.0.0.1 dev-msnp.escargot.log1p.xyz`
- set MSN Switcher to Development mode. This makes MSN use `dev-nexus.escargot.log1p.xyz` which pretends to accept any email/password.
- set `DEV_ACCEPT_ALL_LOGIN_TOKENS = True` in `settings_local.py` to bypass token verification
- run `serv_msnp.py`

*TODO:* Investigate skipping TWN auth by simply returning `USR OK` after `USR I`.
