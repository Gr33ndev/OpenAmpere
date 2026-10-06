# OpenAmpere

**Deutsch:** [README.de.md](README.de.md)

[Website & Demo](https://gr33ndev.github.io/OpenAmpere/) · [FAQ](https://gr33ndev.github.io/OpenAmpere/faq.html) · [Source code](https://github.com/Gr33ndev/OpenAmpere) · [Report a bug](https://github.com/Gr33ndev/OpenAmpere/issues) · [License](LICENSE) · [Third-party licenses](THIRD_PARTY_LICENSES.md) · [Security](SECURITY.md) · [Contributing](CONTRIBUTING.md) · [Code of Conduct](CODE_OF_CONDUCT.md) · [Governance](GOVERNANCE.md)

**Local app for solar systems with battery storage, no cloud at all.**

OpenAmpere talks to the inverter directly on your home network, stores all data locally and shows it in the browser. On a smartphone you can add the page to the home screen, and it then behaves like an app. No account, no cloud, no dependence on a manufacturer's server.

The app's user interface and the website are in German.

<p align="center">
  <img src="docs/screenshots/overview.png" width="190" alt="Übersicht mit Energiefluss von Solar, Speicher, Netz, Wallbox und Heizstab">
  <img src="docs/screenshots/devices.png" width="190" alt="Geräte: Speicher und Wallbox bedienen">
  <img src="docs/screenshots/report.png" width="190" alt="Auswertung mit Tageswerten und Leistungskurve">
  <img src="docs/screenshots/overview-dark.png" width="190" alt="Übersicht im dunklen Design">
</p>
<p align="center"><sub>From the <a href="https://gr33ndev.github.io/OpenAmpere/demo/">demo</a> with made-up values. Regenerate with <code>scripts/screenshots.sh</code>.</sub></p>

> Independent community project. Background and legal notes are [at the end of this page](#background--legal-notes).

## Supported devices

| Device | Status |
|---|---|
| FoxESS H3 / H3 Smart / H3 Pro | ✅ Monitoring and control. Control is off by default. The new or old register map is detected automatically. |
| SAJ H2 / HS2 | ✅ Monitoring. Control will only be enabled once the driver has been tested on real devices. |
| Immersion heater, heat pump (SG-Ready) | ✅ Switching on solar surplus via Shelly relays or web addresses. |
| Immersion heater my-PV AC ELWA-E, AC ELWA 2, AC THOR | ✅ Continuously variable according to solar surplus, optionally with cheap grid power. |
| Wallbox | ✅ Via [evcc](https://evcc.io): monitoring and operation in OpenAmpere, see [docs/evcc.md](docs/evcc.md). |
| Home Assistant | ✅ Dedicated integration via HACS: live data and control, see [Home Assistant](#home-assistant). |

**The device type is detected automatically.** Setup tries all known devices one after another, read-only: FoxESS at device address 247, SAJ at 1 and 2. So you don't need to know the device address or the manufacturer. The connection uses Modbus TCP, default port 502. New drivers are welcome, see `src/openampere/drivers/registry.py`.

## Features

- **Menu:** **Übersicht** (Overview), **Geräte** (Devices: operate the battery, wallbox and immersion heater, and set the order in which they get solar power), **Auswertung** (Report) and **Mehr** (More: setup and settings). A switch **„Nur ansehen / Testen / Aktiv“** (View only / Test / Active) determines whether OpenAmpere is allowed to change anything.
- **Overview:**
  - Live energy flow for PV, house, grid and battery with state of charge, plus wallbox and immersion heater
  - Daily totals
  - Self-sufficiency
  - **Solar by module array:** power, voltage and current per PV input (MPPT), e.g. for the south roof, west roof or garage
  - **Temperatures:** inverter, battery, battery cells
- **Report:**
  - Power curve over the day with state of charge, values on tap
  - Energy per day (15 or 60 minutes), week, month and year
  - Yield per module array
  - Temperature history
  - Consumption per device (wallbox, immersion heater) and charging sessions
  - Self-sufficiency, self-consumption, consumption split between household and devices, estimated savings
- **Electricity tariffs:** fixed price or dynamic tariff (exchange price plus markup, Germany and Austria) with standing charge, multiple tariffs with start dates; from these, savings and the net electricity cost.
- **Keep track of advance payments:** enter monthly advance payments for grid consumption and feed-in (**Mehr → Abschläge** (More → Advance payments)). The report shows where you stand today: costs and feed-in compensation so far against the advance payments up to today, with the current month pro rata.
- **Meter readings from the grid operator:** with a smart meter, OpenAmpere fetches the daily values from the grid operator's customer portal (**Mehr → Verbindung → Zählerwerte** (More → Connection → Meter readings)) and uses them to calculate the advance payments. Currently for Netze BW; other grid operators can be added as separate modules.
- **Export:** energy values as a CSV file for Excel and similar tools.
- **Battery & backup power:** backup power reserve, charge limits, operating mode
- **Feed-in limit:** view and change the maximum feed-in power. You enter the installed module power (kWp) and the applicable rule: 60 % under the Solarspitzengesetz (German solar peak law; until a smart metering system with a control box is installed), the former 70 % rule, a fixed value from the grid connection approval (e.g. zero feed-in) or no limit. The percentages refer to the module power, not the inverter; the app does not allow more than the rule permits. **With a fixed value from the grid operator, any increase is only permitted with the grid operator's written consent**; the app requires a confirmation for this and logs the date and reference. "No limit" requires an explicit declaration, which is also logged.
- **Import history from the EKD cloud:** for previous users of the "Ampere.IQ" app. Under **Mehr → Daten & Sicherung** (More → Data & backup), enter the API key from the Ampere.IQ app and start the import. The import only uses the public customer API with your own key; OpenAmpere has nothing to do with EKD, see [legal notes](#background--legal-notes).
  - It runs in the background with at most one request per minute.
  - After a restart, it continues where it left off.
  - Alternatively, a ZIP from the [export tool](tools/cloud-export/) can be imported.
- **Cloud-compatible interface:** `/api/v1/customer/installation` and `/api/v1/installation/{id}/now/all/power` respond like the previous cloud customer API. Existing tools only need to be pointed at the new address.
- **Charging from the grid (experimental):** at the cheapest exchange price or in a fixed time window, up to a charge target. Only works via the inverter's remote control with a time limit; in test mode it is only logged. Legal notes must be confirmed first (EEG storage, § 14a EnWG).
- **Use surplus:** a my-PV immersion heater (AC ELWA-E, AC ELWA 2, AC THOR) follows the solar surplus continuously, with minimum surplus, maximum power, battery priority and optionally cheap grid power. OpenAmpere switches a heat pump (SG-Ready) or other devices via Shelly relays or web addresses, by priority, with minimum run time and minimum pause time.
- **Battery health** (**Mehr → Meine Anlage** (More → My system)): full cycles, efficiency since commissioning, temperature of the warmest and coolest cell and their difference, with a warning for unusual values.
- **Firmware changes:** OpenAmpere remembers the inverter's firmware and shows when it has changed. An update can change registers; afterwards it's best to run the diagnostics once.
- **Notifications** via ntfy: inverter unreachable, fault, overwritten setting, battery full, cheapest power tomorrow, check battery, new firmware.
- **Diagnostics (read-only):** checks the register map, function codes, optional blocks, scaling, feed-in limit, connection drops and daily counters of your own device, and creates a report to share. **Run it once before the first change to the inverter.**
- **Wallbox with evcc:** wallboxes are controlled by the independent open-source project [evcc](https://evcc.io). OpenAmpere supplies evcc with the grid, solar and battery readings, so evcc doesn't need its own connection to the inverter, and shows the charge points in the app: charging mode, charge target, minimum charge, charging plan, time until target and charging sessions. If the car itself is set up in evcc, state of charge and range are added, and the report shows kilometres driven, kilometres on solar power, consumption per 100 km and cost per 100 km compared with grid power. Whether the wallbox or the immersion heater gets surplus first is configurable. Setup: [docs/evcc.md](docs/evcc.md). Thanks to the evcc community!
- See also [docs/architecture.md](docs/architecture.md).

## Home Assistant

The **OpenAmpere** integration brings power, energy (suitable for the Energy dashboard), state of charge and status live
into Home Assistant, and optionally also control of the battery, charging from the grid and the immersion heater.

> **Install OpenAmpere first** (see [Installation](#installation)), then the integration. It only connects
> to the running OpenAmpere and does not replace it. Only OpenAmpere talks to the inverter.

[![HACS Custom](https://img.shields.io/badge/HACS-custom-orange.svg?style=for-the-badge&logo=homeassistantcommunitystore&logoColor=ccc)](https://hacs.xyz)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Gr33ndev&repository=OpenAmpere&category=integration)
[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=openampere)

Then add the OpenAmpere integration in Home Assistant and **pair** it: enter the address of OpenAmpere, compare the
6-digit code with the one under **Mehr → Verbundene Apps** (More → Connected apps) and allow it there. The connection is
encrypted, and each app gets its own revocable access. Only what is enabled in OpenAmpere can be controlled.
Instructions and details: [docs/homeassistant.md](docs/homeassistant.md).

## Installation

**Requirements:** a Linux computer on the same network as the inverter (Raspberry Pi with a 64-bit system, NAS, Proxmox …) and Modbus TCP enabled on the inverter. For the battery systems sold by EKD this is already the case; for other devices the installer can take care of it.

**1. Install:** run in a terminal on the computer:

```bash
curl -fsSL https://gr33ndev.github.io/OpenAmpere/install.sh | bash
```

The script installs Docker if needed and asks for the folder (default `/opt/openampere`), whether it should also set up evcc for a wallbox, and whether you want to use OpenAmpere on the go as well (Tailscale, see [Remote access](#remote-access)). When run again, the answers are kept. It detects the time zone and a free port by itself. At the end it shows the address of the app. What it does is in [scripts/install.sh](scripts/install.sh).

**Updates:** when a new version is available, the app shows a notice at the top; a tap on **Aktualisieren** (Update) is all it takes. Under **Mehr → Über OpenAmpere** (More → About OpenAmpere), updates can also be installed automatically at night. For this, the install script sets up a small helper container ([scripts/updater.sh](scripts/updater.sh)): it has access to Docker, but only reacts to a request file that the app writes into the data folder. It then pulls the new version, starts it and checks that it is running. If it doesn't start, the previous version is restored. The app itself gets no access to Docker. The app checks GitHub for new versions every 6 hours; this can be turned off. If you installed with the script before, run it once more so the helper is added.

**2. Set up in the browser:** open the displayed address, usually `http://<server-ip>:8080`, and set a **password**. A **setup wizard** searches for the inverter on the home network, or you enter its IP address. It tests the connection and saves it. Values can be viewed on the home network without a password. Changing settings, control commands and data backup require a login.

**3. Optional: immersion heater or heat pump.** No additional software is needed. Add it in the app under **Mehr → Verbindung → Heizstab und weitere Geräte** (More → Connection → Immersion heater and other devices).

**4. Optional: wallbox.** The wallbox is controlled by [evcc](https://evcc.io), a separate open-source project. If you answered yes to "Wallbox" in the script, evcc is already running as a second container at `http://<server-ip>:7070`, and OpenAmpere knows its address. Then:
1. Set up the wallbox and vehicle in evcc.
2. In OpenAmpere, under **Mehr → Verbindung → Wallbox** (More → Connection → Wallbox), copy the meter configuration and paste it into evcc. This way evcc gets the grid, solar and battery readings from OpenAmpere.

If evcc is already running elsewhere, enter its address under Mehr → Verbindung → Wallbox. Details are in [docs/evcc.md](docs/evcc.md).

**Where to find what:** under **Geräte** you operate the battery (backup power reserve, charge limits), wallbox and immersion heater, and set who gets solar power first. Under **Mehr** you'll find **Meine Anlage** (with the feed-in limit), **Stromtarif** (Electricity tariff), **Verbindung**, **Steuerung und Protokoll** (Control and log), **Benachrichtigungen** (Notifications), **Zugriffsschutz** (Access protection), **Darstellung** (Appearance), **Daten & Sicherung** and **Diagnose** (Diagnostics).

Forgot your password? On the server, run `docker compose exec openampere openampere reset-password` in the installation folder and then set a new one in the app.

### Manual installation

If you want to build it yourself or contribute to development, use the repository instead of the script:

```bash
git clone https://github.com/Gr33ndev/OpenAmpere.git openampere && cd openampere
mkdir -p data && sudo chown 1000:1000 data
docker compose up -d --build
```

The app runs in the container as user 1000 and needs write access to `data/`. Besides the database, that folder contains `secret.key`, the key for the stored credentials. Without it, they have to be entered again in the app. evcc for a wallbox is available in `docker-compose.yml` as a commented-out service, see [docs/evcc.md](docs/evcc.md).

### Remote access

OpenAmpere is built for the home network. The safest way to reach it on the go is via a **VPN**.

**Easiest with Tailscale:** answer yes to the question „von unterwegs nutzen?“ (use on the go?) in the install script, then in the app tap **Einrichten** (Set up) under **Mehr → Zugriff von unterwegs** (More → Remote access) and sign in to Tailscale (free account, e.g. with Google, Apple or Microsoft). On your phone, sign in to the Tailscale app with the same account and open the address that OpenAmpere shows.
- If you have already installed, run the script once more: `curl -fsSL https://gr33ndev.github.io/OpenAmpere/install.sh | OPENAMPERE_TAILSCALE=ja bash`. With `OPENAMPERE_TAILSCALE=nein` it is removed again.
- Tailscale runs as its own container in userspace mode, without additional privileges. As with the update helper, the app gets no access to it; it only drops a request ([scripts/tailscale.sh](scripts/tailscale.sh)). Sending log data to Tailscale is disabled, and Funnel (public access from the internet) is not used.
- Tailscale is a service of Tailscale Inc. (USA). It brokers the connection; the data travels encrypted directly between the devices.

**Without a third party:** the FRITZ!Box VPN (WireGuard) or your own WireGuard server. Then open the app via the server's IP address, just like at home.

**Never expose it directly to the internet via port forwarding.** Anyone who reaches the app that way can control the inverter as soon as the password is cracked or intercepted (no HTTPS).

Custom hostnames (e.g. a reverse proxy on the home network) must be added under `server.allowed_hosts`; IP addresses, `localhost`, `*.local`, `*.fritz.box`, `*.home.arpa` and Tailscale names (`*.ts.net`) work without an entry.

For an automated installation, all values can additionally be preset via `data/config.yaml` (see `config.example.yaml`) or via `OPENAMPERE_…` environment variables. Environment variables take precedence and appear in the app as „fest eingestellt“ (fixed).

### Running behind a Modbus proxy

If another energy manager is also connected to the inverter (e.g. the previous Smartbox), the Modbus access is often shared via a **Modbus TCP proxy**. OpenAmpere handles this:
- In the app, under **Mehr → Verbindung**, enter the proxy's address, **port** and **device address**. The network search uses the port set there.
- If the proxy briefly reports that the inverter is not responding (Modbus error 10/11), or a response arrives too late, OpenAmpere retries the request. Only after several consecutive failures is the connection considered lost. No registers are wrongly marked as invalid in the process.
- For slow proxies or Wi-Fi, increase the **timeout per request** (**Mehr → Verbindung → Erweitert** (More → Connection → Advanced)).
- A read-only proxy rejects control commands. OpenAmpere shows this as an understandable message.

**Important:** the inverter only allows a few simultaneous Modbus connections. If another energy manager (e.g. a previous Smartbox) or another integration is also talking to it, connection drops can occur.

### The previous Smartbox resets settings

A previous Smartbox fetches its target settings from its manufacturer's cloud roughly every 100 seconds and writes them to the inverter. In doing so, it overwrites changes made by OpenAmpere, for example to the backup power reserve, operating mode or charging from the grid. OpenAmpere detects this, shows a notice and can send a notification „Einstellung überschrieben“ (Setting overwritten).

Remedies:
- **Block the Smartbox's internet access**, e.g. on the FRITZ!Box under Internet → Filter → Kindersicherung (Internet → Filters → Parental Controls), access profile „gesperrt“ (blocked). One user successfully switched to price-based charging and changed the backup power reserve this way. The Smartbox then keeps reading along, but no longer receives target values or updates, and the manufacturer's app no longer shows current data.
- **Disconnect the Smartbox** if it is no longer needed. Check beforehand whether it is required for anything else, such as control by the grid operator.

## Development without a real system

OpenAmpere includes a simulator for the FoxESS H3.

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
```

```bash
.venv/bin/python -m openampere.simulator --port 5020 --speed 20
```

```bash
OPENAMPERE_INVERTER_HOST=127.0.0.1 OPENAMPERE_INVERTER_PORT=5020 OPENAMPERE_SERVER_PORT=8089 .venv/bin/python -m openampere
```

```bash
.venv/bin/python -m openampere.demo_data --db data/openampere.db --days 60
```

The last command is optional and generates demo history data.

Web app with live reload; it forwards the API to port 8089:

```bash
cd web && npm install && npm run dev
```

Tests:

```bash
.venv/bin/pytest
```

The Python dependencies are pinned in `requirements.lock` (Docker image, with hashes) and `requirements-dev.lock` (development, CI). Both are generated with [uv](https://docs.astral.sh/uv/) from `pyproject.toml`:

```bash
scripts/deps.sh
```

This also generates the license list (`THIRD_PARTY_LICENSES.md` and the list in the app); everything belongs in the same commit. If something is missing, e.g. in Dependabot PRs, the `dependencies.yml` workflow regenerates the files and commits them to the same branch. Once a week it upgrades all Python dependencies to the latest allowed versions and opens a pull request for this from the `deps/python-updates` branch. CI on GitHub checks the tests, web build, Docker build and whether the license list is up to date.

Project site with demo: the landing page lives in `site/`; the demo is the regular web app, built with `VITE_DEMO=1`. It simulates a system in the browser (`web/src/demo/`) and sends nothing to a server. Build and view locally:

```bash
scripts/build-site.sh
```

```bash
python3 -m http.server 8090 -d _site
```

The `pages.yml` workflow publishes the site to GitHub Pages (once, under Settings → Pages, select "GitHub Actions" as the source). A version tag (`v…`) publishes the image for x86 and ARM under `ghcr.io`.

## Safety of control functions

Everything that writes to the inverter is **off** by default. It is enabled in the app under **Mehr → Steuerung** (More → Control), with a safety confirmation:
- After enabling, control first runs in **test mode**. Changes are then only logged.
- Only once you explicitly end test mode are values sent to the inverter.
- Every change is recorded in the log with its old and new value, and is read back from the device after writing.

Settings such as charge limits, operating mode or feed-in limit are stored by the inverter itself. They remain active even if OpenAmpere is not running or is uninstalled. To undo something, you have to change it again in the app (or have the installer do it).

Other apps such as [Home Assistant](docs/homeassistant.md) can only control via their own access with the permission
„Lesen + Steuern“ (Read + control), and only while control is enabled. They cannot change the main switch, test mode or the
feed-in limit, and can change battery settings at most six times per hour.

The registers used for writing come from community documentation and have not yet been verified on every device variant. That is why OpenAmpere reads back every written value and reports deviations.

## Background & legal notes

**How OpenAmpere came about:**
- Energiekonzepte Deutschland GmbH (EKD) and other companies of the group filed for insolvency in early October 2026. The proceedings are being conducted at the Local Court (Amtsgericht) of Leipzig; the official announcements are available at [insolvenzbekanntmachungen.de](https://neu.insolvenzbekanntmachungen.de/ap/suche.jsf) (court: Leipzig, enter the case number):
  - Energiekonzepte Deutschland GmbH: 401 IN 2082/26
  - AMPERE German Electric Innovation GmbH: 401 IN 2085/26
  - EKD Montage GmbH: 401 IN 2100/26
  - Energiekonzepte Deutschland Holding GmbH: 401 IN 2101/26
  - Energiekonzepte Deutschland Investorenholding GmbH: 401 IN 2107/26
  - Energiekonzepte Deutschland PV-Montage GmbH: 401 IN 2110/26
  - ES Energiesysteme GmbH: 401 IN 2111/26
- This means that all seven companies which, according to [Handelsblatt](https://www.handelsblatt.com/unternehmen/energie/solarenergie-solarspezialist-energiekonzepte-deutschland-meldet-insolvenz-an/100258988.html), are said to be affected have now been officially published (as of 6 October 2026).
- According to EKD, business operations continue without restriction, and customer service remains the point of contact for customers ([pv magazine](https://www.pv-magazine.de/2026/10/02/energiekonzepte-deutschland-stellt-insolvenzantrag/)).
- The "Ampere.IQ" app only works via EKD's servers. What will happen to it is unclear. Should the insolvency proceedings lead to these servers being shut down, OpenAmpere simply keeps running: it talks to the inverter directly on the home network and does not need any EKD server.
- OpenAmpere was created as a local alternative by affected owners for affected owners.

**Why it works with EKD systems:** OpenAmpere talks directly to the installed inverters. The battery systems sold by EKD as "Ampere.StoragePro E3" are based on the FoxESS H3 series, the older "Ampere.StoragePro" on SAJ H2/HS2. OpenAmpere works just the same with these devices from other sources.

**No affiliation with EKD or the manufacturers:** OpenAmpere is an independent, unofficial community project. It has no affiliation whatsoever with Energiekonzepte Deutschland GmbH, its insolvency administration, FoxESS, SAJ or Kiwigrid. It was neither commissioned, authorized nor supported by them.

**Trademarks:** "AMPERE", "Ampere.IQ", "Ampere.StoragePro" and all other product and company names mentioned are trademarks or designations of their respective owners. All rights to them naturally remain with those owners. They are mentioned here solely to describe which devices OpenAmpere works with. The project name refers to the ampere, the physical unit.

**No third-party code:** OpenAmpere contains no code, graphics or text from the Ampere.IQ app. The cloud import exclusively uses the customer API that EKD documented for customers at developer.ekd-solar.de, with the personal key from the respective user's app.

**Use at your own risk:** OpenAmpere is a free community project without any warranty. It does not replace a qualified electrician. Control functions are switched off by default. Anyone who changes settings on the inverter, in particular the feed-in limit, is personally responsible for complying with the grid connection conditions. Unsuitable settings can put strain on the battery and jeopardize guarantee or warranty claims against the manufacturer, dealer or insolvency administrator. Note down the previous values before you change anything. The notes in the app are not legal advice.

## Contributing

Bug reports, device diagnostics and ideas are welcome – even without programming skills. First an issue, then the pull request; details in [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE). Notes on the sources and trademarks used are in [NOTICE](NOTICE), and the licenses of all bundled open-source components are in [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md). This file is generated with `scripts/third_party_licenses.py`. Please regenerate it when dependencies change.
