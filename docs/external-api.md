# Interface for other apps

This page is for developers of other apps, such as dashboards, home automation or your own scripts, that want to
read data from OpenAmpere or control it. The Home Assistant integration (`custom_components/openampere/`) uses exactly
this interface and is the reference client: [api.py](../custom_components/openampere/api.py) and
[config_flow.py](../custom_components/openampere/config_flow.py). The server side is
[external.py](../src/openampere/external.py) and [apitokens.py](../src/openampere/apitokens.py) (#76).

Apps that need only the power values of the former cloud can use the read-only endpoint
`/api/v1/installation/{id}/now/all/power` of the web app instead. It needs no token, but it offers only five values.

> **Use OpenAmpere as the only Modbus client.** Many inverters accept only one Modbus TCP connection, or very few
> (#311). An app that reads the inverter directly while OpenAmpere runs can take turns with OpenAmpere or lock it
> out. Read the values from this interface instead.

## Overview

- **Base address:** `https://<host>:<port>/api/external/v1`. The HTTPS port is `8443` with Docker and on the Android
  tablet, and `8743` for the Home Assistant app (its default port mapping). The owner can change it. The page **Mehr
  → Verbundene Apps** in OpenAmpere shows the port and the certificate fingerprint.
- **HTTPS only.** Plain HTTP gets `403` with `"code": "https_required"`. The web app's port (8080) refuses it the
  same way.
- **Self-signed certificate, pinned by its SHA-256 fingerprint.** There is no certificate authority and no host
  name to check. Your app stores the fingerprint and accepts exactly this certificate, nothing else. A new
  certificate means pairing again.
- **A token per app** in the header `Authorization: Bearer oa_…`. A token is valid only for this interface, never
  for the web app, and the owner can remove it at any time under **Mehr → Verbundene Apps**.
- **Two scopes:** `read` sees the data, `control` may also use the commands below. The owner chooses the scope when
  approving the app.
- **JSON** in both directions. Units: power in W, energy in Wh, temperatures in °C, state of charge in %, times as
  Unix time in seconds.
- **`api_version`** is `1`. It changes only with an incompatible change; new fields can appear in any answer at any
  time, so ignore fields you do not know.

## Connecting

There are two ways to get a token. Pairing is the better one for an app with a user interface: the owner does not
copy a secret, only compares six digits.

### Pairing

Pairing works like numeric comparison when pairing Bluetooth devices. Your app and OpenAmpere each compute six digits
from the certificate the app sees and two random numbers. If someone sits in between, the app sees a different
certificate and the digits differ.

1. **Ask the owner for the address and the HTTPS port.** Prefer the IP address: a host name needs to be allowed in
   OpenAmpere first (see [Errors](#errors)).
2. **Read the certificate.** Open a TLS connection without verification and compute the SHA-256 of the certificate
   in DER form (`fingerprint`, 64 hex characters). Keep it; from now on accept only this certificate.
3. **Commit to a random number.** Create 32 random bytes `app_nonce` and send their SHA-256 as hex:

   ```http
   POST /api/external/v1/pair/start
   {"name": "My dashboard", "commitment": "<sha256(app_nonce) as 64 hex characters>"}
   ```

   The answer contains `request_id`, `poll_key` (secret, keep it in memory), the server's random number `nonce`
   (32 bytes as hex), `expires_in` (600 s), `api_version`, `installation_id` and `version`. The name (at most 40
   characters) is what the owner sees in OpenAmpere and in the control log.
4. **Reveal your number** within 60 seconds:

   ```http
   POST /api/external/v1/pair/{request_id}/reveal
   {"poll_key": "<poll_key>", "nonce": "<app_nonce as hex>"}
   ```

   Only now does the request appear in OpenAmpere, with its code.
5. **Show the code** and ask the owner to compare it with the one in OpenAmpere (**Mehr → Verbundene Apps**):

   ```python
   digest = sha256(bytes.fromhex(fingerprint) + app_nonce + bytes.fromhex(server_nonce)).digest()
   code = f"{int.from_bytes(digest[:4], 'big') % 1_000_000:06d}"
   ```

6. **Wait for the decision.** Poll every 2 seconds:

   ```http
   POST /api/external/v1/pair/{request_id}/status
   {"poll_key": "<poll_key>"}
   ```

   The answer is `{"status": "pending"}`, `{"status": "rejected"}` or
   `{"status": "approved", "token": "oa_…", "scope": "read"}`. The token is handed out once; store it together with
   address, port and fingerprint. `404` means the request expired (after 10 minutes) or OpenAmpere restarted: start
   again.

Limits against misuse: at most 3 open requests per device and 10 in total, at most 10 starts per device and 30 in
total per hour. Beyond that `pair/start` answers `429`.
OpenAmpere keeps at most 10 tokens. With 10 already there, the owner cannot approve until they remove one; your
app then sees `pending` until the request expires.

### Connection code

Alternatively, the owner creates a token under **Mehr → Verbundene Apps** and copies the connection code. It is
`openampere1:` followed by URL-safe Base64 (without padding) of this JSON:

```json
{"host": "192.168.x.x", "port": 8443, "fingerprint": "<64 hex characters>", "token": "oa_…"}
```

The host is the address the owner's browser used; your app should let the owner change it. A host name instead of
an IP address works only after the owner added it under **Mehr → Zugriffsschutz → Eigene Adressen**; otherwise
every HTTP request gets `421`.

### Example client

Pairing and one reading in Python, with nothing but the standard library. A real app keeps the token and the
fingerprint and skips the pairing next time; for live values it opens the WebSocket (`/ws`) with the same header and
the same certificate check.

```python
import hashlib
import http.client
import json
import secrets
import ssl
import time

HOST, PORT = "192.168.x.x", 8443  # ask the owner


def connect(fingerprint=None):
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE  # instead: exactly this certificate, checked below
    conn = http.client.HTTPSConnection(HOST, PORT, context=context, timeout=20)
    conn.connect()
    seen = hashlib.sha256(conn.sock.getpeercert(binary_form=True)).hexdigest()
    if fingerprint and seen != fingerprint:
        conn.close()
        raise RuntimeError("OpenAmpere shows a different certificate: pair again")
    return conn, seen


def call(method, path, fingerprint, body=None, token=None):
    conn, _ = connect(fingerprint)
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    conn.request(method, "/api/external/v1" + path, json.dumps(body) if body is not None else None, headers)
    response = conn.getresponse()
    data = json.loads(response.read() or b"{}")
    conn.close()
    if response.status >= 400:
        raise RuntimeError(f"{response.status}: {data.get('detail')}")
    return data


# pairing: once, then store HOST, PORT, fingerprint and token
_, fingerprint = connect()
app_nonce = secrets.token_bytes(32)
start = call("POST", "/pair/start", fingerprint,
             {"name": "My dashboard", "commitment": hashlib.sha256(app_nonce).hexdigest()})
request, poll = f"/pair/{start['request_id']}", {"poll_key": start["poll_key"]}
call("POST", request + "/reveal", fingerprint, {**poll, "nonce": app_nonce.hex()})
digest = hashlib.sha256(bytes.fromhex(fingerprint) + app_nonce + bytes.fromhex(start["nonce"])).digest()
print("Compare in OpenAmpere (Mehr → Verbundene Apps):", f"{int.from_bytes(digest[:4], 'big') % 1_000_000:06d}")
while (status := call("POST", request + "/status", fingerprint, poll))["status"] == "pending":
    time.sleep(2)
if status["status"] != "approved":
    raise SystemExit("The owner rejected the request.")
token = status["token"]

# reading
state = call("GET", "/state", fingerprint, token=token)
live = state["live"] or {}
print(f"PV {live.get('pv_power')} W, battery {live.get('battery_soc')} %, grid {live.get('grid_power')} W")
```

## Reading

### `GET /info`

What is connected. Read it once after connecting and again when `status` changes in a way that matters to you (for
example a new PV inverter in `pv_external`).

```json
{
  "api_version": 1, "version": "0.38.0", "installation_id": "…",
  "token": {"name": "My dashboard", "scope": "read"},
  "device": {"manufacturer": "FoxESS", "model": "H3", "firmware": "…", "rated_power_w": 10000,
             "supports_control": true, "work_modes": null, "backup_reserve": false},
  "pv_inputs": [{"index": 0, "name": "Süddach"}, {"index": 1, "name": "Modulfeld 2"}],
  "pv_input_count": 2,
  "pv_external": null,
  "devices": [{"id": "…", "name": "Heizstab", "kind": "mypv"}],
  "wallboxes": [{"id": "…", "name": "Garage", "model": "…", "read_only": false}],
  "poll_interval": 10, "web_port": 8080
}
```

- `device` is `null` until OpenAmpere has found the inverter. There is no serial number on purpose.
- `work_modes`: the battery modes the device offers, `null` for all of them (see [Battery](#put-battery)).
- `backup_reserve`: whether the device has its own reserve for the backup mode (`backup_soc`).
- `pv_inputs`: the inputs the owner did not hide, with the owner's names. `index` is the position in
  `live.pv_inputs`.
- `pv_external`: `{"name": …}` if the owner counts a second PV inverter, otherwise `null`.
- `devices`: heaters and relays OpenAmpere switches itself; `wallboxes`: wallboxes it reads itself. Wallboxes run
  through evcc are not part of this interface.

### `GET /state`

Everything at once: the same content as a [live message](#live-values-ws), plus the settings.

```json
{
  "type": "state",
  "live": { "…": "see below" },
  "status": { "…": "see below" },
  "devices": [],
  "wallboxes": [],
  "battery_settings": {"work_mode": "self_use", "min_soc": 10, "max_soc": 100, "min_soc_on_grid": 15,
                       "backup_soc": null},
  "grid_charging": {"enabled": false, "target_soc": 80, "active": false},
  "price_ct": 31.2
}
```

- `battery_settings` is `null` while they could not be read. OpenAmpere waits at most 5 seconds for a fresh reading
  and otherwise answers with the last one.
- `price_ct`: the electricity price now in cent per kWh from the owner's tariff (fixed or dynamic), `null` without a
  tariff.

### `live`: the latest reading

`null` before the first reading. All values can be `null` when the device does not provide them.

| Field | Meaning |
|---|---|
| `timestamp` | Time of the reading |
| `pv_power` | Solar power, ≥ 0. Includes `pv_external_power` if the owner counts a second PV inverter. |
| `pv_external_power` | Power of a second PV inverter the device measures, ≥ 0 |
| `house_power` | Consumption of the house, ≥ 0 |
| `grid_power` | **+ import** from the grid, **− export** to the grid |
| `battery_power` | **+ discharging**, **− charging** |
| `battery_soc`, `battery_soh` | State of charge and state of health in % |
| `battery_voltage`, `battery_current`, `battery_temperature` | Battery in V, A and °C |
| `pv_inputs` | List per PV input: `power`, `voltage`, `current`. The position matches `index` in `/info`; also contains hidden inputs. |
| `temperatures` | °C by name: `inverter`, `ambient`, `battery`, `battery_cell_max`, `battery_cell_min`, and the same with `battery2` for a second battery. Only what the device provides. |
| `inverter_state` | The device's own state number (meaning per device, see [docs/devices/](devices/README.md)) |
| `off_grid` | The device reports off-grid operation in this reading. For a confirmed outage use `status.off_grid`. |
| `alarms` | The device's alarm codes |
| `today` | Energy since midnight in Wh, see below |
| `totals` | Lifetime counters in Wh, see below |
| `inverters` | Empty with one inverter. With further inverters of the same installation (#619): one entry per inverter, the first with `id` `"main"`, each with `id`, `name`, `connected` and, while it answers, `pv_power`, `pv_external_power`, `battery_power`, `battery_soc`, `grid_power` and `alarms`; `left_out: true` for one that has been away for more than 5 minutes. The fields above are then the combined values: PV and battery added up, the grid from the first inverter (plus those that measure a grid connection of their own), the house from the balance, the state of charge weighted by battery size; `pv_inputs` holds the inputs of all inverters, each inverter always at the same positions (empty while it has no reading). While a further inverter has had no reading for up to 5 minutes, the combined PV, battery, house, state of charge and the summed counters in `totals` and `today` are `null` (the gap is spread later). After that it is left out (`left_out`): the combined values and counters come back without it, so a lower sum then means an inverter is missing, not less power. Its counters count again from its next reading; what it produced while away is not in `totals`. Battery and temperature details are the first inverter's. |

Fields of `today` and `totals`: `pv`, `consumption` (what the app shows as consumption), `grid_import`,
`grid_export`, `battery_charge`, `battery_discharge`, `load` (the inverter's own load counter, which can count much
more than the house used; prefer `consumption`) and, only in `totals`, `pv_external`.

### `status`

| Field | Meaning |
|---|---|
| `connected` | OpenAmpere is connected to the inverter |
| `stale` | No new reading for more than 60 seconds: show the values as outdated |
| `last_update` | Time of the latest reading |
| `off_grid` | A power outage OpenAmpere has confirmed |
| `grid_charging` | Grid charging is running right now |
| `control` | `{"enabled", "dry_run"}`: the master switch for control and the test mode |
| `firmware` | Firmware of the inverter (changes with an update) |
| `token_days_left` | Days until the token of an Enphase gateway runs out, `null` for other devices |
| `pv_external` | As in `/info` |

### `devices` and `wallboxes`

`devices` (heaters and relays OpenAmpere switches): `id`, `name`, `kind`, `enabled`, `power_w`, `on`,
`temperature_c`, `target_c`, `status`, `error` and `mode` (`auto`, `off` or `boost`).

`wallboxes` (wallboxes OpenAmpere reads itself): `id`, `name`, `enabled`, `mode`, `max_current_a` (the owner's
limit), `mode_current_a` (highest current per charging mode), and the live values `online`, `state`, `connected`,
`charging`, `power_w`, `current_a`. The live values are `null` while the wallbox does not answer, which is normal for
a portable one that is unplugged.

### Live values: `/ws`

A WebSocket at `wss://<host>:<port>/api/external/v1/ws` with the same `Authorization` header. Right after connecting
and after every new reading of the inverter (every `poll_interval` seconds) OpenAmpere sends:

```json
{"type": "live", "live": {}, "status": {}, "devices": [], "wallboxes": []}
```

The settings (`battery_settings`, `grid_charging`, `price_ct`) are not part of it; read `/state` after a command or
every few minutes. A connection without a valid token or over plain `ws://` is refused in the handshake with HTTP `403`. An open connection whose token the owner removes
is closed with code `1008`. Reconnect with a growing pause (the integration waits 5, 10, 30, then
60 seconds). OpenAmpere shows which apps are connected live.

### History

The history a `read` token may see, with the same answers as the charts in the web app (#588). All of them are
`GET` requests with query parameters; times in the answers are the start of each bucket.

Parameters shared by several endpoints:

- `period`: `day` (default), `week`, `month` or `year`.
- `date`: a day in the period as `YYYY-MM-DD`, default today. Also `YYYY-Www` for a week, `YYYY-MM` for a month and
  `YYYY` for a year. Days and weeks follow the time zone of the installation; a week starts on Monday.
- `resolution`: `15m`, `60m`, `day` or `month`.

An unknown period or a date that cannot be read answers `400`; a parameter outside its values (`step`, `resolution`,
`mode`) answers `422` with FastAPI's technical list (see [Errors](#errors)).

**At most 30 reads of the history per minute and token**, for all five endpoints together. Beyond that the answer is
`429`, with the seconds to wait in the message. Live values (`/state`, `/ws`) do not count.

**What is kept:** energy per quarter hour is kept for good, so `summary`, `energy` and the energy of `pv-inputs` work
for any period OpenAmpere has recorded (or imported from the former cloud). The single readings behind `power`, the
power curve of `pv-inputs` and `temperatures` are kept for 30 days by default (the owner can change that); older
days answer with empty `entries`, except `power`, which then has the average of each quarter hour (see there).

#### `GET /history/summary`

Energy of a period, the shares and the money, as the report in the app shows them.

```http
GET /api/external/v1/history/summary?period=month&date=2026-06
```

```json
{
  "period": "month", "from": 1780264800, "to": 1782856800, "quarters": 2880,
  "partial_since": null, "recorded_since": null,
  "energy_wh": {"pv": 1200000, "load": 900000, "grid_import": 150000, "grid_export": 450000,
                "battery_charge": 200000, "battery_discharge": 180000, "pv_external": null},
  "autarky": 0.83, "self_consumption": 0.63,
  "money": {"savings_eur": 210.5, "feed_in_eur": 36.0, "grid_cost_eur": 45.3, "base_fee_eur": 12.0,
            "net_cost_eur": 21.3, "incomplete": false}
}
```

- `energy_wh`: energy in Wh. `load` is the consumption of the house (as `consumption` in `live.today`), not the
  inverter's own load counter. `pv_external`: a counted second PV inverter, otherwise `null`; it is part of `pv`.
- `quarters`: quarter hours with data, including the one that is running.
- `recorded_since`: on the first day of recording, when recording started; the values then come from the inverter's
  daily counters and are complete. `partial_since`: the values cover only the time since then.
- `autarky`: share of the consumption not drawn from the grid, `self_consumption`: share of the solar energy used in
  the house; 0–1, `null` without consumption or solar energy.
- `money`: from the owner's tariffs. `savings_eur`: self-used solar energy at the price of that moment plus the feed-in
  pay; `net_cost_eur`: grid power plus base fee minus feed-in pay. `incomplete`: prices were missing for part of the
  period.

#### `GET /history/energy`

Energy per bucket, for bar charts. Parameters: `period`, `date`, `resolution` (default `15m`).

```json
{"period": "day", "resolution": "60m", "from": 1780351200, "to": 1780437600,
 "entries": [{"ts": 1780351200, "pv": 0, "load": 350, "grid_import": 350, "grid_export": 0,
              "battery_charge": 0, "battery_discharge": 0, "pv_external": null, "soc": 42}]}
```

Each entry has the energy of the bucket in Wh (fields as in `summary`) and `soc`, the state of charge at the end of
the bucket. A bucket without data is missing, not zero. The quarter hour that is running is included.

#### `GET /history/power`

Power curve of one day. Parameters: `date`, `step` (seconds per point, 10–3600, default 60).

```json
{"from": 1780351200, "to": 1780437600, "step": 300,
 "entries": [{"ts": 1780394400, "pv": 4200, "house": 600, "grid": -2100, "battery": -1500, "soc": 64,
              "pv_external": null}]}
```

Averages per step in W, with the signs of `live`: `grid` + import, − export; `battery` + discharging, − charging. A
step without readings is missing. A quarter hour with no readings but with stored energy or state of charge (a day
imported from the former cloud, or older than the readings are kept) has one entry at its start with
`"quarter": true`: the average power of that quarter hour from its energy (Wh × 4) and its state of charge. Values
the quarter hour has no energy for are `null`. A gap in OpenAmpere's own readings (it did not run) stays missing, also
once the readings around it are no longer kept.

#### `GET /history/pv-inputs`

Per PV input (module array). Parameters: `period`, `date`, `mode` (`energy`, default, or `power`, only with
`period=day`), `resolution` (default `60m`, for `energy`).

```json
{"mode": "energy", "inputs": [1, 2], "labels": ["Süddach", "Modulfeld 2"],
 "entries": [{"ts": 1780394400, "values": [2100, 1400]}], "totals_wh": [21000, 14000]}
```

- `inputs`: the numbers of the inputs, **counted from 1** (`index` in `/info` counts from 0). Inputs the owner hid are left out,
  and so are inputs without any yield in the period, unless no input had any (at night, or a day without data).
- `labels`: the owner's names, in the same order.
- `entries[].values`: per input, energy in Wh per bucket (`energy`) or average power in W per 5 minutes (`power`).
- `totals_wh`: energy of each input over the period, `null` with `mode=power`.

#### `GET /history/temperatures`

Temperatures of one day. Parameters: `date`, `step` (seconds, 60–3600, default 300).

```json
{"entries": [{"ts": 1780394400, "inverter": 41.5, "battery": 24.0, "cell_max": 24.5, "cell_min": 23.5}]}
```

Averages per step in °C, `null` for what the device does not provide.

## Controlling

Commands need a `control` token, and the owner must have switched control on in OpenAmpere (**Mehr → Steuerung und
Protokoll**). Everything goes through the same checks as in the app:

- **Test mode** (`status.control.dry_run`): OpenAmpere checks the command and logs it, but writes nothing to the
  inverter. The answer of `/battery` says so with `"dry_run": true`.
- **Limits** of the device are checked before writing, the value is read back afterwards.
- **Control log:** every change appears there with the token's name, so the owner sees what your app did.
- **Not available** on purpose: the master switch and test mode, the export limit, settings, tariffs, the
  configuration of devices, tokens, backups and updates. These stay with the owner in the app.

### `PUT /battery`

Battery settings. Send only what you want to change.

```json
{"work_mode": "self_use", "min_soc": 10, "max_soc": 100, "min_soc_on_grid": 20}
```

| Field | Values |
|---|---|
| `work_mode` | `self_use`, `feed_in_first`, `backup`, `peak_shaving`; only those in `device.work_modes` if that is not `null` |
| `min_soc` | Lowest state of charge during a power outage (off-grid), 0–100 % |
| `min_soc_on_grid` | Backup reserve, 10–100 %, at least `min_soc` |
| `max_soc` | Charge limit, 10–100 %, above `min_soc_on_grid` |
| `backup_soc` | Reserve in the backup mode, 10–100 %, only if `device.backup_reserve`; not above `max_soc` |

The answer is `{"dry_run", "written", "warning"}`. A backup reserve below 10 % can only be set in OpenAmpere itself,
after a warning (`428`).

**At most 6 changes per hour per setting.** These settings end up in the inverter's permanent memory, and an
automation stuck in a loop must not wear it out. Beyond that the answer is `429`, with the minutes to wait in the
message. A command refused before anything was written does not count.

Not every inverter accepts every command: `supports_control` in `/info` says whether OpenAmpere may control the
device at all.

With more than one inverter (#619) this interface controls the first one; the battery settings of the further ones
can be changed in the app.

### `PUT /grid-charging`

Charging the battery from the grid, for example when electricity is cheap.

```json
{"enabled": true, "target_soc": 80}
```

`target_soc`: 20–100 %. The owner must have set up grid charging once in OpenAmpere (it asks about the legal side);
until then switching it on answers `400`. OpenAmpere charges through the inverter's remote control with a watchdog,
so charging stops by itself when OpenAmpere stops. Answer: `{"enabled", "target_soc", "active"}`. At most 60 changes
per hour.

### `PUT /devices/{id}/mode`

Mode of a heater or relay from `devices`:

```json
{"mode": "boost", "hours": 2}
```

`mode`: `auto` (follow the solar surplus), `off` (stay off) or `boost` (full power, for example hot water now).
`hours` (optional, up to 48) ends `off` or `boost` after that time; without it the mode stays until changed. Answer:
`{"devices": [...]}`. At most 60 changes per hour per device.

### `PUT /wallboxes/{id}/mode`

Charging mode of a wallbox OpenAmpere controls itself (not `read_only`):

```json
{"mode": "pv", "current_a": 16}
```

`mode`: `off`, `pv` (solar surplus only), `minpv` (minimum current plus surplus), `now` (full current) or `manual` (OpenAmpere leaves the wallbox alone; the owner
controls it on the wallbox itself).
`current_a` (optional, only with `pv`, `minpv` and `now`) sets the highest current of that mode, within the owner's
limit. Answer: `{"wallboxes": [...]}`. At most 60 changes per hour per wallbox.

## Errors

Errors are JSON with a German explanation for the owner in `detail`, which your app can show as it is (except for
`422`, see below):

```json
{"detail": "Dieser Zugang darf nur lesen.", "code": "read_only"}
```

| Status | Meaning |
|---|---|
| `400` | Invalid value or command; `detail` says what is wrong |
| `401` | Token invalid or removed (`"code": "token_invalid"`): pair again |
| `421` | An HTTP request used a host name that OpenAmpere does not know (protection against DNS rebinding); no `code` field. Use the IP address, or the owner adds the name under **Mehr → Zugriffsschutz → Eigene Adressen**. |
| `403` | Plain HTTP (`https_required`), a `read` token tried to write (`read_only`), or control is switched off in OpenAmpere |
| `404` | Pairing request expired or unknown |
| `422` | The body is not a JSON object (an array, a number, broken JSON or no body), or a query parameter of the history is outside its values. Here `detail` is FastAPI's technical list in English, not a text for the owner: a bug in your app. |
| `428` | Needs a confirmation that only OpenAmpere itself can ask for |
| `429` | Too many changes, pairing requests or reads of the history; `detail` says how long to wait |
| `502` | The inverter refused the write or did not confirm it |
| `503` | OpenAmpere is not connected to the inverter |

A changed certificate shows up as a failed TLS handshake (the fingerprint does not match). Do not accept the new
certificate silently: ask the owner to pair again.

## Good manners

- Read with `/ws` instead of polling `/state` every few seconds.
- Read the history when it is shown or once per quarter hour, not with every live value: `power` with a small
  `step` reads every reading of the day.
- Send a command only when the owner or a rule of your app really changes something, not on every reading.
- Show `status.stale` and `status.connected`; old values shown as current are worse than none.
- Store the token like a password. Whoever has a `control` token can change the battery settings.
- Do not show or log the token, and keep the owner's data on their network.
