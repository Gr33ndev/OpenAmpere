# Architecture

OpenAmpere is a locally running, open-source app for solar systems with battery storage. It talks directly to the inverter on the home network, without any cloud.

## Principles

- **Local:** All data stays on your own server. The app works without internet.
- **For end users:** Everything can be configured in the web app. A setup wizard detects the device automatically; no configuration files are needed.
- **Safe when writing:**
  - Control functions are off by default and additionally have a test mode.
  - Values are checked before writing and read back afterwards.
  - Every change is recorded in the log.
- **Robust:** The app copes with Modbus proxies, connection drops and restarts. It keeps running in the background even when no browser is open.

## Components

```
┌──────────── Docker container (Proxmox LXC/VM, Raspberry Pi, NAS) ────────────┐
│                                                                              │
│  drivers/              collector           storage (SQLite)       api        │
│  ├ registry (auto)  ─▶ polls every     ─▶  raw data (days)     ─▶ REST       │
│  ├ foxess/ (H3)        5–60 s, one         15-min energy          WebSocket  │
│  ├ saj/ (H2/HS2)       TCP connection      energy per PV string   cloud-compat.│
│  └ modbus.py (base)                        settings, log          web app    │
│                                                                    (PWA)     │
│  control.py: battery, backup power, export limit (switches, test mode)       │
│  cloud_import.py: history from the previous cloud (1 request/min, resumable) │
└──────────────────────────────────────────────────────────────────────────────┘
        ▲ Modbus TCP (direct or via a Modbus proxy)                 ▲ browser/smartphone
     inverter / battery
```

| Component | Purpose |
|---|---|
| `drivers/modbus.py` | Base for all drivers: one persistent, serialized connection. Transient errors such as timeouts or proxy dropouts are retried. Rejections by the device (illegal address) lead to reading registers individually; registers are only blocked if the device really rejects them. |
| `drivers/registry.py` | List of drivers and automatic detection, read-only: FoxESS at device address 247, then SAJ at 1 and 2. |
| `drivers/foxess/` | FoxESS H3 / H3 Smart / H3 Pro. New or old register map and function code are detected automatically. |
| `drivers/saj/` | SAJ H2 / HS2. Display only for now. |
| `collector.py` | Polls the device, distributes live values via WebSocket and stores them. Individual dropouts do not break the connection. |
| `storage.py` | SQLite database. Every access, including every read, goes through a lock. A file lock prevents two instances from using the same database. At the start, a damaged database (open error or failed `PRAGMA quick_check`) is renamed to `<db>.damaged-<time>` and OpenAmpere starts with an empty one; the app shows a notice. A backup restored in the app is checked, gets the current password, sessions, app tokens and secrets, and waits as `<db>.restore`; the process then starts itself again and puts it in place before opening the database, keeping the replaced one as `<db>.before-restore-<time>`. These copies are never deleted automatically. Schema version in `PRAGMA user_version`: `SCHEMA` is version 1 (databases from before had version 0 and count as 1), every change is a step in `MIGRATIONS` and `SCHEMA_VERSION + 1`, each step in one transaction. Before upgrading an existing database, it is copied to `<db>.schema-<old version>` with SQLite's backup API; copies of older versions are deleted (each is as large as the database), and without enough free space for the copy the start stops instead. An older version that finds a newer database (e.g. after the updater rolled back the image) moves it aside as `<db>.newer-<version>-<time>` and continues with `<db>.schema-<its version>`; without that copy it stops with a message. |
| `runtime.py`, `config.py` | Settings: defaults < `config.yaml` < settings from the web app < environment variables. Environment variables appear in the app as **fest eingestellt** (fixed). Changes take effect without a restart. |
| `control.py` | Battery settings and export limit. The limit follows the specified rule (60 % / 70 % of the PV module power, value from the grid operator, none). Anything above what the rule allows is rejected; with a value from the grid operator, every increase requires the operator's approval, whose reference number is logged. |
| `auth.py` | Access protection: a local password (scrypt), session cookie, CSRF header, Origin and Host checks (against DNS rebinding), lockout after failed attempts. Reading on the home network is open; changes require a login, and so do reads of secrets and of values that identify the owner or give access to their data (`PROTECTED_READS`, private settings in `config.PRIVATE`). |
| `tariffs.py` | Electricity tariffs with a start date (fixed price or dynamic), aWATTar exchange prices per quarter hour, savings calculation. |
| `charging.py` | Charging from the grid by price or time window, only via remote control with a watchdog (3 min); checks that the battery is really charging. |
| `consumers.py` | Distributes surplus in order of priority: my-PV immersion heater, continuously variable (`drivers/mypv.py`), Shelly/HTTP on or off with hysteresis and minimum times, battery priority, optionally cheap grid power, consideration for a waiting car. Runs in the fast cycle (5 to 15 s). |
| `evcc.py` | Integration with evcc for wallboxes: reads `/api/state` fault-tolerantly, sends charge mode, charge target and charge plan. At `/api/evcc/site`, evcc receives grid, solar and battery, see [evcc.md](evcc.md). |
| `diagnostics.py` | Read-only diagnostics: blocks with FC03/FC04, scaling, export limit, remote control, optionally the connection limit; plus passively collected connection drops, meter anomalies and reset times of the daily counters. |
| `notify.py` | Notifications via ntfy, each event only once. |
| `cloud_import.py` | Imports the history from the previous manufacturer cloud via its customer API, alternatively via ZIP from `tools/cloud-export`. Own measurements are never overwritten. |
| `simulator.py` | Modbus TCP simulator for FoxESS and SAJ, with PV strings, temperatures, export limit and proxy errors. This makes it possible to develop and test without a real system. |
| `web/` | React PWA: dashboard, report, **Mehr** (More) with all settings, setup wizard. |

How to add support for another device: [Writing a driver](writing-a-driver.md).

## Data model

- **samples:** Raw values of every poll (power values, state of charge, power per PV input, temperatures). How long they are kept is configurable.
- **energy_15m:** Energy per quarter hour from the inverter's counters. Kept permanently. `source` indicates the origin, `local` or `cloud`.
- **pv_input_15m:** Energy per PV input (string) and quarter hour, summed up from the power.
- **meta / control_log:** Settings, import progress, log of all control commands.

## Repository contents

- `src/openampere/`: backend, drivers, simulator
- `web/`: web app (PWA)
- `tools/cloud-export/`: backs up the history from the previous cloud while it is still running
- `docs/`: architecture and register documentation
- `scripts/third_party_licenses.py`: generates the license notices for the bundled components

The repository deliberately contains no code, graphics or texts from third-party apps. Only facts such as interfaces, register addresses and data models were adopted.
