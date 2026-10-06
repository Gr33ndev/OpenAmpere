Deutsch: [README.de.md](README.de.md)

# Export from the EKD cloud

Backs up the complete history of a system from the EKD cloud (app "Ampere.IQ") while the cloud is still reachable.

> Unofficial tool: OpenAmpere has nothing to do with Energiekonzepte Deutschland GmbH (EKD) and was neither commissioned nor authorized by it. The tool uses only the public customer API with your personal key. "EKD" and "Ampere.IQ" are names belonging to their respective owners. See [Background & legal notes](../../README.md#background--legal-notes).

- Uses only the official, read-only customer API of the EKD cloud.
- Runs on Python 3.9 or newer and needs no additional packages.
- Every response is saved unchanged as JSON, one file per endpoint and day. If the script is interrupted, simply start it again and it picks up where it left off.
- It sends at most one request every 65 seconds. Clients that poll more often get blocked by the API.

## 1. Create an API key

In the "Ampere.IQ" app: **Mehr → Konfiguration API-Zugang** (More → API access configuration) → generate a key.

## 2. Create `.env`

```
CLOUD_API_KEY=dein-schluessel
# optional:
# CLOUD_INSTALLATION_UUID=…     # only needed if the key covers several systems
# CLOUD_MIN_INTERVAL=65         # seconds between requests
# CLOUD_EXPORT_DIR=data/cloud-export
```

Never share the `.env` file and never upload it anywhere.

## 3. Test and export

```bash
python3 cloud_export.py check
```

```bash
nohup caffeinate -i python3 cloud_export.py export >> cloud-export.log 2>&1 &
```

`caffeinate` keeps a Mac awake. On Linux, just leave it out.

With `--start YYYY-MM-DD` you skip the automatic search for the first date with data.

## What gets backed up

The following is backed up for each day, the histories at 15-minute resolution:

| Folder | Endpoint |
|---|---|
| `work/` | `history/common/work`: energy flows (PV, house, grid, battery) |
| `stateOfCharge/` | `history/stateOfCharge`: battery state of charge |
| `gridDraw/` | `history/gridDraw/work`: grid import |
| `power/` | `history/common/power`: power |
| `consumptionWork/`, `consumptionPower/` | consumption |
| `totalWork/` | `total/common/work`: daily totals |

**Duration:** about 7 requests per day of system operation, so roughly 2 days of runtime per year of system operation.
