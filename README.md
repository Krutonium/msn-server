# MSN Server

This is an MSN server. Support is planned for all MSN and WLM clients.

See [escargot.log1p.xyz](https://escargot.log1p.xyz) for how to connect.


## Support status

Currently, MSNP2 through MSNP12 are implemented. It's been tested and works with MSN 1 through MSN 7.5, with some caveats:

- Because of MSNP limitations, if you want to log in to MSN < 5, you have to store an MD5-encoded password (`User.password_md5`)

## Developers

For local development, firstly you need `openssl` to be on your `$PATH`. Then:

- in your `HOSTS`, add `127.0.0.1 m1.escargot.log1p.xyz`
- run `python dev`

The **first time** you run `python dev`, a root certificate `DO_NOT_TRUST_DevEscargotRoot.crt` is created in `dev/cert`,
it tells you to install it, and exits. To install (on Windows):

- double click the certificate
- click "Install Certificate..."
- select "Current User" for "Store Location"
- select "Place all certificates in the following store", click "Browse...", and select "Trusted Root Certification Authorities"
- click "Next" and then "Finish"

Now run `python dev` again, and it should start all the services: NB, SB, http, https.
When you visit a domain that's redirected to `127.0.0.1` using https, the dev server automatically creates a certificate.

When you run MSN now, assuming it's patched, all traffic will be going to your local dev server.
However, MSN <= 6.2 still cache the IP of the server in the registry, so you might need to clear that out
if you're testing those versions. It's located:

- MSN 1.0 - 4.7: `HKEY_CURRENT_USER\SOFTWARE\Microsoft\MessengerService\Server`
- MSN 5.0 - 6.2: `HKEY_CURRENT_USER\SOFTWARE\Microsoft\MSNMessenger\Server`

All generated certificates expire after 30 days for "security" purposes (i.e. I didn't
set it to a long period of time so as to not open anyone up to... vulnerabilities...
if they forget to uninstall the root certificate).
