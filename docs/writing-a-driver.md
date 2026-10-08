# Writing a driver

This guide is for developers who want OpenAmpere to support another inverter, hybrid inverter or battery system.
It explains how drivers fit into the app, how to add a read-only driver step by step, what the (optional) write path
has to guarantee, how to work without the hardware, which tests a driver needs and how to get it reviewed.

**Read-only first.** A new driver starts as display only (`supports_control=False`), like the SAJ driver today.
Writing to a device comes later, in a separate pull request, once the registers have been verified on a real device.

Contents:

1. [How drivers fit in](#1-how-drivers-fit-in)
2. [A read-only driver, step by step](#2-a-read-only-driver-step-by-step)
3. [The write path (optional)](#3-the-write-path-optional)
4. [Developing without hardware](#4-developing-without-hardware)
5. [Tests](#5-tests)
6. [Licensing and sources](#6-licensing-and-sources)
7. [Getting it reviewed](#7-getting-it-reviewed)

## 1. How drivers fit in

| File | Role |
|---|---|
| `src/openampere/drivers/base.py` | Device-independent data model (`Snapshot`, `DeviceInfo`, `EnergyCounters`, `PvInput`, `BatterySettings`, `ExportLimit`, `WorkMode`) and the `InverterDriver` protocol. |
| `src/openampere/drivers/modbus.py` | `ModbusDevice`, the base class of all inverter drivers, plus the error classes and `friendly_error()`. |
| `src/openampere/drivers/regs.py` | `Reg`, `Kind` (`U16`, `I16`, `U32`, `I32`), `decode()` and `encode()`. |
| `src/openampere/drivers/registry.py` | `DRIVERS`, `LABELS`, `create()`, `detect_driver()`, `detect()` and `AutoDriver`. |
| `src/openampere/drivers/foxess/` | FoxESS H3 family: two register maps (`H3_NEW`, `H3_LEGACY`), read and write path. |
| `src/openampere/drivers/saj/` | SAJ H2 / HS2: read-only. The smallest complete example and the best template. |
| `src/openampere/drivers/mypv.py` | my-PV heating rods. Not an inverter driver, see [Other devices](#other-devices). |

### The driver interface

The collector and the control features only talk to the `InverterDriver` protocol in `base.py`:

| Method | Purpose |
|---|---|
| `connect() -> DeviceInfo` | Identify the device. Called before the first reading and after every disconnect. |
| `read() -> Snapshot` | One reading of all live values. |
| `close()` | Release the TCP connection. |
| `read_settings() -> BatterySettings` | Work mode and SoC limits, as far as known. |
| `read_export_limit() -> ExportLimit` | Feed-in limit; `supported=False` if not available. |
| `write_work_mode()`, `write_soc_limits()`, `set_remote_power()`, `release_remote_power()`, `write_export_limit()` | Write path, see [section 3](#3-the-write-path-optional). |

In practice every inverter driver subclasses `ModbusDevice` from `modbus.py`, which already provides:

- one persistent, serialised Modbus TCP connection (inverters allow only a few), with a pause of `REQUEST_GAP_S`
  between requests and `CONNECT_SETTLE_S` after connecting;
- `_read()` with retries for transient errors, `_read_block()` that falls back to single registers when the device
  rejects a block and remembers rejected addresses for `BAD_ADDRESS_TTL_S`, `_read_reg()`, `_probe()` and `_write()`;
- `connect()`, which calls your `_detect()` once and afterwards only returns the cached `self.info`;
- `disconnect()` (closes the socket but keeps `self.info`, used by the connection mode `per_poll`) and `close()`.

Errors are classified so the rest of the app can react correctly:

| Exception | Meaning |
|---|---|
| `DeviceUnreachable` | Nothing accepts TCP connections at host:port. Detection stops right away. |
| `DetectionFailed` | Something answers, but no driver recognises it (German message for the app). |
| `ModbusIllegalError` | The device rejected the request (Modbus exception 1, 2 or 3). Retrying will not help. |
| `ModbusTransientError` | Timeout, busy device, a proxy that could not reach the inverter. Has `transient = True`. |

### `Snapshot` and its sign conventions

`read()` returns a `Snapshot`. Every field is optional: `None` means "this device does not provide it" or "not
readable right now". **A wrong value is worse than a missing one.**

| Field | Unit | Convention |
|---|---|---|
| `pv_power` | W | `>= 0` |
| `house_power` | W | `>= 0` (consumption) |
| `grid_power` | W | `+` = import from the grid, `-` = export to the grid |
| `battery_power` | W | `+` = discharging, `-` = charging |
| `battery_soc`, `battery_soh` | % | 0 to 100 |
| `pv_inputs` | | one `PvInput(power, voltage, current)` per MPPT tracker or string |
| `temperatures` | °C | keys from `TEMPERATURE_KEYS` (`inverter`, `ambient`, `battery`, ...) |
| `battery_voltage`, `battery_current`, `battery_temperature` | V, A, °C | |
| `inverter_state`, `alarms` | | raw device codes |
| `off_grid` | | `True` while the system runs in backup mode during a power cut |
| `totals`, `today` | Wh | `EnergyCounters`: `pv`, `load`, `grid_import`, `grid_export`, `battery_charge`, `battery_discharge` |

Things that are easy to get wrong:

- **Signs differ per vendor.** FoxESS reports grid power with `+` = export, so `foxess/driver.py` negates it in
  `to_snapshot()`. Convert in the driver; never let a vendor's convention leak into the snapshot.
- **Counters are monotonic lifetime values in Wh.** The quarter-hour energy table (`energy_15m`) is only filled when
  all six `totals` counters are present (`Storage._accumulate()`); with a missing counter the report stays empty.
- **Do not switch the source of a counter between readings.** The SAJ driver pins the grid counters to either the
  summed or the L1 registers after the first reading (`grid_counter_source`), because switching would look like a
  huge energy jump.
- The collector uses `today.pv` to notice when the device resets its daily counters, and `off_grid` together with
  `grid_power` to record power cuts (`outages.py`).

### Registry and automatic detection

`registry.py` holds the list of drivers and does the detection:

```python
DRIVERS: dict[str, type[ModbusDevice]] = {"foxess": FoxessDriver, "saj": SajDriver}
LABELS = {"auto": "Automatisch erkennen", "foxess": "FoxESS H3 / H3 Smart / H3 Pro", "saj": "SAJ H2 / HS2 (nur Anzeige)"}
```

`detect_driver()` goes through `DRIVERS` in order and, for each driver, through its `DEFAULT_UNITS` (Modbus unit
ids; FoxESS `(247,)`, SAJ `(1, 2)`), unless the user set a unit id. For each candidate it creates the driver with
`read_attempts=2` and a timeout of at least `DETECT_TIMEOUT_S` and calls `connect()`:

- the first driver whose `_detect()` succeeds wins; its connection is kept and handed over;
- `DeviceUnreachable` aborts the whole detection (nothing listens there);
- `ConnectionError`, `ModbusReadError`, `OSError` and timeouts move on to the next unit or driver;
- if nobody matches, `DetectionFailed` is raised.

**Detection only reads.** Vendors use disjoint register ranges, so probing the wrong vendor only produces Modbus
rejections. A driver must never write during detection, and it must not claim a device that is not its own: check an
identification block *and* plausible values (see the SAJ driver). `AutoDriver` runs this detection on the first
`connect()` and then forwards every call to the detected driver. `create()` builds a driver directly when the user
picked one in the setup.

### How the collector uses a driver

`Collector._run()` in `collector.py` does, every `inverter.poll_interval` seconds:

1. `connect()` if not connected, which returns the `DeviceInfo`;
2. `snap = (await driver.read()).sanitize()`; after the first good reading it notes a firmware change;
3. records outages, stores the snapshot, publishes it to the WebSocket subscribers;
4. with the connection mode `per_poll`, calls `disconnect()` after each reading.

On an exception that has `transient = True`, up to `MAX_TRANSIENT` consecutive failures keep the connection. Anything
else marks the device as disconnected, shows `friendly_error(err)` in the app, calls `close()` and retries with an
exponential backoff (5 s up to 300 s). So raise `ModbusTransientError` for "try again" and `ModbusIllegalError` or
`ConnectionError` for "this does not work".

## 2. A read-only driver, step by step

Use `src/openampere/drivers/saj/` as the template. Replace `<vendor>` below with a short lower-case key, e.g. `acme`.

### Step 1: the register map

Create `src/openampere/drivers/<vendor>/__init__.py` (empty) and `registers.py` with the facts only:

```python
"""ACME X1 hybrid inverters.

Register facts from <source> (license: see NOTICE). Reads use FC03, unit id 1.

VERIFY ON HARDWARE: sign of battery power (0x0120).
"""
from ..regs import Kind, Reg

READ_FUNCTION = 3
INFO_ADDRESS, INFO_LENGTH = 0x8000, 20   # identification block
DEVICE_TYPES = {0x0001: "X1 5K", 0x0002: "X1 10K"}  # type code in the first word of the block
BLOCKS = ((0x0100, 40), (0x0200, 24))    # contiguous ranges read every poll
VALUES = {
    "battery_soc": Reg(0x0105, Kind.U16, 0.1),
    "battery_power": Reg(0x0120, Kind.I16),        # + = discharge
    "pv_total": Reg(0x0200, Kind.U32, 100),        # 0.1 kWh -> scale 100 gives Wh
}
```

- `Reg(address, kind, scale)`: `decode()` returns `raw * scale`. 32-bit values have the high word at the lower
  address. Choose `scale` so the result is already in the snapshot's unit (W, Wh, °C, %).
- Keep blocks small and contiguous (Modbus allows at most 125 registers per request; `docs/registers.md`
  recommends at most 100 for FoxESS). If the device rejects a block, `_read_block()` falls back to single reads.
- Write every open question into the docstring (`VERIFY ON HARDWARE`, as in `saj/registers.py`).

### Step 2: the driver class

```python
class AcmeDriver(ModbusDevice):
    KEY = "acme"
    DEFAULT_UNITS = (1,)

    def __init__(self, host, port=502, unit=1, *, timeout=3.0, read_attempts=3, **_ignored):
        super().__init__(host, port, unit, timeout=timeout, read_attempts=read_attempts)
        self.read_function = READ_FUNCTION
```

`registry.create()` and `detect_driver()` call `cls(host, port, unit, timeout=..., read_attempts=...)`; accept and
ignore other keyword arguments as `SajDriver` does. Only FoxESS gets extra options (`register_map`,
`read_function`), handled by a special case in `registry.py`.

### Step 3: detection and the device info

Implement `_detect()`. It must only read, and it must raise `ConnectionError` when the device is not yours:

```python
    async def _detect(self) -> DeviceInfo:
        hit = await self._probe(INFO_ADDRESS, INFO_LENGTH, (READ_FUNCTION,))
        if not hit:
            raise ConnectionError("no ACME identification block")
        words = hit[1]
        if words[0] not in DEVICE_TYPES:
            raise ConnectionError(f"unknown ACME device type 0x{words[0]:04X}")
        # plausibility: a second, independent check against other vendors' devices
        ...
        self.info = DeviceInfo(manufacturer="ACME", model=DEVICE_TYPES[words[0]], serial=ascii_text(words[2:12]) or None,
                               firmware=..., register_map="acme_x1", driver=self.KEY, unit=self._unit,
                               rated_power_w=words[1] or None, supports_control=False)
        return self.info
```

- `_probe()` tries the given function codes in order and only moves on when the device *rejects* the request.
  Transient errors propagate, and `ModbusDevice.connect()` turns them into a `ConnectionError`, so detection is
  retried later instead of guessing.
- `DeviceInfo` fields: `manufacturer`, `model` (shown next to the manufacturer, without repeating it), `serial`,
  `firmware` (the collector logs firmware changes, which may change registers), `register_map` (a name for the map
  in use), `driver` (your `KEY`), `unit`, `rated_power_w` (nominal power in W), `supports_control` and
  `energy_step_wh`: the step of the lifetime energy counters in Wh (`counter_step_wh(VALUES)` from `regs.py` derives it
  from the `*_total` registers). The app shows counter-based energy with one decimal for 100 (0.1 kWh), otherwise two.
  The recorded quarter hours can override it when the firmware counts coarser than its unit.
- `ascii_text()` in `modbus.py` decodes ASCII strings stored two characters per register.

### Step 4: `read()`

Read all blocks, decode the values into a dict, then build the snapshot in a **pure function** that can be tested
without a network, like `to_snapshot()` in both existing drivers:

```python
    async def read_raw(self) -> dict[str, float]:
        words: dict[int, int] = {}
        for start, count in BLOCKS:
            try:
                words.update(await self._read_block(start, count))
            except ModbusIllegalError:
                continue  # optional block, e.g. a second battery module
        result = {}
        for name, reg in VALUES.items():
            chunk = [words.get(reg.address + i) for i in range(reg.count)]
            if None not in chunk:  # skip values whose registers were not readable
                result[name] = decode(reg, chunk)
        return result

    async def read(self) -> Snapshot:
        return to_snapshot(await self.read_raw(), time.time())
```

Skip a rejected block only if the device may legitimately lack it (FoxESS lists these in
`RegisterMap.optional_blocks` and re-raises for all others). Transient errors must propagate unchanged so the
collector can retry.

### Step 5: sanitising

`Snapshot.sanitize()` is called by the collector after every `read()` and handles the generic cases: it drops the
"not available" sentinels in `SENTINELS` (0x7FFF, 0x8000, 0xFFFF, also scaled by 0.1 or 0.01), SoC and SoH outside
0 to 100 %, temperatures outside -40 to 120 °C (battery temperature: 90 °C), negative PV or house power and any
power beyond `MAX_PLAUSIBLE_W`. Energy counters are not touched; the storage guards them separately.

Device-specific cases belong in your `to_snapshot()`. Examples from the FoxESS driver: SoC and battery temperatures
are dropped when register 37002 says no battery is connected, and the second battery's temperatures are dropped when
they are all zero (no second module).

### Step 6: the settings methods of a read-only driver

The control features call these methods, so a read-only driver still implements them, as `SajDriver` does:

- `read_settings()` returns a `BatterySettings` with whatever is readable (or all `None`);
- `read_export_limit()` returns `ExportLimit(supported=False, rated_power_w=...)`;
- `write_work_mode`, `write_soc_limits`, `set_remote_power`, `release_remote_power` and `write_export_limit` raise
  an error (`SajDriver` uses `NotSupported`, a subclass of `ModbusIllegalError`);
- `remote_active()` returns `False`.

### Step 7: register the driver

- `registry.py`: import the class, add it to `DRIVERS` (detection order: after the existing drivers, unless there is
  a reason) and a German label to `LABELS` (shown in the setup, e.g. `"ACME X1 (nur Anzeige)"`).
- `config.py`: add the key to the `"inverter.driver"` choice in `EDITABLE` (and the comment on `InverterConfig.driver`).
- `web/src/api.ts`: add the key to the `"inverter.driver"` type.
- Documentation: a section in `docs/registers.md`, a row in the README's "Supported devices" table, the source in
  `NOTICE` (see [section 6](#6-licensing-and-sources)).

## 3. The write path (optional)

Writes can affect the device, the warranty and the grid connection. They are therefore done in a separate pull
request with the label `safety`, after the read path has been confirmed on real devices, and only with registers
that are backed by evidence:

- manufacturer documentation,
- a project with a compatible license, named in `NOTICE` and `docs/registers.md`, or
- a diagnostics report from a real device.

### What the app already guarantees

A driver's write methods are only called from `control.py` and `charging.py`, which enforce the safety rules:

- **Control switch:** nothing is written unless `control.enabled` is on (off by default). `BatteryControl.write()`
  and grid charging (`charging.py`) also require `DeviceInfo.supports_control`.
- **Test mode:** with `control.dry_run` (on by default) the intended change is logged, but not written.
- **Limits before writing:** `check_limits()` validates the SoC limits against each other and `write_order()` orders
  the writes so every intermediate state is valid. `ExportLimitControl.write()` checks the value against the rated
  power from `read_export_limit()` and the declared feed-in rule; raising it under a limit from the grid operator (or an
  unknown rule) needs the user's declaration that the operator confirmed it in writing.
- **Read-back and log:** after writing, the settings are read back and the result (`ok` or the mismatch) goes to the
  control log via `storage.log_control()`. After `VERIFY_AFTER_S` they are read again to detect a second energy
  manager overwriting them.
- **Continuous control** (charging from the grid) only uses `set_remote_power()` with a watchdog timeout
  (`REMOTE_TIMEOUT_S`), never permanently stored registers.

### What the driver has to do

- Check ranges again in the driver and raise `ValueError` (see `FoxessDriver.write_soc_limits()` and
  `set_remote_power()`): the driver is the last line before the device.
- Write only in the write methods, through `ModbusDevice._write()` (FC06 for one register, FC16 for two). Never write
  in `_detect()` or `read()`.
- `read_settings()` and `read_export_limit()` must read the very registers that are written, so the read-back means
  something.
- Let errors propagate; `control.py` logs them and turns them into German messages. A read-only Modbus proxy answers
  with "illegal function", which ends up as `ModbusIllegalError`.
- Follow the device's rules for remote control, e.g. FoxESS: timeout first, then enable, then the power setpoint;
  `release_remote_power()` only ends remote control that OpenAmpere started itself (after a restart it recognises
  OpenAmpere's leftover by the timeout it wrote, `timeout_s`) and reads back that it is off. Implement `remote_active()` so
  the app can warn when another device is in control.
- Set `supports_control=True` in the `DeviceInfo` only when the write path is implemented, tested and verified.

## 4. Developing without hardware

### The simulator

`src/openampere/simulator.py` is a small Modbus TCP server (FC03, FC04, FC06, FC16) on top of an energy model
(`EnergyModel`: PV with a sun curve and clouds, house load with spikes, battery following the work mode or a
remote-control setpoint, counters that integrate).

```bash
.venv/bin/python -m openampere.simulator --port 5020 --speed 20
```

```bash
OPENAMPERE_INVERTER_HOST=127.0.0.1 OPENAMPERE_INVERTER_PORT=5020 OPENAMPERE_SERVER_PORT=8089 .venv/bin/python -m openampere
```

| Option | Effect |
|---|---|
| `--vendor foxess\|saj\|mypv` | device family (`mypv` = my-PV heating rod) |
| `--map new\|legacy` | FoxESS register map |
| `--unit N` | the only unit id that answers (default FoxESS 247, SAJ 1); other ids get "gateway target failed" |
| `--model`, `--serial` | FoxESS model string at 30000 and serial number |
| `--speed N` | simulated seconds per real second |
| `--strict-function` | FoxESS: answer measurements only on the map's native function code |
| `--max-connections N` | connection limit (default 2) |
| `--fault-rate 0.2` | share of reads answered with "gateway target failed" (flaky Modbus proxy) |
| `--latency 1.5` | extra seconds per answer (slow proxy or network) |
| `--read-only` | FoxESS: reject writes like a read-only proxy (the SAJ simulator always does) |

To simulate your device, subclass `SimulatedInverter` the way `SajSimulatedInverter` does: create the registers of
your `BLOCKS` and the identification block in `self.regs`, and write the `EnergyModel` values into them in
`update_registers()` with `encode()` from `regs.py`. The Modbus server itself (`serve_client()`, `handle()`) is
inherited, including unit id filtering, fault rate and latency. `handle()` only accepts writes to addresses from
40000 and `on_write()` maps FoxESS settings, so a write path needs its own `on_write()`. Add a `--vendor` choice in
`main()` if others should be able to run it.

### The demo and demo data

The demo on the project page (`web/src/demo/`) simulates the API in the browser; it does not run any driver. A
driver PR normally does not touch it. Only if you add an API endpoint does it need a demo response in
`web/src/demo/server.ts` (`npm run check:demo` checks this). For history in a local database:
`.venv/bin/python -m openampere.demo_data --db data/openampere.db --days 60`.

### From a diagnostics report to a register map and a test

Users create a report in the app under **Mehr → Diagnose** and share it with the "Device report / Gerätebericht"
issue form. `diagnostics.py` only reads; the serial number is masked unless the user includes it. A report contains:

- `device`: the `DeviceInfo` (model, firmware, register map, unit id, rated power, `supports_control`);
- `connection`: connection mode and timeout;
- `checks`: for drivers that have a `map` attribute with `name`, `blocks` and `read_function` (like FoxESS's
  `RegisterMap`), which blocks are readable with FC03 and FC04; FoxESS-specific checks with raw register words
  (optional blocks, block vs. single reads, temperature scaling, export limit, remote control); the connection limit
  (optional); observed daily counter resets; disconnects per hour and counter glitches.

The diagnostics need a connected device, so for a vendor without any driver the first evidence comes from
documentation or a licensed project; the report then confirms or corrects the draft read-only driver. Turning it
into code:

1. **Identification:** model and firmware go into the device type table or the map choice, the unit id into
   `DEFAULT_UNITS`.
2. **Function code and blocks:** blocks that only answer on one function code decide `read_function`; blocks that
   are rejected on some devices become optional.
3. **Scaling and signs:** compare raw words with what the user sees on the device's display (the form asks for this)
   and fix `scale`, `Kind` and sign conversions.
4. **Pin it in a test:** put the raw word into the simulator and assert the decoded value, naming the issue, as
   `test_inverter_temperature_in_tenths_of_a_degree` in `tests/test_foxess_driver.py` does. For mapping logic, call
   `to_snapshot()` with a raw dict, as `test_saj_grid_counter_source_does_not_switch` in `tests/test_detection.py`
   does. Use made-up serial numbers and no IP addresses.
5. Update `docs/registers.md` and remove the matching `VERIFY ON HARDWARE` note.

## 5. Tests

`asyncio_mode = "auto"` is set in `pyproject.toml`, so tests are plain `async def` functions. Start simulators with
`asyncio.start_server(sim.serve_client, "127.0.0.1", 0)` (a free port) and always stop a collector in a `finally`
block. Templates:

| What | Template |
|---|---|
| Detected on every default unit id, `DeviceInfo` correct, `supports_control` false | `test_detects_saj_on_either_unit` in `tests/test_detection.py` |
| Auto-detection still picks the right vendor; forcing the wrong driver fails | `test_detects_foxess`, `test_test_connection_reports_label_and_choice` in `tests/test_detection.py` |
| A read-only driver refuses control in the app | `test_runtime_auto_driver_with_saj_is_read_only` in `tests/test_detection.py` |
| Snapshot matches the simulator: signs, SoC, counters, energy balance | `test_detect_and_read` in `tests/test_foxess_driver.py` |
| A missing optional block does not stop readings | `test_missing_optional_block_does_not_stop_readings` in `tests/test_foxess_driver.py` |
| A value confirmed on a real device | `test_inverter_temperature_in_tenths_of_a_degree` in `tests/test_foxess_driver.py` |
| `to_snapshot()` edge cases without a network | `test_saj_grid_counter_source_does_not_switch` in `tests/test_detection.py` |
| Modbus proxies: flaky, slow, read-only | `tests/test_proxy.py` |
| Write path: settings, remote control, second master, partial writes, export limit | `test_write_settings_and_remote_control` in `tests/test_foxess_driver.py`, `tests/test_settings_control.py`, `tests/test_export_limit.py` |

Run them:

```bash
.venv/bin/pytest
```

```bash
.venv/bin/pytest tests/test_detection.py -k acme
```

## 6. Licensing and sources

OpenAmpere is MIT licensed. Register addresses, scaling factors, data types and write sequences are facts; take only
these facts, never code, graphics or texts.

- Sources: manufacturer documentation, projects with a compatible license (the existing drivers use MIT and
  Apache-2.0 projects), or diagnostics reports. If you are unsure about a license, ask in the issue first.
- Credit every source in `NOTICE` (project, URL, license, copyright holder if given) and in `docs/registers.md`, and
  mention it in the docstring of `registers.py`.
- Nothing from the Ampere.IQ app or its decompiled code, see `CONTRIBUTING.md`.
- Use company and product names only to describe compatibility.

## 7. Getting it reviewed

1. **Issue first.** Open an issue (or use a device report) before writing code: which device, which source for the
   registers, read-only or with a write path. It gets the label `driver`; a write path also `safety`. Legal topics
   (feed-in limitation, § 14a EnWG, EEG) are settled in the issue before any code.
2. **Small pull requests.** The read-only driver first, the write path later in its own PR.
3. **PR title** in Conventional Commits format with the vendor as scope, e.g. `feat(acme): read ACME X1 inverters`.
4. **Checklist** from the PR template, in particular for a driver:
   - [ ] "New device / new registers" ticked; sources named in `NOTICE` and `docs/registers.md`.
   - [ ] Detection only reads and does not claim other vendors' simulators.
   - [ ] Snapshot follows the sign conventions; counters in Wh; `None` instead of guessed values.
   - [ ] Simulator and tests added; `.venv/bin/pytest` passes.
   - [ ] Driver registered in `DRIVERS`, `LABELS`, `config.py` and `web/src/api.ts`; `npm run build` passes.
   - [ ] No personal data (serial numbers, IP addresses) in code, tests or reports.
   - [ ] Write path only: the items under "Only if something is written to the inverter".
5. Say how it was tested: simulator only, or which real device and firmware.

## Other devices

Devices that are not the inverter follow other paths:

- **Heating rods:** `drivers/mypv.py` (`MyPvHeatingRod`) is a small standalone Modbus client used by the surplus
  control in `consumers.py` (consumer kinds in `KINDS`). The simulator has `SimulatedHeatingRod`
  (`--vendor mypv`), the tests are in `tests/test_heating_rod.py`.
- **Grid operators (meter readings):** see "Adding grid operators" in [CONTRIBUTING.md](../CONTRIBUTING.md).
- **Wallboxes** are handled by evcc, see [evcc.md](evcc.md).
