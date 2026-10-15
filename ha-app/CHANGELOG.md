# Changelog

<!-- written by scripts/changelog.py from web/public/changelog.json, do not edit -->

All versions: https://github.com/Gr33ndev/OpenAmpere/releases

## 0.30.0 (2026-10-15)

### Neu

- **wallbox:** One form to add or check a wallbox, with the steps to the Tuya local key (#389)
- **homeassistant:** Show and control OpenAmpere's own wallboxes (#390)
- **wallbox:** Read and control the portable dé wallbox over Tuya LAN (#387)
- **saj:** Show and set the reserve that applies in backup mode (#381)

### Behoben

- **wallbox:** Keep a wallbox whose local key cannot be read and ask for it again (#393)
- **consumers:** Keep a device whose switch addresses cannot be read and ask for them again (#394)
- **saj:** Keep charging from the grid when another device changes the mode or the slot (#383)
- **saj:** Show the three-phase type 0x005e by its model name (#379)

### Weitere Änderungen

- **wallbox:** No longer say that wallboxes are controlled only through evcc (#395)
- Collect what we know per device in docs/devices with status and source (#385)

## 0.29.0 (2026-10-14)

### Neu

- **saj:** Control battery settings, export limit and grid charging of SAJ inverters (#378)

## 0.28.0 (2026-10-14)

### Neu

- **homeassistant:** Show the inverter firmware as a sensor that follows an update (#376)
- **control:** Explain in the app when another device sets a change back (#374)

### Weitere Änderungen

- Reload in the overwrite test only once no request is running (#375)

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
