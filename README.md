# MSN Server

This is an MSN server. Support is planned for all MSN and WLM clients.

See [escargot.log1p.xyz](https://escargot.log1p.xyz) for how to connect.


## Support status

MSNP:

Currently, MSNP2 through MSNP12 are implemented. Its been tested and works with MSN 1 through MSN 7.5, with some caveats:

- Because of MSNP limitations, if you want to log in to MSN < 5, you have to store an MD5-encoded password (`User.password_md5`)

YMSG:

As of now, only YMSG10 is implemented. It has only been tested on two Yahoo! Messenger 5.5 builds, 1237 and 1244, in which the latter build works without crashing post-auth (at least on Windows 7).

## Developers

See [CONTRIBUTING.md](/CONTRIBUTING.md).
