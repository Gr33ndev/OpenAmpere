Deutsch: [homeassistant.de.md](homeassistant.de.md)

# Home Assistant

With the **OpenAmpere** integration for Home Assistant you see your system's power, energy, state of charge and
status in Home Assistant, including in the Energy dashboard. If you allow it, you can also control the battery from
there, for example in automations.

> **Important: install OpenAmpere first.** The integration is only the link to a running OpenAmpere, it does not
> replace it. OpenAmpere runs on a computer in your home network (e.g. a Raspberry Pi) and is the only program that
> talks to the inverter. Home Assistant only talks to OpenAmpere.
> → [Installing OpenAmpere](../README.md#installation)

```
Home Assistant ──(HTTPS, access token)──▶ OpenAmpere ──(Modbus)──▶ Inverter
```

Why not connect directly via Modbus? The FoxESS allows only a few Modbus connections at the same time. Two programs
on the inverter get in each other's way and overwrite each other's settings. Going through OpenAmpere there is only
one connection, and every command from Home Assistant goes through the same checks as in the app.

## Installation

1. **Install and set up OpenAmpere**, see the [README](../README.md#installation). The inverter must be connected in
   OpenAmpere.
2. **Install the integration via HACS.** The button opens the repository directly in your Home Assistant:

   [![In HACS öffnen](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Gr33ndev&repository=OpenAmpere&category=integration)

   Manually: HACS → ⋮ → **Benutzerdefinierte Repositories** (Custom repositories) →
   `https://github.com/Gr33ndev/OpenAmpere`, type **Integration** → download OpenAmpere → restart Home Assistant.
3. **Add the integration:** **Einstellungen → Geräte & Dienste → Integration hinzufügen** (Settings → Devices &
   services → Add integration) → OpenAmpere.

   [![Integration hinzufügen](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=openampere)

4. **Connect**, in one of two ways:

   **Pairing (recommended):**
   - Enter the address of OpenAmpere, e.g. `192.168.178.20`, without `http://` and without `:8080`. The HTTPS port
     is `8443`; the install script shows it at the end.
   - Home Assistant shows a 6-digit code. In OpenAmpere, the request appears with a code under
     **Mehr → Verbundene Apps** (More → Connected apps).
   - Only if both codes are **the same**: choose the permission and tap **Code stimmt – erlauben** (Code matches –
     allow). Done.

   **Connection code:**
   - In OpenAmpere, create an access token under **Mehr → Verbundene Apps** and copy the connection code.
   - In Home Assistant choose **Verbindungscode einfügen** (Paste a connection code) and paste the code. The address
     is included in the code. Only if Home Assistant reaches OpenAmpere under a different address than your browser
     do you also enter it.

## What you get in Home Assistant

**Live via push**, as fast as OpenAmpere polls the inverter (every 10 seconds by default):

| Entity | Note |
|---|---|
| PV power, house consumption, grid power, battery power | Grid: + import, − export. Battery: + discharging, − charging |
| Power per PV string | Names as in OpenAmpere; hidden strings are left out |
| PV production, consumption, grid import, grid export, battery charged/discharged | Total counters in kWh, **suitable for the Energy dashboard** |
| State of charge, battery health, voltage, current and temperature, inverter temperature | |
| Inverter connected, off-grid (power cut), charging from the grid, alarm | Yes/no |
| Per immersion heater/switch: power, on, temperature | |

**Every minute:** current electricity price, battery settings, control (view only / test mode / active).

**Energy dashboard:** **Einstellungen → Dashboards → Energie** (Settings → Dashboards → Energy). Grid import and
grid export under **Stromnetz** (Electricity grid), PV production under **Solarmodule** (Solar panels), battery
charged/discharged under **Batteriespeicher** (Home battery storage).

## Control

Only with the **Lesen + Steuern** (Read + control) permission, and only while control is switched on in OpenAmpere
(**Mehr → Steuerung und Protokoll**, More → Control and log). If it is off, these entities are unavailable in Home
Assistant.

| Entity | |
|---|---|
| Work mode | Self-use, feed-in first, backup, peak shaving |
| Backup reserve, charge limit, minimum level during a power cut | in % |
| Grid charging, grid charging target | set up once in OpenAmpere (legal notes) |
| Mode per immersion heater/switch | Automatic (surplus), off, boost |

OpenAmpere checks every change just like in the app: limits, test mode, read-back. The log shows it with the name
of the access token. **At most 6 changes per hour per battery setting**, so that an automation stuck in a loop does
not wear out the inverter's memory. Beyond that, OpenAmpere refuses with a message.

**Deliberately not changeable from Home Assistant:**
- the main control switch and test mode
- the feed-in limit
- direct charge/discharge power
- settings, tariffs, password, access tokens, backups, updates and the device configuration

You control the wallbox in Home Assistant through the
[evcc integration](https://www.home-assistant.io/integrations/evcc/).

## Security

- **A separate access token per app:** It is only valid for this interface, not for the web app, and can be removed
  at any time under Mehr → Verbundene Apps (with "last used" shown next to it). OpenAmpere stores only a hash.
- **Encrypted with a pinned certificate:** OpenAmpere creates its own certificate on first start. Home Assistant
  remembers its fingerprint and rejects any other. Anyone intercepting on the network is noticed: during pairing, by
  the codes not matching.
- **Only approved commands**, see above. The main switch stays in OpenAmpere.
- **No personal data in Home Assistant:** no serial numbers, meter numbers or grid operator data. Otherwise they
  would end up in Home Assistant backups. The integration's diagnostics redact the access token and the address.
- As with all integrations, the access token itself is stored unencrypted in Home Assistant's data and therefore in
  its backups. If a backup gets into the wrong hands: remove the access token in OpenAmpere and pair again.

## Settings and troubleshooting

- **Fewer live values:** integration → **Konfigurieren** (Configure) → **Mindestabstand zwischen Live-Werten**
  (Minimum time between live values), e.g. 30 seconds, spares small systems with an SD card. 0 takes every value.
- **"OpenAmpere ist nicht erreichbar"** (Cannot reach OpenAmpere): check the address and the HTTPS port. If
  OpenAmpere runs in Docker with `network_mode: host` (the default), the port is open directly. Mehr → Verbundene
  Apps shows whether HTTPS is running.
- **Port 8443 in use** (e.g. by a UniFi controller): in `docker-compose.yml`, add
  `OPENAMPERE_SERVER_TLS_PORT: "8444"` under `environment`, run `docker compose up -d` and pair using port 8444.
- **"Neu verbinden"** (Connect again) **in Home Assistant:** the access token was removed in OpenAmpere or the
  certificate changed, e.g. after a reinstall without the `data` folder. Simply pair again.
- **Turning off HTTPS:** `OPENAMPERE_SERVER_TLS_PORT: "0"`. Then no apps can connect.

The integration has the same version as OpenAmpere and comes from the same releases. Keep both up to date: if they
do not match, Home Assistant reports it at startup.
