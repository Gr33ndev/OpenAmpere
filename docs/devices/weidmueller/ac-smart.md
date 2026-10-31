# Weidmüller AC SMART ("AMPERE.Charge")

> Not official manufacturer information: compiled by the OpenAmpere contributors, can be incomplete or wrong. No
> affiliation with Weidmüller or EKD; not legal advice. See the [device knowledge README](../README.md).

**Applies to:** Weidmüller AC SMART ECO, VALUE and ADVANCED wallboxes · **Sold as:** "AMPERE.Charge" (EKD, the VALUE
with 11 kW, about 2023–2024) · **OpenAmpere:** wallbox module `weidmueller_ac_smart` (`src/openampere/wallbox/weidmueller.py`)
· **Support:** display only (live view and the read-only check of the setup) · **Confirmed on a real device:** no
(built from Weidmüller's register document and the simulator)

## Summary

- Modbus TCP is on at delivery: port 502, unit id 255, holding registers (FC03). It accepts **only one Modbus client
  at a time**: while evcc or the previous smartbox holds the connection, OpenAmpere gets none.
- Values of 4 bytes come with the **low word first**; the document does not say so, evcc found it on real devices.
- OpenAmpere only shows the wallbox: state, power, current and voltage per phase, the energy of the charge, its limits.
  It never writes; the only mode is "manual".
- Control would work as a satellite box (a load management current renewed within a timeout). When the controller
  goes away, the wallbox charges on with its fallback current, like the FoxESS charger
  ([foxess/ac-charger.md](../foxess/ac-charger.md)): control needs the same safety rules and a confirmation on a real
  device first ([#523](https://github.com/Gr33ndev/OpenAmpere/issues/523)).

## Registers

All holding registers (FC03), unit id 255. "4 bytes" = two registers, low word first.

| Register | Meaning | Type, scale, sign | R/W | Status | Source |
|---|---|---|---|---|---|
| 300 | Current offered to the car | U16, A | R | documented | Weidmüller document |
| 301 | Vehicle state, letter A–F in the low byte (A no car, B connected, C/D charging, E/F error) | ASCII | R | documented | Weidmüller document |
| 302 | State of the charge controller, 0–15 (3 waiting for authorisation, 11 charging, 12 paused, 14/15 error) | U16 | R | documented | Weidmüller document |
| 305–306 | Duration of the charge since the authorisation | 4 bytes, s | R | documented | Weidmüller document |
| 317 | Phases: 0 = one, 1 = three (stored) | U16 | R/W | documented; which one a satellite follows is open | Weidmüller document |
| 318 | Phases set by the load management (not stored) | U16 | R/W | documented | Weidmüller document |
| 319 | Installed phases (enum, 0 = one phase) | U16 | R | documented | Weidmüller document |
| 400–405 | Voltage L1–L3 (RMS) | 4 bytes each, mV | R | documented | Weidmüller document |
| 406–411 | Current L1–L3 (RMS) | 4 bytes each, mA | R | documented | Weidmüller document |
| 418–419 | Active power, sum | 4 bytes, mW | R | documented | Weidmüller document |
| 430–431 | Energy of the last (current) charge | 4 bytes, Wh | R | documented | Weidmüller document |
| 432 | Temperature of the sensor | INT, unit not stated | R | documented, open (unit) | Weidmüller document |
| 457–460 | Total energy | 8 bytes, Wh | R | documented; stays 0 on ECO and VALUE (derived) | Weidmüller document; evcc-io/evcc#35118 |
| 700 | Current limit of the wallbox | U16, A | R | documented | Weidmüller document |
| 701 | Current limit set at installation (stored) | U16, A | R/W | documented | Weidmüller document |
| 702 | Current limit of the user (stored) | U16, A | R/W | documented | Weidmüller document |
| 704 | Plug and charge (charging without authorisation) | U16 | R/W | documented | Weidmüller document |
| 705 | External charge release (not stored) | U16 | R/W | documented | Weidmüller document |
| 846–851 | Firmware version | ASCII, 12 bytes | R | documented | Weidmüller document |
| 995 | Modbus over 0 = Ethernet, 1 = WLAN (stored, needs a restart) | U16 | R/W | documented | Weidmüller document |
| 11000 / 11001 | Satellite box / control box (stored) | U16 | R/W | documented | Weidmüller document |
| 11050–11051 | Timeout of the satellite box (not stored) | 4 bytes, ms | R/W | documented | Weidmüller document; application note |
| 11052 | Current from the load management, 0 or 6–32 (not stored) | U16, A | R/W | documented | Weidmüller document; application note |
| 11053 | Error of the control box that stops charging | U16 | R/W | documented | Weidmüller document |
| 11054 | **Fallback current**: the current it charges with when the control box is gone (not stored) | U16, A | R/W | documented | Weidmüller document |

"Weidmüller document" is "AC SMART ECO / VALUE / ADVANCED Modbus-Register", Dok.-Nr. 2933050000 Rev. 02 (2023-06),
public on Weidmüller's website. It warns not to write the stored (non-volatile) registers cyclically.

## Behaviour

| Fact | Status | Source |
|---|---|---|
| Modbus TCP is preconfigured at delivery (port 502, unit id 255) and allows only one Modbus client; Modbus over WLAN instead of LAN is a setting that needs a restart. | documented; derived (owners confirm the one-client limit) | operating manual "AC SMART ECO/VALUE/ADVANCED"; [photovoltaikforum.com thread 227016](https://www.photovoltaikforum.com/thread/227016-ampere-charge-weidm%C3%BCller-wallbox-energiekonzepte-deutschland-ampere-storage-pro/) (2024) |
| 32-bit values come with the low word first. | derived | evcc-io/evcc issues #15261, #35118 and PR #35123 (behaviour seen on devices; evcc's `charger/weidmüller.go` is not MIT, nothing taken from it) |
| Control as a satellite box: write 11052, then set 11050 again before it runs out (the application note's example: 60 s). | documented | application note "Einbindung der AC Smart in Lade-/Lastmanagement" (2023-05, attached in evcc-io/evcc#15261) |
| Without its control box a satellite charges at the minimum of 6 A (manual); evcc saw it fall back about 65 s after evcc stopped. It charges on, it does not stop. | documented (manual), derived (evcc) | operating manual; evcc-io/evcc PR #35123 |
| A factory reset sets 11000 back to 0 but keeps 702; with 11000 = 1 and 702 = 0 the wallbox does not charge at all. | derived | evcc-io/evcc#35118, PR #35123 |
| The wallbox can itself read a meter or inverter over Modbus TCP (registers 495, 5606–5677); EKD owners entered inverter addresses there, so it may hold one of the inverter's Modbus connections. | documented (registers), derived (EKD use) | Weidmüller document; photovoltaikforum.com thread 227016 |
| EKD's "AMPERE.Charge" is type CH-W-S-A11-P7.5-V (VALUE, 11 kW, 7.5 m cable); the type plate does not print "VALUE", the last letter gives the variant (E/V/A). | documented (type), derived (letters) | EKD datasheets "AMPERE.Charge" (2023-04, 2023-12) |
| Weidmüller's "AC SMART App" works over Bluetooth, WLAN or LAN without a cloud account. | documented | App Store listing |

## Decisions in OpenAmpere

| Decision | Why | Issue/PR |
|---|---|---|
| Display only: never written, only the mode "manual"; its power counts in the house load like any wallbox. | Nothing confirmed on a real device; when OpenAmpere stops, the wallbox would charge on with its fallback current. | [#523](https://github.com/Gr33ndev/OpenAmpere/issues/523) |
| One short connection per read, closed right away. | The wallbox takes only one Modbus client; holding it would lock out the owner's app and other tools. | [#523](https://github.com/Gr33ndev/OpenAmpere/issues/523); `wallbox/modbus.py` |
| A voltage above 400 V is not shown. | That is what the other word order would give: better no value than a wrong one. | [#523](https://github.com/Gr33ndev/OpenAmpere/issues/523) |
| The check keeps the total energy (457–460) out of the report, only whether it is plausible. | An exact counter identifies the owner. | [#523](https://github.com/Gr33ndev/OpenAmpere/issues/523) |

## Open questions

- The word order on an EKD unit, and which firmware they run.
- What 11054 holds after power-up and whether it can be 0; what the wallbox does when 11050 runs out.
- Which phase register a satellite follows, 317 or 318.
- Whether something else (the previous smartbox, evcc) still holds its Modbus connection on EKD systems.
- The unit of the temperature (432).

## Sources

- Weidmüller, "AC SMART ECO / VALUE / ADVANCED Modbus-Register", 2933050000 Rev. 02, 2023-06 (public); operating
  manual "AC SMART ECO/VALUE/ADVANCED"; application note "Einbindung der AC Smart in Lade-/Lastmanagement" (2023-05).
  Facts only, in own words.
- evcc-io/evcc issues #15261, #35118 and PR #35123: behaviour seen on devices (the module `charger/weidmüller.go` is
  "all rights reserved": nothing taken from it).
- EKD datasheets "AMPERE.Charge" (2023-04 and 2023-12).
- Research comment in [#523](https://github.com/Gr33ndev/OpenAmpere/issues/523).
