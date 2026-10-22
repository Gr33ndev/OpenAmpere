# Changelog

<!-- written by scripts/changelog.py from web/public/changelog.json, do not edit -->

All versions: https://github.com/Gr33ndev/OpenAmpere/releases

## 0.34.0 (2026-10-22)

### Neu

- **notify:** Tell when a car starts and ends charging at the wallbox (#453)

### Behoben

- **wallbox:** Remember openampere's stop after letting go of the wallbox (#457)
- **wallbox:** Start a wallbox again that reports complete after openampere's stop (#455)

### Weitere Änderungen

- Put the review instructions into the ai review prompt and skip bot actors (#450)
- **deps:** Update Python dependencies (#448)

## 0.33.0 (2026-10-18)

### Neu

- **cars:** Read the state of charge of vw group cars from the eu data act portal (#441)
- **wallbox:** Simulate the surplus control for the foxess ac ev charger without writing (#443)
- **wallbox:** Show the foxess ac ev charger live in the app, read only (#442)
- **security:** Set own host names for the access check in the app (#438)

### Behoben

- **wallbox:** End the simulation when manual is saved with the setup (#446)

### Weitere Änderungen

- Run the ai review with opus 5.5 (#447)

## 0.32.1 (2026-10-17)

### Behoben

- **setup:** Tell a busy wallbox apart from a busy inverter in the network search (#434)
- **wallbox:** Name the old smartbox as the usual reason the wallbox check cannot connect (#432)
- **diagnostics:** Warn that a report with the full serial number must not be posted publicly (#430)
- **wallbox:** Tell unresolvable names, refused connections and silent hosts apart (#429)

### Weitere Änderungen

- Post the ai review summary from the workflow and retry a silent run (#436)
- **deps-web:** Bump vite from 8.3.3 to 8.3.4 in /web in the vite group (#425)
- Review pull requests automatically with claude (#424)

## 0.32.0 (2026-10-16)

### Neu

- **saj:** Record the energy of a second PV inverter and show it in the report (#421)
- **saj:** Count a second PV inverter on Meter B and show it on its own (#420)
- **wallbox:** Show the FoxESS charger as the wallbox of EKD systems (#415)

### Behoben

- **evcc:** Count a second PV inverter in the PV counter for evcc (#422)
- **wallbox:** Refuse only the inverter's own port in the wallbox check (#418)
- **diagnostics:** Show an export limit above the rated power as no limit (#416)
- **wallbox:** Name the inverter's address in the wallbox check (#414)

## 0.31.0 (2026-10-16)

### Neu

- **homeassistant:** Set the highest charging current of a wallbox's mode (#410)
- **report:** Hide and show single lines with a tap on the legend (#408)
- **wallbox:** Choose the highest current per mode and keep solar charging through clouds (#407)

### Behoben

- **notify:** Report an inverter fault only after it has lasted 30 minutes (#406)
- **report:** Show every day or month of the period in the bar charts (#405)

### Weitere Änderungen

- **pages:** Let a running deployment finish instead of cancelling it (#399)

## 0.30.1 (2026-10-15)

### Behoben

- **wallbox:** Find the Tuya wallbox, type IP addresses with a dot, stop a self-started charge at once (#397)

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
