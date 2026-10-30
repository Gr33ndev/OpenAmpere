# Changelog

<!-- written by scripts/changelog.py from web/public/changelog.json, do not edit -->

All versions: https://github.com/Gr33ndev/OpenAmpere/releases

## 0.36.2 (2026-10-30)

### Behoben

- **wallbox:** Describe real control in the FoxESS charger's limit, start and setup texts (#506)
- **wallbox:** Offer the FoxESS charger as controllable as a test in the setup (#504)

## 0.36.1 (2026-10-29)

### Behoben

- **saj:** Change the export limit only with a rated power that fits the type (#502)
- **saj:** Show the owner's work mode right after a restart while charging (#501)
- **saj:** Keep a work mode chosen right after a restart while charging still runs (#499)
- **saj:** Keep a work mode chosen while grid charging across a restart (#495)
- **saj:** Switch off OpenAmpere's charge slot also when the inverter's answer timed out (#494)

### Weitere Änderungen

- **agents:** A pull request is done when the ai review finds nothing more (#496)

## 0.36.0 (2026-10-29)

### Behoben

- **release:** List advisory fixes merged without their id, and write down the advisory steps (#488)
- **wallbox:** Say which limit keeps the wallbox from charging and drop an old note (#483)
- **wallbox:** Do not charge when the lowest step is above the owner's highest current (#482)
- **security:** OCPP listener accepts connections from web pages and puts their data into the diagnostics report (GHSA-q2mp-55hm-346m)
- **security:** Prompt injection in the AI review workflow can overwrite a script that runs with the workflow's tokens (GHSA-mwqm-mf75-9jhh)
- **security:** Restoring a backup takes over wallbox ownership and real control without the checks of the app (GHSA-wx8q-mh5w-82jr)
- **security:** A device in the home network can move a stored dé wallbox to its own address (GHSA-q7r4-hqhv-322x)
- **security:** Device query and wallbox check put host names and the device's page title into the report unmasked (GHSA-jpg8-f7vx-4g36)
- **security:** Website build lets a javascript: link in a translation pass the check for unsafe links (GHSA-w7hp-wv8j-xff9)

### Weitere Änderungen

- **ai-review:** Trust nothing in the checkout after the review (#484)

## 0.35.0 (2026-10-29)

### Neu

- **wallbox:** Switch the foxess charger between one and three phases (#479)
- **import:** Say that the state of charge of old days comes in step 2 (#477)
- **wallbox:** Control the foxess ac ev charger for real once its owner agrees (#472)
- **wallbox:** Set the work mode of a foxess charger to unlock it (#470)

### Behoben

- **web:** Explain a live connection that fails behind a reverse proxy (#474)
- **report:** Show the imported state of charge in the power day view (#473)
- **wallbox:** Say when the evcc sponsor token has expired (#466)

### Weitere Änderungen

- Check whether the grid operator needs the old smartbox (#475)
- Mark saj reading as confirmed on the 3p-x e2 (#471)
- **foxess:** Add the write registers of the ac ev charger (#467)
- **deps:** Update Python dependencies (#461)
- **deps-web:** Bump react-dom from 19.3.0 to 19.3.1 in /web (#458)

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
