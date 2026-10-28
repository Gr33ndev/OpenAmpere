# FoxESS AC EV Charger

> Not official manufacturer information: compiled by the OpenAmpere contributors, possibly incomplete or wrong.
> Changing device settings can affect warranty and grid connection rules. No affiliation with FoxESS or EKD, not
> legal advice. See [Device knowledge](../README.md).

**Applies to:** FoxESS AC EV Charger (wallbox, single- and three-phase models) · **Sold as:** not stated in the
sources; owners report it with EKD (Ampere.IQ) systems ([#412](https://github.com/Gr33ndev/OpenAmpere/issues/412)) · **OpenAmpere:** wallbox module `foxess_ac`, read-only wallbox check in the setup and live view
(`wallbox/native.py`, [#439](https://github.com/Gr33ndev/OpenAmpere/issues/439)), its work mode written on request ([#464](https://github.com/Gr33ndev/OpenAmpere/issues/464)), its charging modes as a simulation without writing ([#440](https://github.com/Gr33ndev/OpenAmpere/issues/440)) · **Support:** display
(live view and check) and the work mode on request ([#464](https://github.com/Gr33ndev/OpenAmpere/issues/464)), no
control of charging · **Confirmed on a real device:** partly (connection and the read registers on one
three-phase charger of an EKD system, [#431](https://github.com/Gr33ndev/OpenAmpere/issues/431))

## Summary

- The charger has three communication modes, only one active at a time: default (FoxESS app and cloud, with PV
  linkage to the inverter), Modbus TCP and OCPP.
- OpenAmpere reads it: in a one-off check of the setup, to confirm the register addresses
  ([#325](https://github.com/Gr33ndev/OpenAmpere/issues/325)), and as a wallbox it shows live
  ([#439](https://github.com/Gr33ndev/OpenAmpere/issues/439)). The one register it writes, on the owner's request, is
  the work mode 0x3000 ([#464](https://github.com/Gr33ndev/OpenAmpere/issues/464)); its charging modes are a simulation that only logs
  what OpenAmpere would set ([#440](https://github.com/Gr33ndev/OpenAmpere/issues/440)). Control comes only after
  owners' reports confirm the registers ([#318](https://github.com/Gr33ndev/OpenAmpere/issues/318)).
- Modbus TCP: registers are holding registers read with FC03 (0x4000–0x4003 are write-only, FC06), unit id 1, port 502 (1502 also seen); port 502, unit id 1
  and FC03 confirmed on one charger ([#431](https://github.com/Gr33ndev/OpenAmpere/issues/431)).
- On EKD systems the previous smartbox blocks OpenAmpere's access to the charger while it is powered; with the box
  cut from power the check reads the charger ([#431](https://github.com/Gr33ndev/OpenAmpere/issues/431)).
- In Modbus TCP mode the charger's watchdog fails in the unsafe direction: it keeps charging when the controller stops
  writing.
- The register addresses come from FoxESS's protocol document V1.6, which FoxESS support gives to owners and an owner
  posted in a public evcc discussion; it is marked "all rights reserved", so they are used as facts only. The
  write registers (work mode, current and power setpoints, start/stop) are documented there, not yet tested by
  OpenAmpere ([#463](https://github.com/Gr33ndev/OpenAmpere/issues/463)).

## Registers

Read by the check over one connection: 0x1000–0x1021, 0x3000–0x3006 and, separately, 0x3007–0x300B (missing on
single-phase models). The raw words go into the report through OpenAmpere's masking (serial-like text shortened,
private words removed). Nothing is written.

Read by the live view ([#439](https://github.com/Gr33ndev/OpenAmpere/issues/439)) every 10 s over one short
connection, closed after reading: 0x1000–0x1015 and 0x3000–0x3006, then 0x3007–0x300B (skipped when the charger
refuses it as unknown addresses, as single-phase models may). The energy counters, the card number, the model code
and the serial number (0x1016–0x1031) are not read at all. Only function code 3; nothing is written.

| Register | Meaning | Type, scale, sign | R/W | Status | Source |
|---|---|---|---|---|---|
| 0x1000, 0x1002, 0x1004, 0x1006–0x1007, 0x100F–0x1010 | Read as part of the block, not interpreted by OpenAmpere | – | R FC03 | open | [foxess.py](../../../src/openampere/wallbox/foxess.py) |
| 0x1001 | Firmware: high byte major, low byte minor | U16 | R FC03 | confirmed | FoxESS EV Charger Modbus TCP protocol V1.6; plausible on one device: owner's diagnostics report, [#431](https://github.com/Gr33ndev/OpenAmpere/issues/431) |
| 0x1003 | Status: 0 idle, 1 connected (waiting for the start command), 2 starting (waiting for the car), 3 charging, 4 paused, 5 finished, 6 fault, 8 locked (no operation possible) | U16 | R FC03 | documented; 0 (idle, no car) confirmed | FoxESS EV Charger Modbus TCP protocol V1.6; plausible on one device: owner's diagnostics report, [#431](https://github.com/Gr33ndev/OpenAmpere/issues/431) |
| 0x1003 = 9 | Status "switching phases": not in the document, seen on real devices | U16 | R FC03 | derived | owner reports in public evcc discussions |
| 0x1005 | Plug connected; read 1 on one device while idle with no car plugged in, so the meaning is doubtful (not shown in the app, only in the report) | U16, 0/1 | R FC03 | open (documented, contradicted on one device) | FoxESS EV Charger Modbus TCP protocol V1.6; owner's diagnostics report, [#431](https://github.com/Gr33ndev/OpenAmpere/issues/431) |
| 0x1008–0x100A | Voltage L1, L2, L3 | U16, ×0.1 V | R FC03 | confirmed | FoxESS EV Charger Modbus TCP protocol V1.6; plausible on one device: owner's diagnostics report, [#431](https://github.com/Gr33ndev/OpenAmpere/issues/431) |
| 0x100B–0x100D | Current L1, L2, L3 | U16, ×0.1 A | R FC03 | documented | FoxESS EV Charger Modbus TCP protocol V1.6 |
| 0x100E | Charging power | U16, ×0.1 kW | R FC03 | documented | FoxESS EV Charger Modbus TCP protocol V1.6 |
| 0x1011 | Maximum power | U16, ×0.1 kW | R FC03 | confirmed | FoxESS EV Charger Modbus TCP protocol V1.6; plausible on one device: owner's diagnostics report, [#431](https://github.com/Gr33ndev/OpenAmpere/issues/431) |
| 0x1012 | Minimum power | U16, ×0.1 kW | R FC03 | confirmed | FoxESS EV Charger Modbus TCP protocol V1.6; plausible on one device: owner's diagnostics report, [#431](https://github.com/Gr33ndev/OpenAmpere/issues/431) |
| 0x1013 | Maximum current | U16, ×0.1 A | R FC03 | confirmed | FoxESS EV Charger Modbus TCP protocol V1.6; plausible on one device: owner's diagnostics report, [#431](https://github.com/Gr33ndev/OpenAmpere/issues/431) |
| 0x1014 | Minimum current | U16, ×0.1 A | R FC03 | confirmed | FoxESS EV Charger Modbus TCP protocol V1.6; plausible on one device: owner's diagnostics report, [#431](https://github.com/Gr33ndev/OpenAmpere/issues/431) |
| 0x1015 | Alarm bits; bit 1 was set on one device while idle, meaning unknown | U16, bit field | R FC03 | documented; bit 1: open | FoxESS EV Charger Modbus TCP protocol V1.6; owner's diagnostics report, [#431](https://github.com/Gr33ndev/OpenAmpere/issues/431) |
| 0x1016–0x1019 | Energy counters: 0x1016–0x1017 lifetime, 0x1018–0x1019 the current session; OpenAmpere decodes only the lifetime counter, for the "plausible" check of the report | U32, ×0.1 kWh | R FC03; removed from the report, only "plausible" yes/no | documented | FoxESS EV Charger Modbus TCP protocol V1.6 |
| 0x101A–0x101B | Fault bits | U32, bit field | R FC03 | documented | FoxESS EV Charger Modbus TCP protocol V1.6 |
| 0x101C–0x101D | RFID card that started the session | U32 | R FC03; removed from the report, only "set / not set" | documented | FoxESS EV Charger Modbus TCP protocol V1.6 |
| 0x101E–0x1021 | Model code | ASCII, 4 registers | R FC03; kept in the report | confirmed | FoxESS EV Charger Modbus TCP protocol V1.6; plausible on one device: owner's diagnostics report, [#431](https://github.com/Gr33ndev/OpenAmpere/issues/431) |
| 0x1022–0x1031 | Serial number | ASCII | not read | documented | FoxESS EV Charger Modbus TCP protocol V1.6 |
| 0x3000 | Work mode: 0 controlled (start through the app, a card or a Modbus controller), 1 plug and charge (charges when plugged in), 2 locked | U16 | R FC03, W FC16 | confirmed | FoxESS EV Charger Modbus TCP protocol V1.6; plausible on one device: owner's diagnostics report, [#431](https://github.com/Gr33ndev/OpenAmpere/issues/431) |
| 0x3001 | Maximum charging current (the current setpoint); outside a charge the charger resets it (behaviour below; whether at once, so that a read-back right after the write already shows the reset value, is open), so OpenAmpere does not take it as the set current and writes it again as soon as the car charges; matched the maximum current (0x1013) on one idle device | U16, ×0.1 A | R FC03, W FC16 | documented; read plausible on one device | FoxESS EV Charger Modbus TCP protocol V1.6; owner's diagnostics report, [#431](https://github.com/Gr33ndev/OpenAmpere/issues/431) |
| 0x3002 | Maximum charging power (the power setpoint); without a phase switch box it also selects the phases: below 1.4 kW paused, from 1.4 kW one phase, from 4.2 kW three phases. Matched the maximum power (0x1011) on one idle device | U16, ×0.1 kW | R FC03, W FC16 | documented; read plausible on one device | FoxESS EV Charger Modbus TCP protocol V1.6; owner's diagnostics report, [#431](https://github.com/Gr33ndev/OpenAmpere/issues/431) |
| 0x3003 | Allowed charging time; read as 0xFFFF (probably unset) on one device, not used by OpenAmpere | U16, minutes | R FC03, W FC16 | documented | FoxESS EV Charger Modbus TCP protocol V1.6; reading: owner's diagnostics report, [#431](https://github.com/Gr33ndev/OpenAmpere/issues/431) |
| 0x3004 | Allowed charging energy; read as 0xFFFF (probably unset) on one device, not used by OpenAmpere | U16, kWh | R FC03, W FC16 | documented | FoxESS EV Charger Modbus TCP protocol V1.6; reading: owner's diagnostics report, [#431](https://github.com/Gr33ndev/OpenAmpere/issues/431) |
| 0x3005 | Validity of a command: a setpoint must be written again within this time (the watchdog) | U16, s, 10–60 | R FC03, W FC16 | confirmed | FoxESS EV Charger Modbus TCP protocol V1.6; plausible on one device: owner's diagnostics report, [#431](https://github.com/Gr33ndev/OpenAmpere/issues/431) |
| 0x3006 | Fallback current: the charger charges at this current when a Modbus controller stops writing | U16, ×0.1 A, 6–32 A | R FC03, W FC16 | confirmed | FoxESS EV Charger Modbus TCP protocol V1.6; plausible on one device: owner's diagnostics report, [#431](https://github.com/Gr33ndev/OpenAmpere/issues/431) |
| 0x3007–0x3009 | Read in the separate block (missing on single-phase models), not interpreted | – | R FC03 | open | [foxess.py](../../../src/openampere/wallbox/foxess.py) |
| 0x300A | Automatic 1/3-phase switching | U16, 0/1 | R FC03, W FC16 | confirmed | FoxESS EV Charger Modbus TCP protocol V1.6; plausible on one device: owner's diagnostics report, [#431](https://github.com/Gr33ndev/OpenAmpere/issues/431) |
| 0x300B | Minimum interval between phase switches; the simulated control switches phases at most this often ([#440](https://github.com/Gr33ndev/OpenAmpere/issues/440)) | U16, minutes, 5–30 | R FC03, W FC16 | confirmed | FoxESS EV Charger Modbus TCP protocol V1.6; plausible on one device: owner's diagnostics report, [#431](https://github.com/Gr33ndev/OpenAmpere/issues/431) |
| 0x4000 | Electronic lock of the plug (not the work mode) | U16 | W FC06 | documented | FoxESS EV Charger Modbus TCP protocol V1.6 |
| 0x4001 | Session control: 0 no action, 1 start, 2 stop | U16 | W FC06 | documented; used by evcc's driver on real devices | FoxESS EV Charger Modbus TCP protocol V1.6; [evcc-io/evcc#26218](https://github.com/evcc-io/evcc/discussions/26218), [evcc-io/evcc#31412](https://github.com/evcc-io/evcc/pull/31412) |
| 0x4002 | Phase sequence switching, only with an external phase switch box | U16 | W FC06 | documented | FoxESS EV Charger Modbus TCP protocol V1.6 |
| 0x4003 | Restart | U16 | W FC06 | documented | FoxESS EV Charger Modbus TCP protocol V1.6 |

The registers marked confirmed gave plausible values in the check on one three-phase charger of an EKD system, idle
with no car plugged in ([#431](https://github.com/Gr33ndev/OpenAmpere/issues/431)).
According to the document, read/write registers are written with function code 16 and write-only registers with
function code 6. OpenAmpere has not written any of them yet; evcc's driver (since July 2026) writes them on real
devices ([evcc-io/evcc#31412](https://github.com/evcc-io/evcc/pull/31412)).
Values while charging and single-phase models are not confirmed yet.

## Behaviour

| Fact | Status | Source |
|---|---|---|
| Modbus TCP over LAN or WLAN; port 502 according to the protocol, 1502 seen in at least one integration; unit id 1 by default; registers are holding registers read with FC03; read/write registers are written with FC16, the write-only registers 0x4000–0x4003 with FC06. | documented | FoxESS EV Charger Modbus TCP protocol V1.6; [#325](https://github.com/Gr33ndev/OpenAmpere/issues/325) |
| The charger announces Modbus TCP over mDNS as `EVC-<serial>._modbus._tcp`. | derived | [#325](https://github.com/Gr33ndev/OpenAmpere/issues/325), [#327](https://github.com/Gr33ndev/OpenAmpere/issues/327), [probe.py](../../../src/openampere/probe.py) |
| The wallbox that came with EKD (Ampere.IQ) systems is this charger: owners' wallboxes announce themselves as `EVC-<serial>` and are found by OpenAmpere's search; owners know it as "the EKD wallbox". | derived | owner reports, [#412](https://github.com/Gr33ndev/OpenAmpere/issues/412) |
| With evcc's `foxess-evc` template one owner's charger closed the connection at once (`connection reset by peer`). Likely causes: not in the Modbus TCP mode, or another client holds the connection (on EKD systems probably the previous smartbox, see below). | open | owner report, [#412](https://github.com/Gr33ndev/OpenAmpere/issues/412) |
| Register lists of other charger families (e.g. holding registers 100, 300, 400) do not apply; an evcc `custom` charger built from them only timed out. | derived | owner report, [#412](https://github.com/Gr33ndev/OpenAmpere/issues/412) |
| Three communication modes, set in the Fox Switch app, one at a time: default (FoxESS app/cloud with PV linkage), Modbus TCP, OCPP. | documented | Fox Switch app manual V1.0.1; [#318](https://github.com/Gr33ndev/OpenAmpere/issues/318) |
| In default mode the charger can be wired over RS485 to a FoxESS inverter (H3, H3 Pro, H3 Smart, see [h3.md](h3.md)) or a shared meter and charges from surplus by itself: Fast (full current), Economic (solar first, at least 6 A from the grid), Green (surplus only). FoxESS requires self-use mode, no export limit and the order house → car → battery (the home battery may discharge into the car). | documented | FoxESS energy management user manual; [#318](https://github.com/Gr33ndev/OpenAmpere/issues/318) |
| In default mode Modbus TCP is read-only according to the Fox Switch app manual (section 8.6). | documented | Fox Switch app manual V1.0.1; [#318](https://github.com/Gr33ndev/OpenAmpere/issues/318) |
| Outside the "Modbus TCP" mode some firmware answers every read with exception 6 ("server device busy"). | derived | evcc discussions; [#318](https://github.com/Gr33ndev/OpenAmpere/issues/318), [#325](https://github.com/Gr33ndev/OpenAmpere/issues/325) |
| In Modbus TCP mode the watchdog fails in the unsafe direction: when the controller stops writing, the charger does not stop; after about 60 s it charges at its stored default current or at full power. Only an explicit stop ends charging, and a power setpoint written after a stop can restart it. Cost risk only; the charger stays within its rated limits. | derived | owner reports from other projects, [#318](https://github.com/Gr33ndev/OpenAmpere/issues/318) |
| A controller starts, stops and sets the current or power (0x3001/0x3002, 0x4001) only in work mode 0 ("controlled"); in plug and charge the charger starts by itself, and locked allows nothing. | derived | FoxESS EV Charger Modbus TCP protocol V1.6; [#318](https://github.com/Gr33ndev/OpenAmpere/issues/318) |
| A setpoint (0x3001/0x3002) must be written again within the validity (0x3005); otherwise the charger treats the controller as gone and charges with the fallback. | derived | owner reports in [evcc-io/evcc#26218](https://github.com/evcc-io/evcc/discussions/26218) |
| A setpoint written after a stop (0x4001 = 2) starts charging again without a start command. A controller must not refresh a setpoint while it has stopped the charger. | derived | owner reports in [evcc-io/evcc#26218](https://github.com/evcc-io/evcc/discussions/26218) |
| A current setpoint takes effect only while charging; outside a session the charger resets it. | derived | owner reports in [evcc-io/evcc#26218](https://github.com/evcc-io/evcc/discussions/26218) |
| Some chargers suddenly report work mode 2 ("locked", status 8); the previous app then shows the wallbox as locked and cannot change it. | derived | owner report ([#464](https://github.com/Gr33ndev/OpenAmpere/issues/464)) |
| Current and power setpoints are held in RAM and lost on a power cut; configuration values are probably stored. Automatic 1/3-phase switching follows the power setpoint. | derived | [#318](https://github.com/Gr33ndev/OpenAmpere/issues/318) |
| The previous smartbox of EKD (Ampere.IQ) systems is a Modbus TCP client of the charger: its settings page lists the charger as "Ampere.ChargePro Bridge", Modbus TCP, host `EVC-<serial>.local`, unit id 1, port 502, marked as not configurable. So the box competes with OpenAmpere and evcc for the charger's connection(s). | derived | an owner's screenshot of the box's settings page, [#427](https://github.com/Gr33ndev/OpenAmpere/issues/427); [#412](https://github.com/Gr33ndev/OpenAmpere/issues/412) |
| While the previous smartbox is powered, OpenAmpere cannot read the charger: the connection on port 502 is refused, or it is accepted but the answers carry foreign transaction ids and then time out. With the box cut from power the check reads all blocks at once on port 502, unit id 1, FC03; port 1502 refused. Whether the charger allows only one client or mishandles concurrent clients is open. | confirmed (one device) | owner's diagnostics report, [#431](https://github.com/Gr33ndev/OpenAmpere/issues/431) |
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
| The check tells apart a name that cannot be resolved (outcome `unresolved`: enter the IP address; `.local` names never resolve in the Home Assistant app), a refused connection (`connection_refused`: the address answers but takes no Modbus connection, so it is another device or another client such as the previous smartbox or evcc holds the connection) and no answer at all (`unreachable`). Why a port failed is recorded per port in the report; the plain connection that tells refused from timed out is made only after the Modbus client failed. | Owners copy the box's `EVC-<serial>.local` name, and one outcome for all three cases gave them no hint what to do. | [#427](https://github.com/Gr33ndev/OpenAmpere/issues/427) |
| The check's texts for a refused connection, a connection without a proper answer and a connection closed at once name the previous smartbox first (unplug it from power for the check), then evcc, then another device or a wrong unit id. | The box blocked the check on a real device. | [#431](https://github.com/Gr33ndev/OpenAmpere/issues/431) |
| The setup's network search marks a host that announces `EVC-<serial>._modbus._tcp` as a FoxESS wallbox, does not open a Modbus connection to it and does not offer it as the inverter. A busy device it cannot identify is called "Modbus-Gerät, gerade belegt", and the hint names the wallbox held by the previous smartbox. In the Home Assistant app there is no mDNS, so only the neutral label applies. | While the box was powered, the charger showed up in the search as "Wechselrichter, gerade belegt" next to the real inverter, and the owner doubted which address is the wallbox. | [#433](https://github.com/Gr33ndev/OpenAmpere/issues/433) |
| OpenAmpere assumes the charger allows one Modbus connection: never evcc and OpenAmpere on the same charger. | The number of connections is unknown. | [#318](https://github.com/Gr33ndev/OpenAmpere/issues/318) |
| The charger is a wallbox OpenAmpere shows live, read only: a model in `wallbox/native.py` whose only mode is "manual". The other modes are refused when saved or chosen and offered neither in the app nor in Home Assistant (sensors only; no current sensor, the setpoint is not known). There is no write path: its Modbus connection can only read with FC03, and a test fails on any write request. Its power counts in the house load like any wallbox, but the surplus control never acts on it. The other modes later became a simulation (row for #440 below), and the work mode became writable on request (row for #464 below). | No write register is confirmed, the watchdog fails in the unsafe direction, and § 14a is not settled. | [#439](https://github.com/Gr33ndev/OpenAmpere/issues/439), [#318](https://github.com/Gr33ndev/OpenAmpere/issues/318) |
| The live view opens one short connection per poll (every 10 s; after a failure again after 15 s, 30 s, … up to 5 minutes) and closes it after reading. Values older than 35 s are not used. The wallbox check, the device reader and the connection test of the setup take turns with the poll of a charger set up at the same address and port, so OpenAmpere never holds two connections to it. A wallbox at the inverter's address and port is refused in the setup. | The number of connections the charger allows is unknown, and the previous smartbox holds it on EKD systems. | [#439](https://github.com/Gr33ndev/OpenAmpere/issues/439), [#431](https://github.com/Gr33ndev/OpenAmpere/issues/431) |
| In the live view the status, not 0x1005, tells whether a car is plugged in (status 1–5 and 9). Status 9 counts as charging, 8 (locked) as unknown with the work mode "locked". The power is the sum of voltage × current of the phases, the power register (0.1 kW) only without current; a charge counts from 100 W. | 0x1005 read 1 without a car; 0.1 kW steps are coarse. | [#439](https://github.com/Gr33ndev/OpenAmpere/issues/439), [#431](https://github.com/Gr33ndev/OpenAmpere/issues/431) |
| A charger that does not answer is shown as not reachable without old values; the app names the previous smartbox first (it must stay cut from power for the live view), then evcc or another program. | Like the check, where the box blocked the charger on a real device. | [#439](https://github.com/Gr33ndev/OpenAmpere/issues/439), [#431](https://github.com/Gr33ndev/OpenAmpere/issues/431) |
| The live view does not read the energy counters, the card number, the model code and the serial number (0x1016–0x1031). | Not needed for the display; they identify the owner. | [#439](https://github.com/Gr33ndev/OpenAmpere/issues/439) |
| The modes off, solar, min + solar and fast are offered as a simulation only: `native.py` decides as for the dé wallbox (surplus, margin, two checks in a row, minimum on and off time, raising at most every 30 s, the owner's waiting time) with the charger's own limits (0x1013, 0x1014), and logs every start and stop it would have made, marked as a simulation and not sent, whatever the master switch and the test mode say. Changes of the current are collected and logged at most every 5 minutes per wallbox as one entry (the current then and the lowest and highest current since the entry before); a collected change is logged before the next stop or the end of the plan, and nothing while nothing changes, so the simulation, which also runs with the master switch off, does not bury real changes in the control log (review of [#443](https://github.com/Gr33ndev/OpenAmpere/pull/443)). Without the owner's yes to real control the model stays read only for charging: its Modbus connection for reading still cannot write, and a test fails on any write request (the work mode is written on request, row for #464 below; real control, row for #465 below). The plan is kept in memory, taken over from the charger when a car is plugged in, and dropped with the mode "manual" or a restart. A charge the charger started by itself counts as its own; letting go of a charge the simulation started logs the explicit stop real control would send. Phases follow the automatic 1/3-phase switching (0x300A): one phase below the lowest three-phase power (6 A × 3), three from there, switched at most once per the charger's interval (0x300B, 5 minutes if not told). The plan never counts as load or takes surplus from other devices; the charger's real power stays in the house load. Home Assistant gets no mode for it. | Shows on real data how the control would behave (when it would set what, how often, compared with what the charger does by itself) before anything may be written. The phase rule is derived from the protocol (switching follows the power setpoint), not confirmed on a device; the 5 minutes used when the charger does not report 0x300B are an assumption of OpenAmpere (open). The heartbeat and the fallback current (0x3006) are not simulated: they only matter once OpenAmpere writes (row for #465 below). | [#440](https://github.com/Gr33ndev/OpenAmpere/issues/440), [#318](https://github.com/Gr33ndev/OpenAmpere/issues/318) |
| Register addresses are used as facts only, nothing copied from the FoxESS document; evcc's FoxESS driver (not MIT) and two MIT Home Assistant integrations that derive from a project without a license are not used. | License and permission of the sources. | [#318](https://github.com/Gr33ndev/OpenAmpere/issues/318), [#325](https://github.com/Gr33ndev/OpenAmpere/issues/325), [#327](https://github.com/Gr33ndev/OpenAmpere/issues/327) |
| Decided for control over Modbus (#318), done in #465: a one-time fallback current of 6 A with the owner's consent, an explicit stop on planned shutdown, a heartbeat of 30 s or less, an explanation in the app and an exception noted in AGENTS.md. OpenAmpere does not take on the § 14a role; the charger's own dimming input takes precedence and OpenAmpere never works around it. Whether the charger reports a dimming by the grid operator over Modbus is open, so OpenAmpere cannot yet hold back a higher current while it dims. | Fail-safe behaviour despite the unsafe watchdog; legal topics are settled in the issue first. | [#318](https://github.com/Gr33ndev/OpenAmpere/issues/318) |
| The work mode (0x3000) is the one register OpenAmpere writes to the charger: chosen by the owner in the app after a question, with function code 16, only with the master switch on (logged, not sent in test mode), read back and logged. A ModbusWallbox writes only the registers it was made with (`writable`); the poll and the check get none. | A charger that went "locked" cannot be released with the previous app; a stored setting needs no watchdog. | [#464](https://github.com/Gr33ndev/OpenAmpere/issues/464) |
| Real control (#465) only per wallbox after the owner agreed in the app, as a test: the modes off, solar, min + solar and fast write the current (0x3001, FC16) and start or stop (0x4001, FC06), only in work mode "controlled". Before the first write after a (re)connection 0x3005 = 60 s and 0x3006 = 6 A, nothing else written unless both read back; real control is switched on and off only with the question in the app, never by saving the wallbox list; test mode during a charge OpenAmpere started stops it and lets go; while the car charges the current is written again every 25 s (not logged), never after a stop; the current written before start or stop; on a planned shutdown and when OpenAmpere lets go (also the mode "manual") a charge it started is stopped; a stop never waits for the fallback current to be set again. Under real control OpenAmpere refuses any work mode other than "controlled"; a change outside OpenAmpere during a charge it started makes it send a stop and let go. A charger that does not read back 0x3005/0x3006 is not controlled for 15 minutes, and a charge OpenAmpere started is stopped. A lower current the charger keeps while charging (a limit of its own, or perhaps a dimming) counts as its highest current for 15 minutes, until the mode changes or OpenAmpere lets go; then the mode's current is asked again. No phase switching yet (0x3002 untouched). | The watchdog fails unsafe, so the owner decides; the pitfalls reported by owners (#463). | [#318](https://github.com/Gr33ndev/OpenAmpere/issues/318), [#465](https://github.com/Gr33ndev/OpenAmpere/issues/465) |

## Open questions

- A write by OpenAmpere (work mode 0x3000, [#464](https://github.com/Gr33ndev/OpenAmpere/issues/464)) is not yet
  confirmed on a real device. Open with it: whether the status leaves 8 ("locked") as soon as the work mode is no
  longer 2 (the simulator assumes so), and which exception a charger outside the "Modbus TCP" mode sends for a write
  (the app's text assumes 1, 2, 3 or 6).

- **Conflict, default mode:** the Fox Switch app manual says Modbus TCP is read-only in default mode; evcc reports that
  some firmware refuses every read with exception 6 outside the Modbus TCP mode. Only a real device can settle whether
  reads work in default mode ([#318](https://github.com/Gr33ndev/OpenAmpere/issues/318),
  [#325](https://github.com/Gr33ndev/OpenAmpere/issues/325)).
- Number of simultaneous Modbus connections: while the previous smartbox is powered OpenAmpere cannot read the
  charger, but whether it allows only one client or mishandles concurrent clients is open ([#431](https://github.com/Gr33ndev/OpenAmpere/issues/431)).
- Whether the previous smartbox only reads the charger or also controls it ([#431](https://github.com/Gr33ndev/OpenAmpere/issues/431)).
- Meaning of 0x1005, which read 1 while idle with no car plugged in; needs a reading with a car plugged in
  ([#431](https://github.com/Gr33ndev/OpenAmpere/issues/431)).
- Meaning of alarm bit 1 in 0x1015, set on one device while idle ([#431](https://github.com/Gr33ndev/OpenAmpere/issues/431)).
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
- Meaning of the read but uninterpreted words (0x1000, 0x1002, 0x1004, 0x1006–0x1007, 0x100F–0x1010, 0x3007–0x3009).
- No write by OpenAmpere tested on a real device yet: work mode, setpoints, start and stop
  ([#464](https://github.com/Gr33ndev/OpenAmpere/issues/464), [#465](https://github.com/Gr33ndev/OpenAmpere/issues/465)).
- Whether a stop (0x4001 = 2) has any effect once the work mode is no longer "controlled", and whether the watchdog
  (0x3005) still applies in plug and charge ([#465](https://github.com/Gr33ndev/OpenAmpere/issues/465)).
- Why a charger switches to "locked" by itself ([#464](https://github.com/Gr33ndev/OpenAmpere/issues/464)).

## Sources

- FoxESS EV Charger Modbus TCP protocol V1.6: given to owners by FoxESS support; an owner posted it in a public evcc
  discussion ([evcc-io/evcc#26218](https://github.com/evcc-io/evcc/discussions/26218)). Marked "all rights
  reserved". Following the [README](../README.md#sources), only facts (register addresses, meanings, units) are
  used, in own words; the document is not quoted, not added to the repository and not passed on
  ([#318](https://github.com/Gr33ndev/OpenAmpere/issues/318)).
- FoxESS Fox Switch app manual V1.0.1 and FoxESS energy management user manual (as summarised in
  [#318](https://github.com/Gr33ndev/OpenAmpere/issues/318)).
- Owner reports in public evcc discussions, among them [evcc-io/evcc#26218](https://github.com/evcc-io/evcc/discussions/26218) (facts only).
- evcc's Modbus driver `foxess-evc` ([evcc-io/evcc#31412](https://github.com/evcc-io/evcc/pull/31412)): not MIT
  licensed ("all rights reserved"), its code is not used; it only shows that the write registers work on real
  devices.
- An owner's diagnostics report from the wallbox check ([#431](https://github.com/Gr33ndev/OpenAmpere/issues/431)); not linked; only facts are used, nothing that
  identifies the device.
- OpenAmpere issues and pull requests linked above; code: [wallbox/foxess.py](../../../src/openampere/wallbox/foxess.py),
  [probe.py](../../../src/openampere/probe.py).
