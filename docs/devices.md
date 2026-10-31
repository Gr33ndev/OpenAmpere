Deutsch: [devices.de.md](devices.de.md)

# Supported devices

Which devices OpenAmpere works with, what it reads and controls, and what has already been confirmed on a real
system. The page only lists what the code, the [device pages](devices/README.md) and the issues show.

**How to read this page:**

- **Confirmed** means a user checked it on a real system and the issue says so. The issue number is given.
- **Not yet confirmed** means the support is built from community documentation or the manufacturer's interface
  description and is covered by tests and the simulator, but nobody has reported it from a real system yet. It may
  well work. Please tell us, see [below](#your-device-is-missing-or-behaves-differently).
- Control is **off by default** for every inverter ("Nur ansehen", view only). Under **Mehr → Steuerung und Protokoll**
  (More → Control and log) you switch it to "Testen" (test) or "Aktiv" (active).

## Inverters and batteries

All inverters are connected via Modbus TCP, default port 502. Setup detects the device by reading only, in this
order: FoxESS at device address (unit id) 247, then SAJ at 1 and 2, then Solarmax at 1, then M-TEC at 252, 255 and 247.

| Device / model | Sold by EKD as | Register map / detection (unit id) | Read | Control | Tested on real hardware | Notes |
|---|---|---|---|---|---|---|
| FoxESS H3, newer firmware | "Ampere.StoragePro E3" (see notes) | `foxess_h3_new`; chosen when the energy counters at 39601 answer (unit 247) | Yes | Yes: work mode, backup power reserve and limits, remote control for grid charging, export limit | Reading: confirmed (#11, #16). Control: confirmed (#25, #196) | Which map the E3 uses is still open in [h3.md](devices/foxess/h3.md). #11 confirmed the new map on an H3; the issue form's device choice covers H3, H3 Smart, H3 Pro and the E3, so the exact variant is not stated. The export limit (46616) is known from community documentation for the H3 Smart; it is confirmed on a real system with the new map (#196). |
| FoxESS H3, older firmware | not stated in the sources | `foxess_h3_legacy`; chosen when the new map does not answer but the counters at 32000 do (unit 247) | Yes | Yes: work mode, backup power reserve and limits, remote control for grid charging. No export limit (no known register) | not yet confirmed | Battery health (SoH, 31090) needs firmware 1.80 or newer. House consumption is the sum of the three phases (no total register). |
| FoxESS H3 Smart (model string `H3-…-Smart` or `H3-…-M`) | not stated in the sources | `foxess_h3_new` (unit 247) | Yes | Yes, as above | not yet confirmed | The export limit registers come from community documentation for this model. |
| FoxESS H3 Pro (model string `H3-Pro-…` or `P3-Pro-…`) | not stated in the sources | `foxess_h3_new` (unit 247) | Yes | Yes, as above | not yet confirmed | |
| SAJ H2 (type code 0x005A, 0x005B), SAJ HS2 (0x005C, 0x005D), "3P-X E2" (0x005E, three-phase) | "Ampere.StoragePro" | `saj_h2`; identification block 0x8F00 plus a plausibility check of work mode and state of charge (unit 1 or 2, both are tried) | Yes | Yes: battery settings (self-use and backup only, with the reserve of the backup mode), export limit, grid charging through a charge slot (#377) | Reading: confirmed on a 0x005E "3P-X E2" (#334, #339, #379). Control: not yet confirmed; an owner uses OpenAmpere on a 0x005E in daily operation without problems (owner's report, 2026-10), whether and which writes were made is not recorded. H2 and HS2 (0x005A–0x005D): not yet confirmed | Control registers from SAJ's H2 protocol, written the same way by home-assistant-saj-h2-modbus and evcc. SAJ has no remote control that ends by itself: grid charging uses a short charge slot that OpenAmpere extends (see [h2-family.md](devices/saj/h2-family.md)). Still to verify: the signs of battery and grid power, and whether the PV counter covers all MPPTs. Some firmware lacks the summed grid counters (0x4167); then the L1 counters are used. A second PV inverter measured by Meter B (0x40A3) can be counted under "Meine Anlage" (#419). If the rated power the inverter reports cannot be checked against its type (outside its power class, or no class, as for 0x005E), OpenAmpere changes the export limit only after the owner confirmed the rated power from the type plate in the app (#492). While the previous IQ box is in the network, it keeps the Modbus connection busy; OpenAmpere cannot connect until a few minutes after the box is cut from the network; which message it shows meanwhile is not known (#469). |
| SAJ AS2 (type code 0x0055, 0x0056) and other codes in the SAJ family range 0x0050–0x006F | not stated in the sources | `saj_h2`, as above. Unknown codes in the family range are accepted with a warning in the log | Yes | No | not yet confirmed | Read with the same register map as the H2 and HS2. |
| Solarmax MAX.STORAGE, MAX.STORAGE Ultimate | "AMPERE.Storage" (the 10 kW Ultimate) | `solarmax_max_storage`; input registers 100–126 (FC04) with a plausibility check of firmware, device state, nominal power and state of charge (unit 1) | Yes: PV, battery, house, grid, state of charge; energy counters only with "Connectivity+" | No | not yet confirmed | Built from Solarmax's register list (#519). 32-bit values with the low word first; Solarmax counts charging and export as positive, the driver turns them around. Without "Connectivity+" there is no energy history. OpenAmpere never writes: EKD switched the storage function off remotely in 2025. Details on the [device page](devices/solarmax/max-storage.md). |
| M-TEC Energy-Butler GEN3 | M-TEC Energy-Butler (from late 2022) | `mtec_gen3`; serial number, running state and state of charge (units 252, 255, 247; plain Modbus TCP, then RTU frames over TCP). Port 502, or 5743 on older module firmware | Yes: PV (2 inputs), battery, house, grid, state of charge and health, energy counters | No | not yet confirmed | Built from M-TEC's register document and MTECmqtt (#520). Details on the [device page](devices/mtec/energy-butler.md). |

More about the FoxESS H3 family:

- **The model string is only a hint.** The driver always checks which register map really answers and prefers the
  newer one. Whether the measurement registers are read as input registers (FC04) or holding registers (FC03) is
  also detected automatically, because the sources disagree.
- If detection gets it wrong, you can choose the register map and the read method yourself under
  **Mehr → Verbindung → Für Experten** (More → Connection → For experts).
- **Very few Modbus TCP connections at a time** (#311): when other programs or
  the previous IQ box take all of them, the inverter accepts OpenAmpere's connection and closes it right away. OpenAmpere reports this as "gerade belegt" (currently busy). The
  fix is in the [README](../README.md) under "Der Wechselrichter lässt gerade keine weitere Verbindung zu".
- **Settings written by OpenAmpere can be reset** by a previous smartbox that fetches its targets from the cloud.
  In #25 a user kept changes to the backup power reserve and price-based grid charging after blocking the smartbox's
  internet access. See "The previous Smartbox resets settings" in the [README](../README.md).
- **The lower limit in backup mode may be 0 %** (#69). Whether every device and firmware accepts 0 % is not yet
  confirmed.
- **The backup power reserve may be below 10 %, but only after a warning** (#313) that appears every time it is set
  below 10 %. While it is below 10 %, the app shows a warning. At a real system (FoxESS H3) the inverter accepted and
  kept a value below 10 %. Other apps (Home Assistant) stay at 10 % or more.
- **Off-grid detection** uses bit 0 of register 39065. In #89 a single reading had the bit set while the system was
  feeding into the grid, so OpenAmpere now needs two readings in a row and ignores the bit while the grid meter shows
  import or export. In #93 an inverter that was switched off on purpose had been counted as a power outage.
- **Temperatures:** register 39141 (inverter) counts 0.1 °C, checked against another reader (#11). The "battery"
  temperature from 37611 is the temperature of the BMS electronics, not of the cells (#16).

Registers, behaviour and sources per device: [docs/devices/](devices/README.md) and [NOTICE](../NOTICE).

## Immersion heaters

| Device / model | Connection | Read | Control | Tested on real hardware | Notes |
|---|---|---|---|---|---|
| my-PV AC ELWA-E, AC ELWA 2, AC THOR | Modbus TCP, port 502, unit id 1 | Power setpoint (1000), water temperature (1001), target temperature (1002), status (1003) | Power setpoint only (register 1000), continuously by solar surplus, optionally with cheap grid power | not yet confirmed | In the heater's web interface set the control type to "Modbus TCP" and the control timeout a little longer than OpenAmpere's interval; without a new value the heater switches off. OpenAmpere writes no other register, as the manufacturer asks not to write others frequently. Tested with a simulator. |

## Relays and switched devices

For immersion heaters without Modbus, heat pumps and other loads. These devices are only switched on or off, with
a minimum run time and a minimum pause time. OpenAmpere does not read their power; it uses the power you enter.

| Device | How OpenAmpere switches it | Tested on real hardware | Notes |
|---|---|---|---|
| Shelly Gen1 relays | `http://<address>/relay/<channel>?turn=on` or `off` | not yet confirmed | Channel selectable. |
| Shelly Gen2 and newer | `http://<address>/rpc/Switch.Set?id=<channel>&on=true` or `false` | not yet confirmed | Channel selectable. |
| Any device with web addresses | One address for on, one for off (http or https) | not yet confirmed | Any device in the home network that can be switched by calling an address. |
| Heat pump with SG-Ready input | Via a relay above that switches the SG-Ready contact | not yet confirmed | OpenAmpere only switches the relay; what the heat pump does with the signal is set on the heat pump. |

**A relay that OpenAmpere switched on stays on while OpenAmpere is stopped or uninstalled.** When it stops, OpenAmpere
only sets adjustable devices (such as the immersion heaters above) to 0 W. OpenAmpere gives relays no time limit: they keep their
state until OpenAmpere runs again and switches them as needed. Before you stop or uninstall OpenAmpere, set these
devices to **Aus** (Off) on the **Geräte** (Devices) page.

## Wallboxes

| Device | Connection | Read | Control | Tested on real hardware | Notes |
|---|---|---|---|---|---|
| Wallboxes and vehicles supported by [evcc](https://evcc.io) | evcc's REST API; evcc gets grid, solar and battery readings from OpenAmpere (`/api/evcc/site`) | Charge points, charging power, sessions; state of charge and range if the car is set up in evcc | Charging mode, charging target, minimum charge, charging plan, home battery priority | not yet confirmed | Which wallboxes work is decided by evcc, see the [evcc documentation](https://docs.evcc.io). Setup: [evcc.md](evcc.md). |
| Portable 11 kW wallbox sold as "dé" (Tuya Wi-Fi) | Local Tuya protocol 3.5 in the home network (TCP 6668); needs the device's local key from Tuya's developer platform, no cloud afterwards | State, charging power, current per phase, set current, energy of the session | Charging mode (manual, off, solar, minimum + solar, fast), current in the wallbox's steps; several wallboxes | reading yes, control not yet | Without evcc. Often unplugged: shown as not reachable, reconnects by itself. Setup: [wallbox-direct.md](wallbox-direct.md). Details: [devices/tuya/de-portable.md](devices/tuya/de-portable.md). |
| FoxESS AC EV Charger (also the wallbox of EKD systems) | Modbus TCP in the home network (port 502, unit ID 1), read only except the work mode and, with real control, the current and start/stop; one short connection per read | State, charging power, current per phase, highest current, how it starts charging | its work mode on request (e.g. to unlock it, #464); charging only as a test after the owner agrees (#465): start, stop, current, 1/3 phases (#478) | reading partly (idle, one charger) | Without evcc, never together with evcc. On EKD systems the previous smartbox must stay cut from power. Setup: [wallbox-direct.md](wallbox-direct.md). Details: [devices/foxess/ac-charger.md](devices/foxess/ac-charger.md). |
| Weidmüller AC SMART (EKD sold the VALUE as "AMPERE.Charge") | Modbus TCP in the home network (port 502, unit ID 255), one short connection per read; the wallbox takes only one Modbus client | State, charging power, current and voltage per phase, energy and duration of the charge, current limits, firmware | No (only the mode "manual") | not yet confirmed | Built from Weidmüller's public register document (#523). Values of 4 bytes with the low word first. While evcc or the previous smartbox holds its Modbus connection, OpenAmpere cannot read it. Details on the [device page](devices/weidmueller/ac-smart.md). |

## Cars

| Car | Connection | Read | Control | Tested on real hardware | Notes |
|---|---|---|---|---|---|
| Volkswagen, Audi, Škoda, SEAT, CUPRA | The Volkswagen group's EU Data Act portal with the owner's brand account (cloud, optional); needs a one-time setup in a browser | State of charge, range, odometer, plug and charging state, every 15 minutes and at least 15 minutes late; charges at a wallbox OpenAmpere controls | none (read-only) | not yet confirmed | Setup: [car.md](car.md). Details: [devices/cars/vw-eu-data-act.md](devices/cars/vw-eu-data-act.md). |

## Your device is missing or behaves differently?

1. In the app, open **Mehr → Diagnose → Diagnose starten** (More → Diagnostics → Start diagnostics). The diagnostics
   only read and change nothing.
2. Tap **Bericht kopieren** (Copy report) and paste it into the
   [device report form](https://github.com/Gr33ndev/OpenAmpere/issues/new?template=device_report.yml). Please
   remove IP addresses, passwords and API keys. German is welcome.

Each report helps to move a row from "not yet confirmed" to "confirmed". If you want to add a new device yourself, see
[writing-a-driver.md](writing-a-driver.md).
