# FoxESS AC EV Charger

> Not official manufacturer information: compiled by the OpenAmpere contributors, possibly incomplete or wrong.
> Changing device settings can affect warranty and grid connection rules. No affiliation with FoxESS or EKD, not
> legal advice. See [Device knowledge](../README.md).

**Applies to:** FoxESS AC EV Charger (wallbox, single- and three-phase models) · **Sold as:** not stated in the
sources · **OpenAmpere:** wallbox module `foxess_ac`, read-only wallbox check in the setup · **Support:** display
only (read-only check, no control) · **Confirmed on a real device:** no

## Summary

- The charger has three communication modes, only one active at a time: default (FoxESS app and cloud, with PV
  linkage to the inverter), Modbus TCP and OCPP.
- OpenAmpere only reads it, in a one-off check of the setup, to confirm the register addresses
  ([#325](https://github.com/Gr33ndev/OpenAmpere/issues/325)). Control comes only after owners' reports confirm them
  ([#318](https://github.com/Gr33ndev/OpenAmpere/issues/318)).
- Modbus TCP: all registers are holding registers (FC03), unit id 1, port 502 (1502 also seen).
- In Modbus TCP mode the charger's watchdog fails in the unsafe direction: it keeps charging when the controller stops
  writing.
- The register addresses come from a FoxESS document that is not public; they are used as facts only.

## Registers

Read by the check over one connection: 0x1000–0x1021, 0x3000–0x3006 and, separately, 0x3007–0x300B (missing on
single-phase models). The raw words go into the report through OpenAmpere's masking (serial-like text shortened,
private words removed). Nothing is written.

| Register | Meaning | Type, scale, sign | R/W | Status | Source |
|---|---|---|---|---|---|
| 0x1000, 0x1002, 0x1004, 0x1006–0x1007, 0x100F–0x1010 | Read as part of the block, not interpreted by OpenAmpere | – | R FC03 | open | [foxess.py](../../../src/openampere/wallbox/foxess.py) |
| 0x1001 | Firmware: high byte major, low byte minor | U16 | R FC03 | documented | FoxESS EV Charger Modbus TCP protocol V1.6 |
| 0x1003 | Status: 0 idle, 1 connected, 2 starting, 3 charging, 4 paused, 5 finished, 6 fault, 8 locked | U16 | R FC03 | documented | FoxESS EV Charger Modbus TCP protocol V1.6 |
| 0x1003 = 9 | Status "switching phases": not in the document, seen on real devices | U16 | R FC03 | derived | owner reports in public evcc discussions |
| 0x1005 | Plug connected | U16, 0/1 | R FC03 | documented | FoxESS EV Charger Modbus TCP protocol V1.6 |
| 0x1008–0x100A | Voltage L1, L2, L3 | U16, ×0.1 V | R FC03 | documented | FoxESS EV Charger Modbus TCP protocol V1.6 |
| 0x100B–0x100D | Current L1, L2, L3 | U16, ×0.1 A | R FC03 | documented | FoxESS EV Charger Modbus TCP protocol V1.6 |
| 0x100E | Charging power | U16, ×0.1 kW | R FC03 | documented | FoxESS EV Charger Modbus TCP protocol V1.6 |
| 0x1011 | Maximum power | U16, ×0.1 kW | R FC03 | documented | FoxESS EV Charger Modbus TCP protocol V1.6 |
| 0x1012 | Minimum power | U16, ×0.1 kW | R FC03 | documented | FoxESS EV Charger Modbus TCP protocol V1.6 |
| 0x1013 | Maximum current | U16, ×0.1 A | R FC03 | documented | FoxESS EV Charger Modbus TCP protocol V1.6 |
| 0x1014 | Minimum current | U16, ×0.1 A | R FC03 | documented | FoxESS EV Charger Modbus TCP protocol V1.6 |
| 0x1015 | Alarm bits | U16, bit field | R FC03 | documented | FoxESS EV Charger Modbus TCP protocol V1.6 |
| 0x1016–0x1019 | Energy counters; OpenAmpere decodes 0x1016–0x1017 as the lifetime counter | U32, ×0.1 kWh | R FC03; removed from the report, only "plausible" yes/no | documented | FoxESS EV Charger Modbus TCP protocol V1.6 |
| 0x101A–0x101B | Fault bits | U32, bit field | R FC03 | documented | FoxESS EV Charger Modbus TCP protocol V1.6 |
| 0x101C–0x101D | RFID card that started the session | U32 | R FC03; removed from the report, only "set / not set" | documented | FoxESS EV Charger Modbus TCP protocol V1.6 |
| 0x101E–0x1021 | Model code | ASCII, 4 registers | R FC03; kept in the report | documented | FoxESS EV Charger Modbus TCP protocol V1.6 |
| 0x1022–0x1031 | Serial number | ASCII | not read | documented | FoxESS EV Charger Modbus TCP protocol V1.6 |
| 0x3000 | Work mode: 0 controlled (start through the app or a card), 1 plug and charge (charges when plugged in), 2 locked | U16 | R FC03 | documented | FoxESS EV Charger Modbus TCP protocol V1.6 |
| 0x3001–0x3004 | Read as part of the block, not interpreted by OpenAmpere | – | R FC03 | open | [foxess.py](../../../src/openampere/wallbox/foxess.py) |
| 0x3005 | Validity of the controller's setpoint | U16, s | R FC03 | documented | FoxESS EV Charger Modbus TCP protocol V1.6 |
| 0x3006 | Fallback current: the charger charges at this current when a Modbus controller stops writing | U16, ×0.1 A | R FC03 | documented | FoxESS EV Charger Modbus TCP protocol V1.6 |
| 0x3007–0x3009 | Read in the separate block (missing on single-phase models), not interpreted | – | R FC03 | open | [foxess.py](../../../src/openampere/wallbox/foxess.py) |
| 0x300A | Automatic 1/3-phase switching | U16, 0/1 | R FC03 | documented | FoxESS EV Charger Modbus TCP protocol V1.6 |
| 0x300B | Minimum interval between phase switches | U16, minutes | R FC03 | documented | FoxESS EV Charger Modbus TCP protocol V1.6 |

None of these registers is confirmed on a device for OpenAmpere yet; the check exists to confirm them.

## Behaviour

| Fact | Status | Source |
|---|---|---|
| Modbus TCP over LAN or WLAN; port 502 according to the protocol, 1502 seen in at least one integration; unit id 1 by default; all registers are holding registers (FC03). | documented | FoxESS EV Charger Modbus TCP protocol V1.6; [#325](https://github.com/Gr33ndev/OpenAmpere/issues/325) |
| The charger announces Modbus TCP over mDNS as `EVC-<serial>._modbus._tcp`. | derived | [#325](https://github.com/Gr33ndev/OpenAmpere/issues/325), [#327](https://github.com/Gr33ndev/OpenAmpere/issues/327), [probe.py](../../../src/openampere/probe.py) |
| Three communication modes, set in the Fox Switch app, one at a time: default (FoxESS app/cloud with PV linkage), Modbus TCP, OCPP. | documented | Fox Switch app manual V1.0.1; [#318](https://github.com/Gr33ndev/OpenAmpere/issues/318) |
| In default mode the charger can be wired over RS485 to a FoxESS inverter (H3, H3 Pro, H3 Smart, see [h3.md](h3.md)) or a shared meter and charges from surplus by itself: Fast (full current), Economic (solar first, at least 6 A from the grid), Green (surplus only). FoxESS requires self-use mode, no export limit and the order house → car → battery (the home battery may discharge into the car). | documented | FoxESS energy management user manual; [#318](https://github.com/Gr33ndev/OpenAmpere/issues/318) |
| In default mode Modbus TCP is read-only according to the Fox Switch app manual (section 8.6). | documented | Fox Switch app manual V1.0.1; [#318](https://github.com/Gr33ndev/OpenAmpere/issues/318) |
| Outside the "Modbus TCP" mode some firmware answers every read with exception 6 ("server device busy"). | derived | evcc discussions; [#318](https://github.com/Gr33ndev/OpenAmpere/issues/318), [#325](https://github.com/Gr33ndev/OpenAmpere/issues/325) |
| In Modbus TCP mode the watchdog fails in the unsafe direction: when the controller stops writing, the charger does not stop; after about 60 s it charges at its stored default current or at full power. Only an explicit stop ends charging, and a power setpoint written after a stop can restart it. Cost risk only; the charger stays within its rated limits. | derived | owner reports from other projects, [#318](https://github.com/Gr33ndev/OpenAmpere/issues/318) |
| Current and power setpoints are held in RAM and lost on a power cut; configuration values are probably stored. Automatic 1/3-phase switching follows the power setpoint. | derived | [#318](https://github.com/Gr33ndev/OpenAmpere/issues/318) |
| Some chargers accept a single Modbus client only: a charger read by evcc may refuse a second connection or drop evcc's for a moment. The real number of connections is unknown. | open | [probe.py](../../../src/openampere/probe.py), [#318](https://github.com/Gr33ndev/OpenAmpere/issues/318) |
| The installer's binding of the charger in the FoxESS app expires after 6 hours; owners can bind it to their own FoxESS cloud account. | derived | desk research in [#318](https://github.com/Gr33ndev/OpenAmpere/issues/318) |
| OCPP: 1.6J works; 2.0.1 rejects charging profiles (FoxESS support recommends 1.6J). No phase switching over OCPP. After a server restart the charger can take many minutes to reconnect. In OCPP mode the FoxESS app/cloud and evcc lose control; the charge point id is the serial number. See [wallboxes](../topics/wallboxes.md). | derived | [#318](https://github.com/Gr33ndev/OpenAmpere/issues/318) |
| Newer models have a hardware input for the grid operator's dimming signal (§ 14a EnWG). | derived | [#318](https://github.com/Gr33ndev/OpenAmpere/issues/318) |
| The FoxESS H3 has no registers for an attached charger; its power is part of the house load. | documented | FoxESS inverter Modbus definition; [#318](https://github.com/Gr33ndev/OpenAmpere/issues/318) |

## Decisions in OpenAmpere

| Decision | Why | Issue/PR |
|---|---|---|
| First step is read-only: a generic Modbus read tool, the wallbox check and an OCPP listener. FoxESS owners with PV linkage keep the default mode; OpenAmpere only displays the charger. | Registers are unconfirmed; the watchdog fails unsafe. | [#318](https://github.com/Gr33ndev/OpenAmpere/issues/318), [#324](https://github.com/Gr33ndev/OpenAmpere/issues/324), [#325](https://github.com/Gr33ndev/OpenAmpere/issues/325), [#326](https://github.com/Gr33ndev/OpenAmpere/issues/326), [#327](https://github.com/Gr33ndev/OpenAmpere/issues/327), [#328](https://github.com/Gr33ndev/OpenAmpere/issues/328), [#333](https://github.com/Gr33ndev/OpenAmpere/issues/333) |
| The check tries port 502, then 1502, unit 1, and reads only with FC03. | Both ports are seen. | [#325](https://github.com/Gr33ndev/OpenAmpere/issues/325), [#327](https://github.com/Gr33ndev/OpenAmpere/issues/327) |
| The serial number (0x1022) is not read; energy counter and RFID card words are removed from the report (only "plausible" and "set / not set" remain); the serial in the mDNS name is masked. | They identify the owner. | [#325](https://github.com/Gr33ndev/OpenAmpere/issues/325), [#327](https://github.com/Gr33ndev/OpenAmpere/issues/327), [foxess.py](../../../src/openampere/wallbox/foxess.py) |
| OpenAmpere assumes the charger allows one Modbus connection: never evcc and OpenAmpere on the same charger. | The number of connections is unknown. | [#318](https://github.com/Gr33ndev/OpenAmpere/issues/318) |
| Register addresses are used as facts only, nothing copied from the FoxESS document; evcc's FoxESS driver (not MIT) and two MIT Home Assistant integrations that derive from a project without a license are not used. | License and permission of the sources. | [#318](https://github.com/Gr33ndev/OpenAmpere/issues/318), [#325](https://github.com/Gr33ndev/OpenAmpere/issues/325), [#327](https://github.com/Gr33ndev/OpenAmpere/issues/327) |
| Proposed, if OpenAmpere ever controls the charger over Modbus: a one-time default current of 6 A with the owner's consent, an explicit stop on planned shutdown, a heartbeat of 30 s or less, an explanation in the app and an exception noted in AGENTS.md. OpenAmpere would not take on the § 14a role and never raise a limit while the grid operator dims. Not decided. | Fail-safe behaviour despite the unsafe watchdog; legal topics are settled in the issue first. | [#318](https://github.com/Gr33ndev/OpenAmpere/issues/318) |

## Open questions

- **Conflict, default mode:** the Fox Switch app manual says Modbus TCP is read-only in default mode; evcc reports that
  some firmware refuses every read with exception 6 outside the Modbus TCP mode. Only a real device can settle whether
  reads work in default mode ([#318](https://github.com/Gr33ndev/OpenAmpere/issues/318),
  [#325](https://github.com/Gr33ndev/OpenAmpere/issues/325)).
- Port (502 or 1502) and unit id on real devices.
- Number of simultaneous Modbus connections.
- Real watchdog time and fallback (default current or full power); whether a stop holds after the link is lost;
  which configuration values are stored.
- Whether PV linkage still runs in Modbus TCP mode.
- Possible conflicts with OpenAmpere (no owner reports yet): OpenAmpere's export limit contradicts FoxESS's
  precondition for PV linkage, and the charger might see OpenAmpere's grid charging of the battery as surplus
  ([#318](https://github.com/Gr33ndev/OpenAmpere/issues/318), [#327](https://github.com/Gr33ndev/OpenAmpere/issues/327)).
- OCPP: whether charging profiles survive a reboot, expiry handling, 0 A, reconnect time. A fail-safe idea (stored base
  profile of 0 A or 6 A plus short-lived higher limits renewed by OpenAmpere) is not confirmed on any charger; renewing
  stored profiles may wear the charger's flash, a 0 A base profile blocks charging while OpenAmpere is down, and OCPP
  1.6 leaves expiry and reboot behaviour open.
- Meaning of the read but uninterpreted words (0x1000, 0x1002, 0x1004, 0x1006–0x1007, 0x100F–0x1010, 0x3001–0x3004,
  0x3007–0x3009) and of 0x1018–0x1019.

## Sources

- FoxESS EV Charger Modbus TCP protocol V1.6: given to owners by FoxESS support, not public, marked "all rights
  reserved". Following the [README](../README.md#sources), only facts (register addresses, meanings, units) are
  used, in own words; the document is not quoted, not added to the repository and not passed on
  ([#318](https://github.com/Gr33ndev/OpenAmpere/issues/318)).
- FoxESS Fox Switch app manual V1.0.1 and FoxESS energy management user manual (as summarised in
  [#318](https://github.com/Gr33ndev/OpenAmpere/issues/318)).
- Owner reports in public evcc discussions (facts only; evcc's FoxESS driver is not used).
- OpenAmpere issues and pull requests linked above; code: [wallbox/foxess.py](../../../src/openampere/wallbox/foxess.py),
  [probe.py](../../../src/openampere/probe.py).
