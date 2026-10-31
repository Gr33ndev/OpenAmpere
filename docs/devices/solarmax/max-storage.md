# Solarmax MAX.STORAGE ("AMPERE.Storage")

> Not official manufacturer information: compiled by the OpenAmpere contributors, can be incomplete or wrong. Changing
> inverter settings can affect the warranty and grid rules; no affiliation with Solarmax or EKD; not legal advice. See
> the [device knowledge README](../README.md).

**Applies to:** Solarmax MAX.STORAGE and MAX.STORAGE Ultimate hybrid inverters · **Sold as:** "AMPERE.Storage" (EKD,
the 10 kW Ultimate, about 2022–2023) · **OpenAmpere:** driver `solarmax` (`src/openampere/drivers/solarmax/`),
register map `solarmax_max_storage` · **Support:** display only · **Confirmed on a real device:** no (built from the
manufacturer's register list and the simulator)

## Summary

- Modbus TCP on port 502, unit id 1, **input registers (FC04)**, addresses from 0, 32-bit values with the **low word
  first** (unlike FoxESS and SAJ).
- The live values (PV, battery, house, grid, state of charge) are readable on every device. Everything from register 130
  on, including the **lifetime energy counters**, needs "Connectivity+", which only Solarmax unlocked; without it
  OpenAmpere has no energy history for this device.
- **Signs differ:** Solarmax counts battery charging and grid export as positive; the driver turns both around.
- OpenAmpere only reads. EKD switched the storage function of these devices off remotely in 2025
  ([names.md](../topics/names.md#behaviour)); OpenAmpere must never switch it on again.
- Solarmax is insolvent; the old apps are gone and the portal costs money, so OpenAmpere may be the only free local
  app left for these owners ([#519](https://github.com/Gr33ndev/OpenAmpere/issues/519)).

## Registers

All input registers (FC04), unit id 1. 32-bit values: low word at the lower address.

| Register | Meaning | Type, scale, sign | R/W | Status | Source |
|---|---|---|---|---|---|
| 100–101 | Serial number of the control unit | U32 | R | documented | Solarmax list |
| 102 | Firmware of the control unit, e.g. 344 = 3.4.4 | U16 | R | documented | Solarmax list |
| 103 | Device state: 0 unknown, 1 ok, 2 warning, 3 error | U16 | R | documented | Solarmax list |
| 105 | Nominal AC power | U16, W | R | documented | Solarmax list |
| 110–111 | PV power (DC) of this inverter | I32, W | R | documented, derived (type) | Solarmax list; evcc |
| 112–113 | PV power (DC) of the whole plant, other connected inverters included | I32, W | R | documented | Solarmax list |
| 114–115 | Battery power, **+ = charging**, − = discharging | I32, W | R | documented, derived (type) | Solarmax list; evcc (scale −1) |
| 116–117 | House consumption ("Eigenverbrauchsleistung") | I32, W | R | documented | Solarmax list |
| 118–119 | Grid power, **+ = export**, − = import | I32, W | R | documented, derived (type) | Solarmax list; evcc (scale −1) |
| 120–121 | AC power of the whole PV plant | I32, W | R | documented | Solarmax list |
| 122 | State of charge | U16, % | R | documented | Solarmax list; evcc |
| 123 | Usable battery capacity | U16, whole kWh | R | documented | Solarmax list |
| 124 | Energy stored now | U16, whole kWh | R | documented | Solarmax list |
| 125, 126 | Power of MPPT 1 and 2 | U16, whole kW | R | documented | Solarmax list |
| 130–138 | Battery limits, pack voltages, cell temperatures ("Connectivity+") | | R | documented | Solarmax list |
| 140–143 | AC voltages L1–L3, grid frequency ("Connectivity+") | | R | documented | Solarmax list |
| 150–151 | PV energy of this inverter and those connected to it ("Connectivity+") | U32, whole kWh | R | documented | Solarmax list |
| 152–153 | Energy exported to the grid ("Connectivity+") | U32, whole kWh | R | documented | Solarmax list |
| 154–155 | Energy imported from the grid ("Connectivity+") | U32, whole kWh | R | documented | Solarmax list |
| 156–157 | Energy charged into the batteries ("Connectivity+") | U32, whole kWh | R | documented | Solarmax list |
| 158–159 | Energy discharged from the batteries ("Connectivity+") | U32, whole kWh | R | documented | Solarmax list |
| holding 140–143 | Battery power setpoint, watchdog, active power limit in % ("Connectivity+") | | R/W | documented; not used by OpenAmpere | Solarmax list |

"Solarmax list" is "MAX.STORAGE / MAX.STORAGE Ultimate ModbusTCP Datenpunktliste" (2024-09-11, firmware 3.4.4 and
later). The input registers 140–143 and the holding registers 140–143 are different tables.

## Behaviour

| Fact | Status | Source |
|---|---|---|
| Solarmax's list marks 100–126 as available on every device and 130–159 as "Connectivity+" only. | documented | Solarmax list |
| evcc reads grid 118, PV 110, battery 114 and state of charge 122 as signed 32-bit values with the low word first, and dropped the PV energy register because it counts only whole kWh. Battery control in evcc needs "Connectivity+" and firmware 3.4.4. | derived | evcc-io/evcc `templates/definition/meter/solarmax-maxstorage.yaml` at 8796395558 (MIT) |
| Tinkerforge's energy manager reads the same addresses and also 112, 116 and 120: a second, independent reading of these registers. | derived | Tinkerforge/esp32-firmware `software/src/modules/meters_modbus_tcp/solarmax.py` (LGPL-2.1, facts only) |
| geeks-r-us/solarmax2mqtt reads the device over Modbus TCP; geeks-r-us/maxstorage_ultimate reads the web interface on port 80 instead (no license: facts only). The older Solarmax "MaxTalk" protocol (port 12345) belongs to Solarmax string inverters, not to MAX.STORAGE. | derived | the repositories named (solarmax2mqtt MIT) |
| The web interface shows firmware, hardware version and an "Ident"; the device can be reached as `<Ident>.local`. | derived | geeks-r-us/maxstorage_ultimate |
| The list defines holding registers 140/141 as one 32-bit setpoint; evcc writes them as two 16-bit limits with 142 as watchdog, which owners say works. Only matters for control. | open | Solarmax list; evcc template |
| Solarmax is in preliminary insolvency proceedings since 2024-11-29 (court Neu-Ulm); no buyer is reported. | documented | [solarserver.de, 2024-12-06](https://www.solarserver.de/2024/12/06/wechselrichtermarke-solarmax-hat-insolvenz-angemeldet/) |
| The app and the device's web interface keep working inside the home network by IP address; the cloud portal came back in 2025 as a paid subscription run by another company, and the old Solarmax apps are no longer in the App Store. | derived | photovoltaikforum.com threads 243977 and 252527 (2025); research in [#519](https://github.com/Gr33ndev/OpenAmpere/issues/519) |
| EKD switched the storage function off remotely in September 2025 and released it after a fuse was fitted in the battery line. How the registers look while it is switched off is not known. | derived (shutdown), open (registers) | [names.md](../topics/names.md#behaviour); photovoltaikforum.com thread 252527 |
| "AMPERE.Storage" is the 10 kW variant (three-phase, 2 MPPT, 6–15 kWh in 3 kWh packs); MAX.STORAGE Ultimate also exists with 6, 8, 12 and 15 kW, and the list covers the older MAX.STORAGE too. Register 105 tells them apart. | documented | EKD datasheet "AMPERE.Storage" (2022-07); Solarmax datasheet MAX.STORAGE Ultimate (2021-12) |

## Decisions in OpenAmpere

| Decision | Why | Issue/PR |
|---|---|---|
| Display only: every write method refuses, `supports_control` is off. | EKD switched the storage function off remotely; control also needs "Connectivity+". OpenAmpere never switches it on again. | [#515](https://github.com/Gr33ndev/OpenAmpere/pull/515), [#519](https://github.com/Gr33ndev/OpenAmpere/issues/519) |
| Detected after FoxESS and SAJ, at unit 1, by reading 100–126 with FC04. The device counts as a Solarmax only if firmware (100–999), device state (0–3), nominal power (1–30 kW), state of charge and capacity (at most 100) are all plausible. | Detection must not claim another device; SAJ also answers at unit 1, but not with FC04 at 100. | [#519](https://github.com/Gr33ndev/OpenAmpere/issues/519) |
| Without the counters of "Connectivity+", PV power is the inverter's own DC power (110); the rest of the plant (112 − 110) is shown as a second PV inverter the owner can count, once it was more than 50 W at least once, or with 0 W as soon as the owner counts one (`pv.external`). With the counters, PV power is the whole plant's (112) and there is no second inverter to count. | 112 includes other inverters connected to the MAX.STORAGE; without one the app must not offer to count it. What was seen is not stored, so after a restart at night a counted inverter would otherwise be unknown until the sun comes back. The PV counter (150) already includes the connected inverters: counting one on top would count its energy twice. | [#519](https://github.com/Gr33ndev/OpenAmpere/issues/519) |
| The energy counters (150–159) are used only when they answer and are not all zero, and are no longer asked for after a refusal, or after three reads in a row without an answer if they never answered; there is no load counter, the consumption is their balance. | Without "Connectivity+" the device may refuse them, answer zeros or not answer; zeros would look like counters that start at 0, and the live values must never wait for them. | [#519](https://github.com/Gr33ndev/OpenAmpere/issues/519) |
| Device state 3 (error) is reported as a fault; 2 (warning) is not. | A warning is no fault the owner must act on. | [#519](https://github.com/Gr33ndev/OpenAmpere/issues/519) |
| The diagnostics show the powers with Solarmax's own signs (rounded) and whether the "Connectivity+" registers answer, without the serial number and without counter values. | So the first owner's report can confirm the signs. | [#519](https://github.com/Gr33ndev/OpenAmpere/issues/519) |

## Open questions

- Whether port 502 answers with factory settings, and whether 100–126 really read without "Connectivity+".
- The signs of 114 and 118 on a real device (the list and evcc agree; no owner's report yet).
- What 130–159 return without "Connectivity+": an exception or zeros.
- What 103, 114, 123 and 124 show while EKD's switch-off is active, and whether PV keeps producing then.
- Whether 116 is the whole house consumption when a second inverter is in the house.
- Which firmware EKD's units run; the list is for 3.4.4 and later.
- What the type plate says, and whether EKD's units carry "AMPERE" on the housing.

## Sources

- Solarmax, "MAX.STORAGE / MAX.STORAGE Ultimate ModbusTCP Datenpunktliste", 2024-09-11: handed to owners and attached
  by them to public evcc issues; no longer on solarmax.com. Facts only, in own words.
- evcc-io/evcc, template `solarmax-maxstorage` (MIT), commit 8796395558.
- EKD datasheet "AMPERE.Storage" (2022-07); Solarmax datasheet MAX.STORAGE Ultimate (2021-12).
- Tinkerforge/esp32-firmware (LGPL-2.1), geeks-r-us/solarmax2mqtt (MIT), geeks-r-us/maxstorage_ultimate (no license):
  facts only.
- photovoltaikforum.com threads 243977 and 252527 (owners, 2025); solarserver.de (2024-12-06).
- Research comment in [#519](https://github.com/Gr33ndev/OpenAmpere/issues/519).
