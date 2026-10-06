# Security

OpenAmpere can change settings of an inverter. That is why we take security vulnerabilities seriously.

## Reporting a vulnerability

Please do **not** report it as a public issue. Use GitHub's private reporting instead:
**Security → Report a vulnerability** in the repository. Describe what is affected and how to reproduce it. Reports in
German are welcome and are handled the same way.

We will get back to you as soon as possible, usually within a week. Please give us time to fix it before you publish
details.

## What OpenAmpere protects

- Viewing on the home network is possible without logging in. Changing settings, control and backups require a
  password.
- Protection against third-party websites (CSRF header, origin check) and against DNS rebinding (host check).
- Control features are off by default and start in test mode.
- Credentials (passwords for the grid operator portal and evcc, ntfy token, cloud API keys) are stored encrypted in the
  database (AES-256-GCM). The key is in `data/secret.key`, readable only by the app user. The database alone does not
  reveal them, and backups do not contain them at all. The app never returns them, and for passwords not even parts of
  them. The app's own access password is only stored as a scrypt hash.

## What OpenAmpere does not protect

- OpenAmpere does not speak HTTPS and is not meant to run on the open internet. Access from outside the home only via
  a VPN, **never via port forwarding**.
- Anyone who can read the entire `data/` folder (key and database) can decrypt the credentials. OpenAmpere has to be
  able to read them after every restart without a login, to fetch data from the grid operator and evcc. So do not
  share the folder, and secure the server itself.
- Credentials in `config.yaml` or environment variables are stored there in plain text.
- Modbus TCP itself has no authentication. Anyone on the home network can talk to the inverter even without
  OpenAmpere.
