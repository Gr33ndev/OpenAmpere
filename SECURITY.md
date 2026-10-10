# Security

OpenAmpere can change settings of an inverter. That is why we take security vulnerabilities seriously.

## Reporting a vulnerability

Please do **not** report it as a public issue. Use GitHub's private reporting instead:
**Security → Report a vulnerability** in the repository
(<https://github.com/Gr33ndev/OpenAmpere/security/advisories/new>). Describe what is affected and how to reproduce it. Reports in
German are welcome and are handled the same way.

We will get back to you as soon as possible, usually within a week. Please give us time to fix it before you publish
details.

## How a report is handled

1. **Acknowledge:** the maintainer answers in the private advisory, usually within a week.
2. **Assess:** reproduce the problem, decide which versions are affected and rate the severity (CVSS). If it is not a
   vulnerability, the advisory is closed with an explanation; it can then continue as a normal issue.
3. **Fix:** the fix is developed privately (in the advisory's temporary private fork), with a test that fails without
   it. Fixes go into the next release; there are no fixes for older versions, because updating is one tap in the app
   (or happens at night with automatic updates).
4. **Release and publish:** a new version is released, the advisory is published (with a CVE for medium or higher
   severity) and the release notes point to it.
5. **Credit:** the reporter is named in the advisory, unless they want to stay anonymous.

## What OpenAmpere protects

- Viewing on the home network is possible without logging in. Changing settings, control and backups require a
  password.
- Protection against third-party websites (CSRF header, origin check) and against DNS rebinding (host check).
- Control features are off by default and start in test mode.
- Credentials (passwords for the grid operator portal and evcc, ntfy token, cloud API keys) are stored encrypted in the
  database (AES-256-GCM). The key is in `data/secret.key`, readable only by the app user. The database alone does not
  reveal them, and backups do not contain them at all. The app never returns them, and for passwords not even parts of
  them. The app's own access password is only stored as a scrypt hash.
- Other apps (e.g. the Home Assistant integration) connect only over HTTPS, pin the certificate of OpenAmpere and use
  a token that is shown once, stored only as a hash and limited to reading or to the released commands.
- Updates, from the app and by running the installer again, are only started if the image was built by the release
  workflow of this repository (signed build provenance, see [Verifying a release](README.md#verifying-a-release)).

## What OpenAmpere does not protect

- The web app uses plain HTTP in the home network and is not meant to run on the open internet. Access from outside
  the home only via a VPN, e.g. the Tailscale option of the install script (with HTTPS), **never via port
  forwarding**. The HTTPS port (8443 by default) is meant for other apps, which pin its self-signed certificate.
- Anyone who can read the entire `data/` folder (key and database) can decrypt the credentials. OpenAmpere has to be
  able to read them after every restart without a login, to fetch data from the grid operator and evcc. So do not
  share the folder, and secure the server itself.
- Credentials in `config.yaml` or environment variables are stored there in plain text.
- Modbus TCP itself has no authentication. Anyone on the home network can talk to the inverter even without
  OpenAmpere.
- Until a password is set, anyone on the home network can set it. Set it right after the installation.

Why these protections are enough for the intended use, with the threat model and trust boundaries:
[docs/assurance-case.md](docs/assurance-case.md).
