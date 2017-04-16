# MSN Server

This is an MSN server. Support is planned for all MSN Messenger clients (1.0 through 7.5), and maybe some earlier WLM client.

See [escargot.now.im](https://escargot.now.im) for how to connect.


## Support status

Currently, MSNP2 and MSNP11 are implemented. It's been tested and works with MSN 1, MSN 2, and MSN 7.0.


## Developers

For local development, run `serv_msnp.py`.

You'll also need a TWN server for the later (MSN >= 5, MSNP >= 8) versions. You can use the specially set up
`https://m1.escargot.log1p.xyz/nexusdevel` endpoint (edit your msnmsgr.exe to use it) which redirects the
client to `https://m1.escargot.log1p.xyz/login-dev` and pretends to accept any email/password. Locally,
you should have `DEV_ACCEPT_ALL_LOGIN_TOKENS = True` in `settings_local.py`.
