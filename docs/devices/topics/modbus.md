# Modbus connections, proxies and what all inverters share

> **Not official manufacturer information.** Compiled by the OpenAmpere contributors; can be incomplete or wrong. No
> affiliation with the manufacturers named here or with EKD, not legal advice. See [Device knowledge](../README.md).

**Applies to:** every inverter OpenAmpere talks to (FoxESS H3 family, SAJ H2 family), Modbus TCP proxies, gateways
and data loggers in between · **OpenAmpere:** `src/openampere/drivers/modbus.py`, `drivers/registry.py`,
`drivers/base.py`, `discovery.py`, `probe.py` · **Support:** – · **Confirmed on a real device:** partly

## Summary

- All inverters are reached over Modbus TCP, usually on port 502. Modbus TCP must be turned on at the inverter.
- A FoxESS H3 allows only very few Modbus TCP connections at a time. Other programs that read the inverter or the
  previous IQ box can take them all.
- Proxies and gateways answer with their own errors (exception 10/11), may reject multi-register writes and may mix
  up the answers of several clients; OpenAmpere retries temporary errors.
- The inverter's own consumption counter can count much more than the house used; OpenAmpere calculates consumption
  from the other flows.
- Every write follows the same safety rules, listed below as decisions.

## Behaviour

### Connections

| Fact | Status | Source |
|---|---|---|
| The inverter is reached over Modbus TCP, usually on port 502; Modbus TCP must be turned on. 1502 is another port seen on Modbus TCP devices. | confirmed (502, seen in diagnostics reports); derived (1502) | [#149](https://github.com/Gr33ndev/OpenAmpere/issues/149), [#172](https://github.com/Gr33ndev/OpenAmpere/issues/172), [#182](https://github.com/Gr33ndev/OpenAmpere/issues/182); `probe.py` |
| On the Ampere.StoragePro systems sold by EKD (SAJ and FoxESS) Modbus TCP is already on; the older EKD systems of other makers differ (see [names.md](names.md)); on other devices the installer turns it on. | derived | [#84](https://github.com/Gr33ndev/OpenAmpere/issues/84), [#85](https://github.com/Gr33ndev/OpenAmpere/issues/85); [README](../../../README.md) |
| The inverter usually needs a LAN cable to the home network; a Wi-Fi stick that only talks to the cloud is often not enough. | open (general advice, no report) | [README](../../../README.md) |
| A FoxESS H3 allows only very few Modbus TCP connections at a time. When all are taken, it accepts a new TCP connection and closes it right away, before any request. | confirmed | [#311](https://github.com/Gr33ndev/OpenAmpere/issues/311), [#312](https://github.com/Gr33ndev/OpenAmpere/issues/312) |
| Typical holders of these connections: another program reading the inverter (a Modbus setup in Home Assistant, a dashboard with its own collector, an own script, a second OpenAmpere installation), and the previous IQ box. Several reported cases were other programs, not the box. | confirmed | [#311](https://github.com/Gr33ndev/OpenAmpere/issues/311), [#312](https://github.com/Gr33ndev/OpenAmpere/issues/312); [iq-box.md](iq-box.md) |
| Typical errors: inverter not found, "the device refuses the connection" (Modbus TCP off or wrong port), "no supported device detected" (wrong device address). | confirmed | [#149](https://github.com/Gr33ndev/OpenAmpere/issues/149), [#172](https://github.com/Gr33ndev/OpenAmpere/issues/172), [#182](https://github.com/Gr33ndev/OpenAmpere/issues/182) |
| The inverter may get a new IP address from the router's DHCP. | derived | [#351](https://github.com/Gr33ndev/OpenAmpere/issues/351) |
| Settings written to the inverter stay there when OpenAmpere is uninstalled. | derived | [#172](https://github.com/Gr33ndev/OpenAmpere/issues/172), [#182](https://github.com/Gr33ndev/OpenAmpere/issues/182) |

### Proxies, gateways and data loggers

| Fact | Status | Source |
|---|---|---|
| A Modbus TCP proxy is a common way to share access when another energy manager is connected. OpenAmpere then uses the proxy's address and port, and the device address set in the proxy applies. | derived | [README](../../../README.md) |
| Unit id 0 is the Modbus broadcast address: behind a gateway every device takes a write to it. | documented | Modbus application protocol specification; [#259](https://github.com/Gr33ndev/OpenAmpere/issues/259) |
| When the inverter does not answer, proxies and gateways reply with exception 10 (gateway path unavailable) or 11 (gateway target failed to respond). | documented (codes); derived (that proxies use them so) | Modbus application protocol specification; [README](../../../README.md) |
| A read-only proxy usually answers writes with "illegal function". | derived | [README](../../../README.md); `drivers/modbus.py` |
| A proxy may answer from a cache with a shorter block than requested. | derived | `drivers/modbus.py` |
| Some gateways or proxies accept single-register writes (FC06) but reject writing several registers at once (FC16). With FoxESS grid charging this could refuse only the power after the remote control was switched on. | open (example scenario, not reported from a device) | [#258](https://github.com/Gr33ndev/OpenAmpere/issues/258), [#261](https://github.com/Gr33ndev/OpenAmpere/issues/261) |
| With a proxy or data logger that forwards the inverter, a network scan finds the same inverter twice (same name and serial number); both work. | confirmed | [#20](https://github.com/Gr33ndev/OpenAmpere/issues/20), [#45](https://github.com/Gr33ndev/OpenAmpere/issues/45) |
| An SAJ (type 0x005E) behind a proxy shared with two other integrations: one reading took about 3–22 s, the Modbus library logged mismatched transaction ids (the answer to the previous request) and now and then no answer at all. This suggests the proxy mixes up the answers of several clients. No disconnects from the inverter were recorded. | confirmed | [#336](https://github.com/Gr33ndev/OpenAmpere/issues/336), [#340](https://github.com/Gr33ndev/OpenAmpere/issues/340) |
| Slow proxies or Wi-Fi need a longer timeout per request. | derived | [README](../../../README.md) |
| The previous IQ box may set itself back to the inverter's address, so a proxy is no lasting way to share the inverter with it. | confirmed | [#311](https://github.com/Gr33ndev/OpenAmpere/issues/311); [iq-box.md](iq-box.md) |

### Readings and energy counters

| Fact | Status | Source |
|---|---|---|
| The drivers' register ranges of FoxESS and SAJ do not overlap, so asking a device of the other make only gives Modbus rejections. | derived | `drivers/registry.py`; [docs/devices.md](../../devices.md) |
| A device can reject a block read although single registers in it work, for example while it is booting. | derived | `drivers/modbus.py` |
| 0x7FFF, 0x8000 and 0xFFFF are used by devices for "not available". | derived | `drivers/base.py` |
| An energy counter can briefly read 0 (device booting, proxy cache). | derived | `storage.py` |
| The inverter's own consumption (load) counter can count much more than the house used: on one system its daily value was about 50 % higher than the balance of generation, grid import, feed-in, battery charge and discharge, while those five matched the manufacturer's app, which shows the balance. | confirmed (one system) | [#346](https://github.com/Gr33ndev/OpenAmpere/issues/346), [#349](https://github.com/Gr33ndev/OpenAmpere/issues/349) |
| Energy counters with 0.1 kWh steps tick at different moments, so a quarter hour of a balance calculated from them can come out negative. | derived | [#346](https://github.com/Gr33ndev/OpenAmpere/issues/346), [#349](https://github.com/Gr33ndev/OpenAmpere/issues/349) |
| Some devices' energy counters advance only in steps of 100 Wh, which looked like a false second decimal. | confirmed | [#194](https://github.com/Gr33ndev/OpenAmpere/issues/194), [#195](https://github.com/Gr33ndev/OpenAmpere/issues/195) |
| Without a fault, all fault words of the inverter read 0; only a non-zero code is a fault. Seen on an SAJ-based "Ampere.StoragePro E2". | confirmed | [#330](https://github.com/Gr33ndev/OpenAmpere/issues/330), [#332](https://github.com/Gr33ndev/OpenAmpere/issues/332) |
| The battery may allow less charge power than the inverter's rated power. | derived | [#23](https://github.com/Gr33ndev/OpenAmpere/issues/23) |

## Decisions in OpenAmpere

### Connection and detection

| Decision | Why | Issue/PR |
|---|---|---|
| Detection only reads: FoxESS at unit 247, then SAJ at units 1 and 2, then Solarmax at unit 1; the first match wins. If nothing listens at host and port, other makes are not tried. The detection timeout is at least 2.5 s. | Reading is harmless; the ranges do not overlap. | [docs/devices.md](../../devices.md); `drivers/registry.py` |
| One persistent connection per inverter, requests one after the other with a short pause (0.03 s). Optionally "per poll": connect for each poll and release afterwards. | Inverter stability; "per poll" helps when another energy manager releases its connection now and then (not the IQ box). | [#172](https://github.com/Gr33ndev/OpenAmpere/issues/172), [#182](https://github.com/Gr33ndev/OpenAmpere/issues/182), [#311](https://github.com/Gr33ndev/OpenAmpere/issues/311) |
| A connection that is accepted and closed at once is reported as "currently busy", with a message that names other programs first and the IQ box second. | Matches the reported causes. | [#311](https://github.com/Gr33ndev/OpenAmpere/issues/311), [#312](https://github.com/Gr33ndev/OpenAmpere/issues/312) |
| Only exceptions 1, 2 and 3 (illegal function, address, value) count as permanent. Timeouts, 4, 5, 6, 10 and 11 and shortened answers are temporary: up to 3 attempts 0.3 s apart, and the connection counts as lost only after several failures in a row. | Proxies report a short gap of the inverter as an error. | [README](../../../README.md); `drivers/modbus.py` |
| A rejected block read falls back to single registers; a rejected address is skipped for 600 s and then tried again. | The device may have been booting. | `drivers/modbus.py` |
| A read-only proxy's rejection of a write is shown as an understandable message. | Owners behind such proxies otherwise see only "illegal function". | [README](../../../README.md) |
| The network scan shows the last digits of the serial number and explains that a second hit is probably a proxy or data logger. | Owners did not know which hit to choose. | [#20](https://github.com/Gr33ndev/OpenAmpere/issues/20), [#45](https://github.com/Gr33ndev/OpenAmpere/issues/45) |
| After 180 s without a connection, OpenAmpere searches the /24 network for the same serial number (at most every 30 minutes) and switches over. A fixed IP address in the router is still recommended. | DHCP may give the inverter a new address. | [#351](https://github.com/Gr33ndev/OpenAmpere/issues/351) |
| Two OpenAmpere installations must never talk to the same inverter; moving from Docker to the Home Assistant app goes through a backup and stopping the old installation first. Modbus TCP works unchanged from the app's network. | Connection slots and double control. | [#300](https://github.com/Gr33ndev/OpenAmpere/issues/300), [#304](https://github.com/Gr33ndev/OpenAmpere/issues/304) |
| For slow inverters, the external `/state` waits at most 5 s for the battery settings and otherwise answers with the last known ones; readings count as stale for other apps after 60 s (OpenAmpere's own control: 30 s); the Home Assistant integration bridges up to 3 failed state queries. | A shared proxy made single readings take up to about 20 s. | [#336](https://github.com/Gr33ndev/OpenAmpere/issues/336), [#340](https://github.com/Gr33ndev/OpenAmpere/issues/340), [#342](https://github.com/Gr33ndev/OpenAmpere/issues/342), [#343](https://github.com/Gr33ndev/OpenAmpere/issues/343) |
| The device reader in the diagnostics (Mehr → Diagnose → Gerät auslesen) reads only (FC03/FC04), at most 600 registers per run in blocks of 60 with 0.5 s pauses over one connection, and checks ports 502, 1502, 80, 443 and 8080. The configured inverter is read only over OpenAmpere's existing connection with its own unit id. | Some devices accept only one Modbus client. | [#324](https://github.com/Gr33ndev/OpenAmpere/issues/324), [#326](https://github.com/Gr33ndev/OpenAmpere/issues/326) |

### Readings and counters

| Decision | Why | Issue/PR |
|---|---|---|
| 0x7FFF, 0x8000, 0xFFFF (also scaled) count as not available; powers beyond ±100 kW, SoC or SoH outside 0–100 % and temperatures outside −40 to 120 °C are dropped. | Implausible values must not reach the charts or the control. | `drivers/base.py` |
| An energy counter that drops to 0 is ignored for the energy accounting. | Booting device or proxy cache. | `storage.py` |
| Consumption = PV + grid import + battery discharge − feed-in − battery charge. The inverter's own consumption counter stays only as a diagnostic Home Assistant sensor; the former "inverter losses" figure was this counter error and was removed. | The counter counts too much; the balance matches the manufacturer's app. | [#346](https://github.com/Gr33ndev/OpenAmpere/issues/346), [#349](https://github.com/Gr33ndev/OpenAmpere/issues/349) |
| A negative quarter hour of consumption is carried into the next quarters (at most 1 kWh), so no quarter is negative and the sums stay exact. | Counters with 0.1 kWh steps tick at different moments. | [#349](https://github.com/Gr33ndev/OpenAmpere/issues/349) |
| Counter step detection: from the last 7 days of locally recorded quarter-hour counter differences (imported history and quarters filled after an outage are ignored), if at least 20 non-zero differences are all multiples of 100 Wh the step is 100 Wh, else 10 Wh; unknown with too little data; cached for an hour. It overrides the driver's hint; `/api/status` reports `energy_step_wh` and the app shows one decimal at 100 Wh. | Coarse counters showed a false second decimal; also covers firmware that counts coarser than its unit. | [#194](https://github.com/Gr33ndev/OpenAmpere/issues/194), [#195](https://github.com/Gr33ndev/OpenAmpere/issues/195) |
| The drivers always deliver every fault word; only a non-zero code is a fault. The Home Assistant alarm sensor showed "Problem" for any non-empty list until 0.25.0; its `codes` attribute keeps all raw words so the position stays visible. | A list of zeros is no fault. | [#330](https://github.com/Gr33ndev/OpenAmpere/issues/330), [#332](https://github.com/Gr33ndev/OpenAmpere/issues/332) |

### Writing to an inverter (safety)

| Decision | Why | Issue/PR |
|---|---|---|
| Writes only through the control features: master switch and test mode respected, the device's `supports_control` checked, limits checked before writing, the value read back afterwards, every change in the control log. | Writes can affect devices, warranties and grid connection rules. | [AGENTS.md](../../../AGENTS.md) |
| The export limit is refused for a device with `supports_control` off, like battery settings and grid charging, not only when the driver reports the limit as unsupported. | The export limit is part of the grid connection approval; it needs a second safeguard. | [#113](https://github.com/Gr33ndev/OpenAmpere/issues/113), [#119](https://github.com/Gr33ndev/OpenAmpere/issues/119) |
| Never write to unit id 0 or above 247; consumers accept only 1–247. | Unit id 0 is the broadcast. | [#259](https://github.com/Gr33ndev/OpenAmpere/issues/259), [#262](https://github.com/Gr33ndev/OpenAmpere/issues/262) |
| Continuous control (grid charging) only through the inverter's remote control with a watchdog timeout, never through permanently stored registers; release only what OpenAmpere started. SAJ, which has no such remote control, uses a short charge slot that ends by itself. | If OpenAmpere stops, the inverter falls back by itself. | [#141](https://github.com/Gr33ndev/OpenAmpere/issues/141), [#377](https://github.com/Gr33ndev/OpenAmpere/issues/377); [foxess/h3.md](../foxess/h3.md), [saj/h2-family.md](../saj/h2-family.md) |
| Switching off is the safe direction and is written even with the master switch off or in test mode. | Leaving something on is the risk. | [#217](https://github.com/Gr33ndev/OpenAmpere/issues/217), [#243](https://github.com/Gr33ndev/OpenAmpere/issues/243) |
| A value that could not be read back (a single timeout) is ignored when checking battery settings or the export limit, so it is not reported as overwritten; a failed read-back after an export-limit write is still logged ("Rücklesen fehlgeschlagen") and the later check still runs. | A timeout is not an overwrite by another device. | [#219](https://github.com/Gr33ndev/OpenAmpere/issues/219), [#226](https://github.com/Gr33ndev/OpenAmpere/issues/226) |
| Stored grid charging power is lowered (not refused) to the inverter's rated power and the battery's charge limit; below 200 W the setting counts as off. | The battery may allow less than the inverter. | [#23](https://github.com/Gr33ndev/OpenAmpere/issues/23), [#351](https://github.com/Gr33ndev/OpenAmpere/issues/351), [#363](https://github.com/Gr33ndev/OpenAmpere/issues/363) |
| Writes from other apps (Home Assistant) are limited per battery setting to a number per hour; the write times survive a restart. | Protects the inverter's memory from frequent writes. | [#351](https://github.com/Gr33ndev/OpenAmpere/issues/351), [#352](https://github.com/Gr33ndev/OpenAmpere/issues/352), [#363](https://github.com/Gr33ndev/OpenAmpere/issues/363), [#366](https://github.com/Gr33ndev/OpenAmpere/issues/366) |
| Owners are told to switch off grid charging before they uninstall OpenAmpere. | Written settings stay in the inverter. | [#172](https://github.com/Gr33ndev/OpenAmpere/issues/172), [#182](https://github.com/Gr33ndev/OpenAmpere/issues/182) |

## Open questions

- Whether real gateways reject multi-register writes while accepting single ones; so far only an example scenario
  ([#258](https://github.com/Gr33ndev/OpenAmpere/issues/258)).
- Whether the consumption counter error applies to every device and firmware or only to some; seen on one system
  ([#346](https://github.com/Gr33ndev/OpenAmpere/issues/346)). See also the conflict on [iq-box.md](iq-box.md).
- How many writes per hour an inverter's memory tolerates: no manufacturer figure is known; the limits for other apps
  are a cautious choice ([#352](https://github.com/Gr33ndev/OpenAmpere/issues/352)).
- How many Modbus TCP connections a FoxESS H3 allows exactly, and how many an SAJ allows, is not known.
- Whether a cloud-only Wi-Fi stick can ever be used; only general advice so far.

## Sources

- Modbus application protocol specification (Modbus Organization): exception codes, broadcast address.
- Issues and pull requests: [#20](https://github.com/Gr33ndev/OpenAmpere/issues/20),
  [#23](https://github.com/Gr33ndev/OpenAmpere/issues/23), [#45](https://github.com/Gr33ndev/OpenAmpere/issues/45),
  [#84](https://github.com/Gr33ndev/OpenAmpere/issues/84), [#85](https://github.com/Gr33ndev/OpenAmpere/issues/85),
  [#113](https://github.com/Gr33ndev/OpenAmpere/issues/113), [#119](https://github.com/Gr33ndev/OpenAmpere/issues/119),
  [#141](https://github.com/Gr33ndev/OpenAmpere/issues/141), [#149](https://github.com/Gr33ndev/OpenAmpere/issues/149),
  [#172](https://github.com/Gr33ndev/OpenAmpere/issues/172), [#182](https://github.com/Gr33ndev/OpenAmpere/issues/182),
  [#194](https://github.com/Gr33ndev/OpenAmpere/issues/194), [#195](https://github.com/Gr33ndev/OpenAmpere/issues/195),
  [#217](https://github.com/Gr33ndev/OpenAmpere/issues/217), [#219](https://github.com/Gr33ndev/OpenAmpere/issues/219),
  [#226](https://github.com/Gr33ndev/OpenAmpere/issues/226), [#243](https://github.com/Gr33ndev/OpenAmpere/issues/243),
  [#258](https://github.com/Gr33ndev/OpenAmpere/issues/258), [#259](https://github.com/Gr33ndev/OpenAmpere/issues/259),
  [#261](https://github.com/Gr33ndev/OpenAmpere/issues/261), [#262](https://github.com/Gr33ndev/OpenAmpere/issues/262),
  [#300](https://github.com/Gr33ndev/OpenAmpere/issues/300), [#304](https://github.com/Gr33ndev/OpenAmpere/issues/304),
  [#311](https://github.com/Gr33ndev/OpenAmpere/issues/311), [#312](https://github.com/Gr33ndev/OpenAmpere/issues/312),
  [#324](https://github.com/Gr33ndev/OpenAmpere/issues/324), [#326](https://github.com/Gr33ndev/OpenAmpere/issues/326),
  [#330](https://github.com/Gr33ndev/OpenAmpere/issues/330), [#332](https://github.com/Gr33ndev/OpenAmpere/issues/332),
  [#336](https://github.com/Gr33ndev/OpenAmpere/issues/336), [#340](https://github.com/Gr33ndev/OpenAmpere/issues/340),
  [#342](https://github.com/Gr33ndev/OpenAmpere/issues/342), [#343](https://github.com/Gr33ndev/OpenAmpere/issues/343),
  [#346](https://github.com/Gr33ndev/OpenAmpere/issues/346), [#349](https://github.com/Gr33ndev/OpenAmpere/issues/349),
  [#351](https://github.com/Gr33ndev/OpenAmpere/issues/351), [#352](https://github.com/Gr33ndev/OpenAmpere/issues/352),
  [#363](https://github.com/Gr33ndev/OpenAmpere/issues/363), [#366](https://github.com/Gr33ndev/OpenAmpere/issues/366),
  [#377](https://github.com/Gr33ndev/OpenAmpere/issues/377).
- Code: `src/openampere/drivers/modbus.py`, `drivers/registry.py`, `drivers/base.py`, `discovery.py`, `probe.py`,
  `storage.py`, `charging.py`, `control.py`, `external.py`.
