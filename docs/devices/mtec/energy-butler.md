# M-TEC Energy-Butler GEN3

> Not official manufacturer information: compiled by the OpenAmpere contributors, can be incomplete or wrong. No
> affiliation with M-TEC, Solinteg or EKD; not legal advice. See the [device knowledge README](../README.md).

**Applies to:** M-TEC Energy-Butler GEN3 hybrid inverters (a Solinteg inverter, also sold as Wattsonic, Sunways,
A-Tronix, SunEnergyBox) · **Sold as:** M-TEC Energy-Butler (EKD from late 2022) · **OpenAmpere:** driver `mtec`
(`src/openampere/drivers/mtec/`), register map `mtec_gen3` · **Support:** display only · **Confirmed on a real
device:** no (built from M-TEC's register document, open projects and the simulator)

## Summary

- Read through the Wi-Fi/LAN module: **port 502** on newer module firmware (V27.52.4.0 and later), **5743** before.
- Unit id and framing differ between the sources: OpenAmpere tries **plain Modbus TCP, then RTU frames over TCP**, on
  units **252, 255 and 247**, and shows the one that answered in the register map (`mtec_gen3 (socket)` or `(rtu)`).
- Holding registers (FC03), 32-bit values with the high word first; energy counters in 0.1 kWh, so OpenAmpere has an
  energy history for this device.
- **Signs:** grid power is + for export (turned around), battery power + for discharging (as OpenAmpere).
- M-TEC still serves owners (app and portal); OpenAmpere only reads.
- GEN2 uses other addresses (4xxxx) and is not covered; the GEN3 registers need inverter firmware 3.04.01 or later.

## Identification

- Type plate: "Energy-Butler 10kW-3P-3G25" (three-phase, GEN3); from 2023-07 the 10 kW model is "3G40", so EKD's units
  from 2022/23 are most likely 3G25 (derived: M-TEC datasheet 2025, owners' posts).
- GEN2 has RS485 only and the 4xxxx registers (documented: M-TEC document).
- Firmware on the display, in the app or in 10011–10014.

## Registers

All holding registers (FC03).

| Register | Meaning | Type, scale, sign | R/W | Status | Source |
|---|---|---|---|---|---|
| 10000–10007 | Serial number | ASCII | R | documented | M-TEC document; MTECmqtt |
| 10008 | Equipment info (model code, high byte = series) | U16 | R | derived | MTECmqtt |
| 10011–10014 | Firmware, two versions of four bytes | bytes | R | derived | MTECmqtt |
| 10105 | Running state: 0 wait, 1 check, 2 on-grid, 3 fault, 4 flash, 5 off-grid | U16 | R | documented | M-TEC document |
| 10112–10115 | Fault flags 1 and 2 | U32 each | R | derived | MTECmqtt |
| 10994–10999 | Grid meter power L1–L3 | I32 each, W | R | derived | MTECmqtt |
| 11000–11001 | Grid meter power, total, **+ = export** | I32, W | R | documented (register), derived (sign) | M-TEC document; MTECmqtt; evcc (scale −1) |
| 11016–11017 | Inverter AC power | I32, W | R | documented | M-TEC document; MTECmqtt |
| 11028–11029 | PV power, total | U32, W | R | documented | M-TEC document; MTECmqtt |
| 11038–11041 | PV1, PV2 voltage and current | U16, 0.1 V, 0.1 A | R | derived | MTECmqtt |
| 11062–11065 | PV1, PV2 power | U32 each, W | R | derived | MTECmqtt |
| 30230–30231 | Backup (EPS) output power | I32, W | R | documented | M-TEC document; MTECmqtt |
| 30254 | Battery voltage | U16, 0.1 V | R | documented | M-TEC document |
| 30255 | Battery current | I16, 0.1 A | R | documented | M-TEC document |
| 30256 | Battery mode | U16 | R | derived; not used | MTECmqtt |
| 30258–30259 | Battery power, **+ = discharging** | I32, W | R | documented (register), derived (sign) | M-TEC document; evcc |
| 31000–31006 | Today: export, import, backup output (31002), battery charge and discharge, PV, load | U16, 0.1 kWh | R | derived | MTECmqtt |
| 31102–31113 | Lifetime: export, import, backup output (31106), battery charge, battery discharge, PV | U32, 0.1 kWh | R | derived | MTECmqtt |
| 33000 | State of charge | U16, 0.01 % | R | documented | M-TEC document; MTECmqtt |
| 33001 | State of health | U16, 0.01 % | R | derived | MTECmqtt |

"M-TEC document" is "M-TEC Energy Butler RTU Protocol GEN2 + GEN3 V1.0" (2022-11-19), which an owner posted publicly
in the ioBroker forum; facts only, in own words. It describes RS485 (9600 8N1, address 247) and the function codes
03, 06 and 16, but lists every register as read-only.

## Behaviour

| Fact | Status | Source |
|---|---|---|
| The Wi-Fi/LAN module listens on port 5743 before module firmware V27.52.4.0 and on 502 from then on. | derived | croedel/MTECmqtt; owners' reports (2025–2026) |
| Unit 252 with RTU frames over TCP (MTECmqtt, port 5743), 247 (RS485 default, also shown on the display), 255 with plain Modbus TCP (evcc); on 502 owners report both framings working. | open | MTECmqtt; evcc `solinteg-gen3`; owners' reports |
| The house load is the inverter's AC power minus the grid power (11016 − 11000). | derived | MTECmqtt (32cdf27) |
| The Energy-Butler GEN3 is a Solinteg inverter; evcc's `solinteg-gen3` and the Solinteg plugin of homeassistant-solax-modbus cover it. | derived | evcc-io/evcc (MIT); wills106/homeassistant-solax-modbus (Apache-2.0) |
| The module shows up in the router as "espressif"; the data also goes to M-TEC's cloud at least once a minute. | derived | owners' reports; croedel/M-TEC-API README |
| Writing (operating mode 50000, export limit 25100/25103, SoC floors 52502–52505, EMS setpoints 50202–50210) is used by projects without a documented watchdog: the values are probably stored. | derived, open | MTECmqtt (LGPL, facts only), evcc, Solinteg plugin |
| M-TEC is still in business; owners use the "M-TEC Energy Butler" app and the portal energybutler.mtec-portal.com. In 2023 M-TEC removed the grid export settings from the portal for owners; installers still have them. | documented (store listings), derived (forum) | App Store and Google Play listings; forum.iobroker.net topic 64236 |

## Decisions in OpenAmpere

| Decision | Why | Issue/PR |
|---|---|---|
| Display only: every write method refuses, `supports_control` is off. | Nothing confirmed on a device; the writes known have no watchdog. | [#520](https://github.com/Gr33ndev/OpenAmpere/issues/520) |
| Detected after FoxESS, SAJ and Solarmax on units 252, 255 and 247; plain Modbus TCP first, then RTU frames over TCP, one try each. If the first unit answers in neither framing, the others are asked in plain Modbus TCP only for 10 minutes. A device counts as an M-TEC only with a readable serial number, a known running state and a state of charge within 0–100 %. | The sources disagree on unit id and framing; detection must not claim another device, and a silent Modbus device in the network must not hold up the setup scan. |
| The port is not probed: with older module firmware the owner enters 5743 (the setup says so next to the port). | Detection stops when nothing listens on the port; a second port would need a second address in the settings. | [#520](https://github.com/Gr33ndev/OpenAmpere/issues/520) |
| Blocks only over the addresses the sources list. | A block across an undocumented gap might be refused. | [#520](https://github.com/Gr33ndev/OpenAmpere/issues/520) |
| Running state 3 (fault) reports the fault flags as a fault; 5 means off-grid. | M-TEC document. | [#520](https://github.com/Gr33ndev/OpenAmpere/issues/520) |

## Open questions

- On port 502: which framing and unit id answer on an EKD unit; does 5743 still answer after an update?
- The signs of 11000 and 30258, and whether 11016 − 11000 matches the house load in the M-TEC app.
- Whether loads on the backup (EPS) output are part of 11016: if not, the house load needs 30230 too (and during an
  outage it is all of it). The diagnostics show 30230 so a report can tell.
- Whether the module takes OpenAmpere next to the M-TEC app and cloud without trouble.
- Type plate text ("Energy-Butler 10kW-3P-3G25" is expected) and register 10008 of an EKD unit; whether any is a GEN2.

## Sources

- M-TEC, "Energy Butler RTU Protocol GEN2 + GEN3 V1.0" (2022-11-19).
- croedel/MTECmqtt up to commit 32cdf27 (MIT; LGPL-3.0 after it): register table and the house load.
- evcc-io/evcc, template `solinteg-gen3` (MIT): signs, unit id 255.
- wills106/homeassistant-solax-modbus, `plugin_solinteg.py` (Apache-2.0): cross-check.
- Research comment in [#520](https://github.com/Gr33ndev/OpenAmpere/issues/520).
