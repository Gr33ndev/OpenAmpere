Deutsch: [devices.de.md](devices.de.md)

# Supported devices

Which devices OpenAmpere works with, what it reads and controls, and what has already been confirmed on a real
system. The page only lists what the code, [registers.md](registers.md) and the issues show.

**How to read this page:**

- **Confirmed** means a user checked it on a real system and the issue says so. The issue number is given.
- **Not yet confirmed** means the support is built from community documentation or the manufacturer's interface
  description and is covered by tests and the simulator, but nobody has reported it from a real system yet. It may
  well work. Please tell us, see [below](#your-device-is-missing-or-behaves-differently).
- Control is **off by default** for every inverter ("Nur ansehen", view only). Under **Mehr → Steuerung und Protokoll**
  (More → Control and log) you switch it to "Testen" (test) or "Aktiv" (active).

## Inverters and batteries

All inverters are connected via Modbus TCP, default port 502. Setup detects the device by reading only, in this
order: FoxESS at device address (unit id) 247, then SAJ at 1 and 2.

| Device / model | Sold by EKD as | Register map / detection (unit id) | Read | Control | Tested on real hardware | Notes |
|---|---|---|---|---|---|---|
| FoxESS H3, newer firmware | "Ampere.StoragePro E3" (see notes) | `foxess_h3_new`; chosen when the energy counters at 39601 answer (unit 247) | Yes | Yes: work mode, backup power reserve and limits, remote control for grid charging, export limit | Reading: confirmed (#11, #16). Control: confirmed in part (#25) | Which map the E3 uses is still open in [registers.md](registers.md). #11 confirmed the new map on an H3; the issue form's device choice covers H3, H3 Smart, H3 Pro and the E3, so the exact variant is not stated. The export limit (46616) is known from community documentation for the H3 Smart and is not yet confirmed on a real E3. |
| FoxESS H3, older firmware | not stated in the sources | `foxess_h3_legacy`; chosen when the new map does not answer but the counters at 32000 do (unit 247) | Yes | Yes: work mode, backup power reserve and limits, remote control for grid charging. No export limit (no known register) | not yet confirmed | Battery health (SoH, 31090) needs firmware 1.80 or newer. House consumption is the sum of the three phases (no total register). |
| FoxESS H3 Smart (model string `H3-…-Smart` or `H3-…-M`) | not stated in the sources | `foxess_h3_new` (unit 247) | Yes | Yes, as above | not yet confirmed | The export limit registers come from community documentation for this model. |
| FoxESS H3 Pro (model string `H3-Pro-…` or `P3-Pro-…`) | not stated in the sources | `foxess_h3_new` (unit 247) | Yes | Yes, as above | not yet confirmed | |
| SAJ H2 (type code 0x005A, 0x005B), SAJ HS2 (0x005C, 0x005D) | "Ampere.StoragePro" | `saj_h2`; identification block 0x8F00 plus a plausibility check of work mode and state of charge (unit 1 or 2, both are tried) | Yes | No, deliberately locked | not yet confirmed | Control registers are known from community documentation but unverified, so control stays off until the driver has been checked on real devices. Still to verify: the signs of battery and grid power, and whether the PV counter covers all MPPTs. Some firmware lacks the summed grid counters (0x4167); then the L1 counters are used. |
| SAJ AS2 (type code 0x0055, 0x0056) and other codes in the SAJ family range 0x0050–0x006F | not stated in the sources | `saj_h2`, as above. Unknown codes in the family range are accepted with a warning in the log | Yes | No | not yet confirmed | Read with the same register map as the H2 and HS2. |

More about the FoxESS H3 family:

- **The model string is only a hint.** The driver always checks which register map really answers and prefers the
  newer one. Whether the measurement registers are read as input registers (FC04) or holding registers (FC03) is
  also detected automatically, because the sources disagree.
- If detection gets it wrong, you can choose the register map and the read method yourself under
  **Mehr → Verbindung → Für Experten** (More → Connection → For experts).
- **Settings written by OpenAmpere can be reset** by a previous smartbox that fetches its targets from the cloud.
  In #25 a user kept changes to the backup power reserve and price-based grid charging after blocking the smartbox's
  internet access. See "The previous Smartbox resets settings" in the [README](../README.md).
- **The lower limit in backup mode may be 0 %** (#69). Whether every device and firmware accepts 0 % is not yet
  confirmed.
- **Off-grid detection** uses bit 0 of register 39065. In #89 a single reading had the bit set while the system was
  feeding into the grid, so OpenAmpere now needs two readings in a row and ignores the bit while the grid meter shows
  import or export. In #93 an inverter that was switched off on purpose had been counted as a power outage.
- **Temperatures:** register 39141 (inverter) counts 0.1 °C, checked against another reader (#11). The "battery"
  temperature from 37611 is the temperature of the BMS electronics, not of the cells (#16).

Register details and sources: [registers.md](registers.md) and [NOTICE](../NOTICE).

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

## Wallboxes

| Device | Connection | Read | Control | Tested on real hardware | Notes |
|---|---|---|---|---|---|
| Wallboxes and vehicles supported by [evcc](https://evcc.io) | evcc's REST API; evcc gets grid, solar and battery readings from OpenAmpere (`/api/evcc/site`) | Charge points, charging power, sessions; state of charge and range if the car is set up in evcc | Charging mode, charging target, minimum charge, charging plan, home battery priority | not yet confirmed | OpenAmpere does not control wallboxes itself; which wallboxes work is decided by evcc, see the [evcc documentation](https://docs.evcc.io). Setup: [evcc.md](evcc.md). |

## Your device is missing or behaves differently?

1. In the app, open **Mehr → Diagnose → Diagnose starten** (More → Diagnostics → Start diagnostics). The diagnostics
   only read and change nothing.
2. Tap **Bericht kopieren** (Copy report) and paste it into the
   [device report form](https://github.com/Gr33ndev/OpenAmpere/issues/new?template=device_report.yml). Please
   remove IP addresses, passwords and API keys. German is welcome.

Each report helps to move a row from "not yet confirmed" to "confirmed". If you want to add a new device yourself, see
[writing-a-driver.md](writing-a-driver.md).
