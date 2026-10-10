# Assurance case

Why we think OpenAmpere meets its security requirements. The requirements themselves (what it protects and what it
does not) are in [SECURITY.md](../SECURITY.md); the components are described in [architecture.md](architecture.md).
This document is kept up to date with changes to authentication, the external API, control features and the release
process.

## Claim

Used as intended (on a server in the home network, reached from outside only through a VPN), OpenAmpere does not let
anyone change inverter settings, read credentials or take over the server who could not already do so without
OpenAmpere, and it does not write values to the inverter that the user did not ask for or that are outside the
device's limits.

## Assets

1. **The inverter and the battery:** settings, control commands, the export limit. Wrong writes can affect the device,
   the warranty or the grid connection rules. This is the most important asset.
2. **Credentials:** the access password, passwords for grid operator portals and evcc, the ntfy token, cloud API keys,
   tokens of other apps.
3. **Personal data:** energy history, readings and settings that identify a household (e.g. meter numbers).
4. **The server:** the machine OpenAmpere runs on, and the update path to it.

## Threat model

| Actor | What they can do | Relevant for |
|---|---|---|
| A website on the internet, opened in the user's browser | Send requests to addresses in the home network (cross-site requests, DNS rebinding) | assets 1–3 |
| Someone in the home network (guest Wi-Fi, a compromised device) | Reach the web app, the HTTPS port and the inverter directly | assets 1–3 |
| Someone who listens in the home network | Read unencrypted traffic | assets 2–3 |
| An attacker on the internet, if the app is exposed against the advice | Everything of the above, from anywhere | out of scope: not supported, see SECURITY.md |
| A compromised dependency, build or distribution channel | Ship malicious code in an image or script | asset 4, and through it 1–3 |
| Someone with access to the server or the `data/` folder | Read the key and the database | out of scope: this is the owner's responsibility |
| The user themselves, by mistake | Enter limits that the device or the grid operator does not allow | asset 1 |

Modbus TCP has no authentication. Anyone in the home network can write to the inverter without OpenAmpere; OpenAmpere
does not make this worse and cannot prevent it.

## Trust boundaries

```
 internet ─────────────┬────────────────────────────────────────────────────────────────
                       │ VPN (Tailscale)        outbound HTTPS: grid operator, aWATTar,
                       │                        ntfy, cloud import, update check
 home network ─────────┼────────────────────────────────────────────────────────────────
   browser ── HTTP ──▶ [1] web app API ─┐
   other app ─ HTTPS ─▶ [2] external API ┤   OpenAmpere container (non-root)
                                         ├── [3] Modbus TCP ──▶ inverter, heater
                                         ├── [4] data/ (database, secret.key, TLS key)
                                         └── [5] data/update/request ──▶ updater container (Docker access)
 release pipeline (GitHub Actions) ── [6] signed image ──▶ updater
```

1. **Browser → web app:** every request passes the host check (only IP addresses and home network names, against DNS
   rebinding). Every change, and every read of secrets or identifying values, needs a session cookie (`HttpOnly`,
   `SameSite=Strict`), a matching `Origin` and the custom CSRF header. Reading live values is open in the home network
   by design.
2. **Other apps → external API:** HTTPS only, the app pins the certificate fingerprint from the connection code, tokens
   have 256 random bits, are stored as SHA-256 hashes, can be revoked and are limited to `read` or `control`. Pairing
   needs approval in the web app.
3. **OpenAmpere → inverter:** only the control features write, and only through the checks in
   [Writing to the inverter](#writing-to-the-inverter). Continuous control uses the inverter's remote control with a
   watchdog, so it ends by itself if OpenAmpere stops.
4. **Data folder:** credentials are encrypted with AES-256-GCM; the key lives in a separate file. Backups contain no
   credentials, and a restored backup keeps the current password, sessions, tokens and secrets.
5. **App → updater:** the app cannot reach Docker. It can only write a request file; the updater decides what to pull
   and verifies the image before starting it, and rolls back if the new version does not become healthy.
6. **Release pipeline → users:** images and release files carry Sigstore-signed build provenance from the release
   workflow; dependencies are pinned by hash, actions by commit.

## Secure design principles

| Principle | How it is applied |
|---|---|
| Economy of mechanism | One local password, one session cookie, one security middleware for all requests; no user management, no cloud. |
| Fail-safe defaults | Control features are off and start in test mode; without a password all changes are refused; unknown host names are rejected; SAJ control stays locked until verified on real devices; when in doubt a feature stays read-only. |
| Complete mediation | All `/api/` requests go through the same middleware (host, origin, CSRF header, session or token); control writes go through one code path with limit checks. |
| Open design | All code, register maps and protocols are public; security rests on the password, tokens and keys only. |
| Separation of privilege | Control needs the master switch *and* leaving test mode *and* a login; tokens for other apps are separate from the web session and scoped; the updater with Docker access is a separate container that only reacts to a request file. |
| Least privilege | The app runs as a non-root user without access to Docker; tokens can be limited to `read`; workflows run with read-only tokens unless a job needs more. |
| Least common mechanism | Sessions, app tokens and stored secrets are separate; the external API does not accept cookies. |
| Psychological acceptability | Reading works without login in the home network; German messages that say what to do; one-tap updates so users stay current. |
| Limited attack surface | No API documentation endpoints (`/docs`, `/openapi.json` are off), the HTTPS port can be turned off, no inbound connections from the internet. |
| Input validation with allowlists | Typed request models with ranges and patterns, settings validated against a table of allowed keys and values, limits checked before any write. |

## Writing to the inverter

- Only through the control features, which respect the master switch (`control.enabled`) and test mode
  (`control.dry_run`) and the device's `supports_control`.
- Values are checked against the device limits before writing; the order of several writes keeps every intermediate
  state valid (covered by property-based tests).
- Every write is read back, and every change is recorded in the control log.
- The export limit cannot be raised above what the selected rule allows; raising a limit set by the grid operator needs
  the operator's reference number.
- Registers need a documented source or a report from a real device.

## Common weaknesses and how they are countered

Based on the OWASP Top 10 (2021) and the CWE Top 25.

| Weakness | Countermeasure |
|---|---|
| Broken access control (A01, CWE-862/863) | Central middleware; changes and sensitive reads need a session; tests in `tests/test_security.py`. |
| Cross-site request forgery (CWE-352) | Custom header that browsers cannot send cross-site without preflight, `Origin` check, `SameSite=Strict` cookie. |
| DNS rebinding | Host allowlist (IP addresses, home network suffixes, configurable names). |
| Cryptographic failures (A02, CWE-327/330) | AES-256-GCM with random nonces, scrypt for the password, `secrets`/`os.urandom` for tokens and keys, TLS via Python's `ssl` defaults; outgoing HTTPS verifies certificates. |
| Injection (A03, CWE-89/78) | Parameterized SQL only (the two formatted statements in migrations use fixed names); no shell calls; `yaml.safe_load`. |
| Cross-site scripting (CWE-79) | React escapes all output; no `dangerouslySetInnerHTML`. |
| Insecure design (A04) | Safety rules for writes above; legal topics settled in an issue before code. |
| Security misconfiguration (A05) | Secure defaults; no debug endpoints; warnings in the app and the guides against port forwarding. |
| Vulnerable and outdated components (A06) | Dependabot alerts and updates, weekly lockfile upgrades, OpenSSF Scorecard. |
| Authentication failures (A07, CWE-307) | Lockout after 5 failed logins, minimum password length, sessions expire, password stored as scrypt hash. |
| Software and data integrity failures (A08, CWE-494) | Hash-pinned dependencies and actions, signed build provenance, the updater verifies images, backups are checked before restoring. |
| Logging and monitoring failures (A09) | Control log of every write; diagnostics reports pseudonymise addresses and serial numbers; credentials are never returned by the API. |
| Server-side request forgery (A10, CWE-918) | Outgoing addresses (Shelly, evcc, my-PV) can only be set by a logged-in user. |
| Path traversal (CWE-22) | Storage paths are validated; uploads are size-limited and checked before use. |
| Unrestricted upload / resource exhaustion (CWE-400/434) | Size limits for imports and backups; a restored database is checked with SQLite before it is used. |

## Verification

- Tests for the security middleware, the control checks and the external API run on every pull request
  ([ci.yml](../.github/workflows/ci.yml)); property-based tests check register decoding and limit writes.
- CodeQL analyses Python, TypeScript and the workflows on every pull request.
- The OpenSSF Scorecard checks the repository and release practices weekly.

## Residual risks

- Plain HTTP in the home network: someone listening there can read the session cookie and the password at login.
  Mitigation: VPN with HTTPS for remote access; the home network is assumed to be trusted.
- The first password can be set by anyone in the home network until the owner sets it.
- Someone with access to the server or the `data/` folder can read the credentials.
- Modbus TCP has no authentication; this is a property of the devices.
