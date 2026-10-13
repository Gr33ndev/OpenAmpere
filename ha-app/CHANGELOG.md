# Changelog

<!-- written by scripts/changelog.py from web/public/changelog.json, do not edit -->

All versions: https://github.com/Gr33ndev/OpenAmpere/releases

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

## 0.23.0 (2026-10-12)

### Neu

- **battery:** Allow a backup power reserve below 10 % after a warning (#314)
- **connection:** Say when the inverter's connections are all taken (#312)

### Weitere Änderungen

- **site:** Steps to add the app repository by hand for way 2 (#310)
- Complete steps to add the app repository, with its maintainer (#308)

## 0.22.0 (2026-10-12)

### Neu

- The control log names the user of Home Assistant (#306)
- OpenAmpere as a Home Assistant app for Home Assistant OS (#304)

### Weitere Änderungen

- **deps:** Update Python dependencies (#301)
- **ui:** Check the retro charts with a full week (#303)

## 0.21.0 (2026-10-11)

### Neu

- **changelog:** Link security advisories (#278)

### Behoben

- **site:** Use own buttons instead of images from my.home-assistant.io (#296)
- **security:** Tailscale option makes services that listen only on localhost reachable from the tailnet (GHSA-6r67-5vwf-5x8x)
- **install:** Run the app without capabilities and new privileges (#294)
- **pairing:** Limit pairing requests per device and show where they come from (#292)
- **diagnostics:** No time in the report header, pseudonymise meters and devices (#290)
- Refuse values that are no numbers with a sentence, keep bad cloud data out (#288)
- **auth:** Stronger password hashing, 8 characters, secure cookies over HTTPS (#286)
- **install:** Pin the update helper image by digest (#284)
- **homeassistant:** Leave names, model and firmware out of the diagnostics (#282)
- **foxess:** Write settings only to a device confirmed as FoxESS (#280)
- **auth:** Issue a new device cookie on every login (#276)

### Weitere Änderungen

- Use an obviously made-up meter number in the diagnostics test (#299)
- **agents:** Note that switching off is written in test mode too (#298)

## 0.20.0 (2026-10-11)

### Behoben

- **release:** List fixes merged from security advisories in the changelog (#274)
- **security:** Database and its copies are readable by other users of the host (GHSA-p5pf-45jm-94c3)
- **security:** Restoring a crafted backup takes over unchecked device, charging and update data (GHSA-4jmw-6h58-vc78)
- **security:** Concurrent first setup can silently replace the owner's new password (GHSA-m3qh-j9xv-jf9m)
- **security:** Full inverter serial number readable without login (GHSA-vcc7-57gx-gcgr)
- **security:** Wallbox charging sessions readable without login (GHSA-33vp-mf38-3mj3)
- **security:** A long Host header blocks the server (regular expression denial of service) (GHSA-vrrc-53mr-hc3c)
- **security:** Large request bodies without login can exhaust the memory (GHSA-gfr8-g665-933w)
- **security:** Home Assistant setup dialog shows unverified server text as Markdown (GHSA-vx3h-6qm5-fqr2)
- **security:** Exported control log can contain spreadsheet formulas (GHSA-9qfr-fvc7-hm9g)
- **security:** Backup download link stays valid after logout and can be used more than once (GHSA-f9rp-mp8c-m936)
- **security:** Switch addresses with device passwords are stored unencrypted and included in backups (GHSA-3jrg-5v7g-9f96)
- **security:** Changing the evcc or ntfy address sends the stored password or token to the new address (GHSA-pgj6-8cf2-3qxp)
- **security:** Client address used for login limits can be spoofed with X-Forwarded-For (GHSA-7qj3-8hhh-r724)
- **security:** Anyone on the home network can lock the owner out of the login (GHSA-cp8v-wgf4-r8cj)
- **security:** Root helper containers follow symbolic links in the app's data folder (GHSA-2j3r-6fcx-9m7v)
- **security:** Restoring a crafted backup can plant database triggers that keep an attacker logged in (GHSA-q3c2-xqgm-w8jw)
- **charging:** Run one grid charging tick or stop at a time (#272)
- Refuse redirects with credentials, limit and check outgoing requests (#270)
- **api:** Send security headers with every answer (#264)
- **charging:** Switch the remote control off when a command is refused (#261)
- **consumers:** Write to a my-PV heating rod only after it answered like one (#262)
- Check the language from the switch, serve web files by normalized path (#256)
- No technical error texts in the app, only known language codes in links (#255)

### Weitere Änderungen

- Stub the evcc opener in the cookie test (#273)
- **pages:** Add the install scripts in a job without npm (#268)
- **release:** Tag only the highest stable version as latest (#266)
- **deps:** Run dependency code without write access (#260)
- **changelog:** Add a summary for 0.19.0 (#253)

## 0.19.0 (2026-10-11)

Diese Version schließt drei Sicherheitslücken, bitte aktualisiere bald: Die Schaltadressen eigener Geräte sind nur noch nach der Anmeldung sichtbar, neue Versionen werden vor dem Start immer auf ihre Herkunft geprüft, und beim Wiederherstellen einer Sicherung werden nur noch geprüfte Einstellungen übernommen.

Wichtig: Den verbesserten Update-Helfer bekommt deine Installation nur, wenn du das Installationsscript noch einmal ausführst. Deine bisherigen Antworten bleiben dabei erhalten. Den Befehl findest du in der Anleitung zur Installation.

**English:** This version closes three security gaps, please update soon: the switch addresses of your own devices are only visible after logging in, new versions are always checked for their origin before they start, and restoring a backup only takes over checked settings.

Important: your installation only gets the improved update helper if you run the install script again. Your previous answers are kept. The command is in the installation guide.

### Neu

- **ui:** Extend the easter egg (#216)

### Behoben

- **auth:** Lock out per device, keep revoked tokens revoked, slimmer backups (#251)
- **backup:** Restore only settings the app may change
- **updates:** Check the provenance also when the app is not running
- **consumers:** Show switch addresses only after login
- Heating target cap in evcc, space check for cloud export uploads (#249)
- **ui:** Wallbox plan at the clock change, form states after saving (#248)
- **consumers:** Switch off devices in more situations (#247)
- **charging:** Stop at the inverter's charge limit (#246)
- **ui:** Smaller bugs in the report, devices and settings (#231)
- **api:** Check unexpected input, durations in the diagnostics report (#230)
- **install:** Reload helpers, safer downloads and folders, checked releases (#229)
- **ui:** Keep what was typed in forms (#228)
- **homeassistant:** Hide stale readings, set up again after the first reading (#227)
- **control:** Log and gate every write, no false external changes (#226)
- **charging:** Treat a normal end of charge as no fault (#225)
- **consumers:** Switch off devices that openampere switched on (#224)

### Weitere Änderungen

- Cover the state of my-PV heating rods (#241)
- Cover the error paths of the network search (#240)
- Cover the calendar periods and buckets (#239)
- Add copyright and license headers to every source file (#234)
- Document code review, the two-factor rule and first tasks (#235)
- **ui:** Add property-based tests with fast-check (#213)
- Enforce coding standards with ruff and biome (#212)
- Document roles and a roadmap for the next year (#211)
- **security:** Add an assurance case and the report handling process (#210)
- Show the openssf best practices badge in the readme (#209)
- Add property-based tests with hypothesis (#208)
- Link the private vulnerability report form in the security policy (#204)
- **deps-web:** Bump the vite group in /web with 2 updates (#202)
- Attach signed build provenance for the release files (#206)
- Pin docker base images and python installs by hash (#207)
- Add codeql analysis for python, typescript and workflows (#205)
