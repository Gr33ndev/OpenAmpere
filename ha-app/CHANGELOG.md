# Changelog

<!-- written by scripts/changelog.py from web/public/changelog.json, do not edit -->

All versions: https://github.com/Gr33ndev/OpenAmpere/releases

## 0.27.0 (2026-10-14)

### Behoben

- **release:** List only the advisory a fix closes, not one it mentions (#371)
- **restore:** Keep the current password and app accesses when a backup is restored (#367)
- **updates:** Keep the health check working while the database is busy and harden the update helper (#361)
- **auth:** Harden login, sessions, app access and pairing (#366)
- **status:** Hide database file names and times without login, refuse credentials in the evcc address (#369)
- **security:** Let the web app run only its own scripts (#368)
- **install:** Keep the tailscale login link and evcc data private and check helpers against the release (#364)
- **control:** Keep grid charging and surplus control within safe limits (#363)
- Never send the cloud api key on after a redirect and limit online requests in size and time (#362)
- **security:** Home Assistant integration does not check the server when pairing (GHSA-8f28-gpm6-w56g)
- **security:** Going back to an older version restores old passwords, sessions and revoked tokens (GHSA-x8rj-ccv8-qvmp)
- **security:** Dependency update workflow can run code from an updated package with a write token (GHSA-mh4w-fmr4-2wpw)
- **security:** Update helper and installer accept an older release (GHSA-8893-x8jf-64v6)
- **security:** Release workflow signs files from any tag when started by hand (GHSA-3xmc-fc67-chgq)
- **security:** Manual Docker Compose layout lets the root Tailscale helper follow a link in the data folder (GHSA-3grw-j32m-8gcj)
- **security:** Website build can replace the install script that owners run with sudo (GHSA-g83m-rrj2-cm2j)

### Weitere Änderungen

- Correct security notes on backups, home assistant control and relays (#365)
- Check website texts for unsafe html and pin the release helper images (#360)

## 0.26.4 (2026-10-13)

### Behoben

- Calculate consumption from the other energy values, not the inverter's load counter (#349)

### Weitere Änderungen

- Bring the issue templates in line with the app (#348)

## 0.26.3 (2026-10-13)

### Behoben

- **homeassistant:** Show live values when home assistant is opened by a remote address (#345)

## 0.26.2 (2026-10-13)

### Behoben

- **homeassistant:** Show a battery setting change right away (#343)

## 0.26.1 (2026-10-13)

### Behoben

- **diagnostics:** Leave raw modbus frames out of the report log (#341)
- **homeassistant:** Keep the entities when the inverter answers slowly (#340)

### Weitere Änderungen

- **saj:** Record the battery limits confirmed on a device (#337)

## 0.26.0 (2026-10-13)

### Behoben

- **security:** evcc password and ntfy token from config.yaml or the environment are sent to a changed address (GHSA-8mjv-f2x6-2v3h)
- **security:** A damaged database resets a protected installation to setup without a password (GHSA-4pjj-g988-r5vc)
- **security:** A slowly answering switched device delays switching off the other devices (GHSA-wxgh-j9mh-32j6)
- **security:** A login with the old password can stay valid after the password is changed (GHSA-fqfx-94pj-6jhh)
- **security:** Error text of a failed switch command shows the device password without login (GHSA-7whx-cq9r-q56g)
- **security:** Credentials in the ntfy address appear in the diagnostics report (GHSA-9fpp-m6jp-5vw4)
- **security:** Cloud export tool writes files outside its folder when the cloud sends a crafted installation id (GHSA-8qjf-v7mq-g2r5)
- **security:** Importing a crafted ZIP file can use far more memory than the limits allow (GHSA-963v-6j22-r7x5)
- **security:** Power curve of the PV inputs loads the readings of a whole year without login (GHSA-p7r4-hj2r-hg5h)

## 0.25.1 (2026-10-13)

### Behoben

- **saj:** Show the on-grid and off-grid battery reserve the right way round (#335)

## 0.25.0 (2026-10-13)

### Neu

- **wallbox:** Listen to a wallbox over ocpp for the diagnostics report (#333)
- **control:** Copy the control log for a report without personal data (#331)
- **wallbox:** Check a foxess wallbox read-only in the setup (#327)
- **diagnostics:** Read any modbus device in the home network, read-only (#326)

### Behoben

- **homeassistant:** Report an alarm only for fault codes other than 0 (#332)

## 0.24.0 (2026-10-12)

### Neu

- **evcc:** Explain the sponsor token and warn before it runs out (#319)

### Behoben

- **charging:** Explain in the log why grid charging ended and what changed (#323)

### Weitere Änderungen

- **charging:** Stop raising the backup reserve for grid charging (#321)

## 0.23.1 (2026-10-12)

### Behoben

- **charging:** Charge past the backup reserve on inverters that stop there (#316)
