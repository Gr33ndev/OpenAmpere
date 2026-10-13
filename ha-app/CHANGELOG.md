# Changelog

<!-- written by scripts/changelog.py from web/public/changelog.json, do not edit -->

All versions: https://github.com/Gr33ndev/OpenAmpere/releases

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
