# SAJ H2, HS2, AS2 (family range of type codes)

> Not official manufacturer information: compiled by the OpenAmpere contributors, can be incomplete or wrong. Changing
> inverter settings can affect the warranty and grid rules; no affiliation with SAJ or EKD; not legal advice. See the
> [device knowledge README](../README.md).

**Applies to:** SAJ hybrid inverters H2, HS2 and AS2, every type code 0x0050–0x006F in register 0x8F00 · **Sold as:**
"Ampere.StoragePro" (H2, HS2 and the three-phase 0x005E; for the AS2 not stated in the sources) · **OpenAmpere:**
driver `saj` (`src/openampere/drivers/saj/`), register map `saj_h2` · **Support:** display for the whole family range;
control (battery settings, export limit, grid charging) for 0x005A–0x005E since 0.29.0 · **Confirmed on a real
device:** partly (identification, battery limits and battery temperature; the signs of battery and grid power and
OpenAmpere's own writes not yet)

Type-specific page: [0x005E, "3P-X E2"](0x005e-3p-x-e2.md).

## Summary

- One register map for the whole family; the type code in 0x8F00 decides the name and whether control is enabled.
  Unknown codes in the family range are read like an H2.
- The live and counter registers come from community projects; the control registers from SAJ's
  "H2-3~6K-S2 single phase communication protocol" (2022-12-02). The battery limits 0x3644/0x3645 were matched with
  the manufacturer's app on a real device ([#334](https://github.com/Gr33ndev/OpenAmpere/issues/334)).
- Three-phase types have summed grid counters (0x4167 ff.); some firmware lacks them, then the L1 counters are used.
- SAJ has **no remote control with a watchdog**. Passive mode stays on until it is changed, so OpenAmpere charges from
  the grid through a short charge slot of the time-of-use mode (slot 7) that it keeps extending; if OpenAmpere stops,
  charging ends with the slot, at most 10 minutes later.
- The work mode "backup" has its own reserve (0x3651); the reserve of normal operation (0x3644) does not apply there.
- Still open: the signs of battery and grid power, and whether the PV counter covers all MPPTs.

## Registers

All reads use FC03, all writes FC06 (one register at a time). Device address (unit id) 1 or 2, port 502.
"Community" means the projects listed under [Sources](#sources).

### Identification

| Register | Meaning | Type, scale, sign | R/W | Status | Source |
|---|---|---|---|---|---|
| 0x8F00 | Device type code. Names: 0x0055 "AS2 3-6K", 0x0056 "AS2 5-10K", 0x005A "H2 3-6K S2", 0x005B "H2 5-10K S3", 0x005C "HS2 3-6K S2", 0x005D "HS2 5-10K S3", 0x005E "3P-X E2" (not in the protocol, see [its page](0x005e-3p-x-e2.md)) | U16 | R | confirmed | H2 protocol (0x0055–0x005D); community; read on a real 0x005E ([#334](https://github.com/Gr33ndev/OpenAmpere/issues/334), [#339](https://github.com/Gr33ndev/OpenAmpere/issues/339), [#379](https://github.com/Gr33ndev/OpenAmpere/issues/379)) |
| 0x8F01 | Rated power; used for the export limit and the charging power in % | U16, W | R | open | OpenAmpere's reading since [#379](https://github.com/Gr33ndev/OpenAmpere/issues/379), without a source; stanus74 names it "sub type" ([#492](https://github.com/Gr33ndev/OpenAmpere/issues/492)) |
| 0x8F02 | Communication version (read with the block, not shown) | U16 | R | derived | community |
| 0x8F03–0x8F0C | Serial number | 10 registers ASCII | R | derived | community |
| 0x8F0D–0x8F16 | Product code; shown as serial number if the serial is empty | 10 registers ASCII | R | derived | community |
| 0x8F17–0x8F1C | Firmware versions; the first three are shown as value / 1000 | 6 × U16 | R | derived | community |

### Status, clock and temperatures

| Register | Meaning | Type, scale, sign | R/W | Status | Source |
|---|---|---|---|---|---|
| 0x4000–0x4003 | Inverter clock: year; month << 8 \| day; hour << 8 \| minute; second << 8. Charge slots follow this clock | 4 × U16 | R | documented | H2 protocol; [#377](https://github.com/Gr33ndev/OpenAmpere/issues/377), [#378](https://github.com/Gr33ndev/OpenAmpere/issues/378) |
| 0x4004 | Running mode: 0 init, 1 wait, 2 run, 3 off-grid (power cut), 4 on-grid storage, 5 fault, 6 update, 7 test, 8 self-check, 9 reset. Also part of the detection check | U16 | R | derived | community |
| 0x4005–0x4006 | Fault word 1 | U32 | R | derived | community |
| 0x4007–0x4008 | Fault word 2 | U32 | R | derived | community |
| 0x4009–0x400A | Fault word 3 | U32 | R | derived | community |
| 0x4010 | Inverter (heat sink) temperature | I16, ×0.1 °C | R | derived | community |
| 0x4011 | Ambient temperature | I16, ×0.1 °C | R | derived | community |

### Read-back of the battery settings

| Register | Meaning | Type, scale, sign | R/W | Status | Source |
|---|---|---|---|---|---|
| 0x4022 | Work mode (AppMode) read-back; used for the work mode only if 0x3644–0x3647 cannot be read | U16 | R | derived | community |
| 0x4029 | Charge limit read-back; used only if 0x3644–0x3647 cannot be read. Which setting it mirrors was not established by the report in [#334](https://github.com/Gr33ndev/OpenAmpere/issues/334) | U16, % SoC | R | open | community; [#334](https://github.com/Gr33ndev/OpenAmpere/issues/334), [#337](https://github.com/Gr33ndev/OpenAmpere/issues/337) |
| 0x402A | "BatDisSocDowLimit" / set SoC_L: on-grid limit (backup reserve) read-back; used only if 0x3644–0x3647 cannot be read. Equalled 0x3644 on a real device. Up to 0.25.0 it was wrongly read as the off-grid limit | U16, % SoC | R | confirmed | H1 protocol (applies to grid-connected operation); [#334](https://github.com/Gr33ndev/OpenAmpere/issues/334), [#335](https://github.com/Gr33ndev/OpenAmpere/issues/335) |
| 0x402B | Labelled "dod" in the diagnostics report; read only there, meaning unknown | U16, % | R (diagnostics) | open | [#337](https://github.com/Gr33ndev/OpenAmpere/issues/337) |
| 0x402C | "BatResSoc", reserved SoC: read with the block but not used. Up to 0.25.0 it was wrongly read as the backup reserve | U16, % SoC | R | open | community; [#334](https://github.com/Gr33ndev/OpenAmpere/issues/334), [#335](https://github.com/Gr33ndev/OpenAmpere/issues/335) |

### Battery, PV and power

| Register | Meaning | Type, scale, sign | R/W | Status | Source |
|---|---|---|---|---|---|
| 0x4069 | Battery voltage | U16, ×0.1 V | R | derived | community |
| 0x406A | Battery current | I16, ×0.01 A | R | derived | community |
| 0x406E | Battery temperature (fallback 0xA010) | I16, ×0.1 °C | R | confirmed | community; matched another integration on a real device ([#372](https://github.com/Gr33ndev/OpenAmpere/issues/372), [#376](https://github.com/Gr33ndev/OpenAmpere/issues/376)) |
| 0x406F | State of charge; also part of the detection check (≤ 10000) | U16, ×0.01 % | R | derived | community |
| 0x4071 / 0x4072 / 0x4073 | PV1 voltage / current / power | U16 ×0.1 V / U16 ×0.01 A / U16 W | R | derived | community |
| 0x4074 / 0x4075 / 0x4076 | PV2 voltage / current / power | as PV1 | R | derived | community |
| 0x4077 / 0x4078 / 0x4079 | PV3 voltage / current / power | as PV1 | R | derived | community |
| 0x407A / 0x407B / 0x407C | PV4 voltage / current / power | as PV1 | R | derived | community |
| 0x40A0 | House consumption | I16, W | R | derived | community |
| 0x40A3 | Power measured by a second meter at the inverter (Meter B, "CT PV power"): an extra PV inverter without its own connection. Not part of 0x40A5; on one device the balance only closed with it. Negative values (standby at night, meter direction) are counted as 0. | I16, W | R | derived | stanus74/home-assistant-saj-h2-modbus ("CT_PVPowerWatt"); one owner's measurements, [#419](https://github.com/Gr33ndev/OpenAmpere/issues/419) |
| 0x40A5 | PV power of the inverter itself, total (if missing, the sum of PV1–4); without Meter B (0x40A3) | I16, W | R | derived | community |
| 0x40A6 | Battery power | I16, W, + = discharging | R | open | evcc-io/evcc saj-h2 template; sign not yet checked on a device |
| 0x40AD | Grid power (system meter) | I16, W, + = import | R | open | evcc-io/evcc saj-h2 template; sign not yet checked on a device |

### Energy counters

All U32 (two registers), 0.01 kWh (OpenAmpere: ×10 → Wh, counter step 10 Wh).

| Register | Meaning | Type, scale, sign | R/W | Status | Source |
|---|---|---|---|---|---|
| 0x40BF / 0x40C5 | PV energy today / total. Whether it covers all MPPTs is open | U32, 0.01 kWh | R | derived | community |
| 0x40C7 / 0x40CD | Battery charge today / total | U32, 0.01 kWh | R | derived | community |
| 0x40CF / 0x40D5 | Battery discharge today / total | U32, 0.01 kWh | R | derived | community |
| 0x40DF / 0x40E5 | House consumption today / total | U32, 0.01 kWh | R | derived | community |
| 0x40EF / 0x40F5 | Grid export today / total, phase L1 (SAJ calls it "sell") | U32, 0.01 kWh | R | derived | community |
| 0x40F7 / 0x40FD | Grid import today / total, phase L1 (SAJ calls it "feed-in") | U32, 0.01 kWh | R | derived | community |
| 0x4167 / 0x416D | Grid import today / total, all three phases; missing on some firmware | U32, 0.01 kWh | R | derived | community |
| 0x416F / 0x4175 | Grid export today / total, all three phases; missing on some firmware | U32, 0.01 kWh | R | derived | community |

### Battery modules (BMS)

| Register | Meaning | Type, scale, sign | R/W | Status | Source |
|---|---|---|---|---|---|
| 0xA000 | Number of battery modules (read with the block, not used) | U16 | R | derived | community |
| 0xA00D | Battery state of health | U16, ×0.01 % | R | derived | community |
| 0xA010 | Temperature of the first battery module; fallback for 0x406E | I16, ×0.1 °C | R | derived | community; [#376](https://github.com/Gr33ndev/OpenAmpere/issues/376) |

### Settings and control

| Register | Meaning | Type, scale, sign | R/W | Status | Source |
|---|---|---|---|---|---|
| 0x3604 | Charge slot enable mask: bit n switches charge slot n+1 on. OpenAmpere uses bit 6 (slot 7) | U16, bit mask | R/W | documented | H2 protocol; stanus74 `charge_control.py`; evcc `saj-h2.yaml`; [#377](https://github.com/Gr33ndev/OpenAmpere/issues/377) |
| 0x3606–0x3617 | Charge slots 1–6 (three registers each, layout as slot 7). Not read or written by OpenAmpere; stanus74 and evcc use slot 1 (0x3606–0x3608) | 3 × U16 per slot | – | documented | H2 protocol; stanus74; evcc |
| 0x3618 | Charge slot 7 start: hour << 8 \| minute | U16 | R/W | documented | H2 protocol; [#378](https://github.com/Gr33ndev/OpenAmpere/issues/378) |
| 0x3619 | Charge slot 7 end: hour << 8 \| minute | U16 | R/W | documented | H2 protocol; [#378](https://github.com/Gr33ndev/OpenAmpere/issues/378) |
| 0x361A | Charge slot 7 days (high byte, bit 0 = Monday) and power in % of rated power (low byte) | U16 | R/W | documented | H2 protocol; [#378](https://github.com/Gr33ndev/OpenAmpere/issues/378) |
| 0x3636–0x363A | Passive mode registers. Not read or written: no timeout (see Behaviour) | – | – | documented | H2 protocol; stanus74/home-assistant-saj-h2-modbus#157 |
| 0x3644 | On-grid discharge depth = backup reserve, lowest SoC in normal operation (self-use). In % SoC, not depth of discharge. Matched the manufacturer's app on a real device; the previous IQ box writes it as the reserve. OpenAmpere's write not yet confirmed on a device | U16, % SoC | R/W | confirmed | H2 protocol; stanus74 (name); [#334](https://github.com/Gr33ndev/OpenAmpere/issues/334), [#335](https://github.com/Gr33ndev/OpenAmpere/issues/335), [#337](https://github.com/Gr33ndev/OpenAmpere/issues/337); stanus74/home-assistant-saj-h2-modbus#18 |
| 0x3645 | Off-grid discharge depth: lowest SoC during a power cut. In % SoC. Matched the manufacturer's app on a real device; OpenAmpere's write not yet confirmed | U16, % SoC | R/W | confirmed | H2 protocol; stanus74 (name); [#334](https://github.com/Gr33ndev/OpenAmpere/issues/334), [#335](https://github.com/Gr33ndev/OpenAmpere/issues/335), [#337](https://github.com/Gr33ndev/OpenAmpere/issues/337) |
| 0x3646 | Charge limit (highest SoC). Read on a real device as part of the block; OpenAmpere's write not yet confirmed | U16, % SoC | R/W | documented | H2 protocol; stanus74 `charge_control.py`; evcc `saj-h2.yaml`; [#334](https://github.com/Gr33ndev/OpenAmpere/issues/334), [#378](https://github.com/Gr33ndev/OpenAmpere/issues/378) |
| 0x3647 | Work mode (AppMode): 0 self-use, 1 time-of-use, 2 backup, 3 passive, 10 peak shaving. Read on a real device as part of the block | U16 | R/W | documented | H2 protocol; stanus74; evcc; [#334](https://github.com/Gr33ndev/OpenAmpere/issues/334), [#377](https://github.com/Gr33ndev/OpenAmpere/issues/377) |
| 0x3651 | "BatSocLimitkeep": reserve that applies in work mode backup (AppMode 2). On a real device it held a value of its own, different from 0x3644 | U16, % SoC | R/W | documented | H2 protocol; evcc `saj-h2.yaml`; [#334](https://github.com/Gr33ndev/OpenAmpere/issues/334), [#380](https://github.com/Gr33ndev/OpenAmpere/issues/380), [#381](https://github.com/Gr33ndev/OpenAmpere/issues/381) |
| 0x365A | Export limit in per mille of the rated power; applies only while 0x365C is 1 or 3 | U16, ‰ of 0x8F01 | R/W | documented | H2 protocol; stanus74; evcc; [#377](https://github.com/Gr33ndev/OpenAmpere/issues/377) |
| 0x365B | Export limit by phase current, used with 0x365C = 2. Read with 0x365A–0x365C, never written | U16, A | R | documented | H2 protocol; [#378](https://github.com/Gr33ndev/OpenAmpere/issues/378) |
| 0x365C | Export limit ("anti-reflux") mode: 0 off, 1 total power, 2 phase current, 3 power per phase | U16 | R/W | documented | H2 protocol; [#377](https://github.com/Gr33ndev/OpenAmpere/issues/377) |
| 0x365F | Time-of-use behaviour outside the charge slots: 0 standby, 1 self-use | U16 | R/W | documented | H2 protocol; [#378](https://github.com/Gr33ndev/OpenAmpere/issues/378) |
| 0x8300–0x8306 | Undocumented block; answers reads; the previous IQ box writes it every 1–3 s while charging. Not read or written by OpenAmpere | – | – | open | stanus74/home-assistant-saj-h2-modbus#18; research in [#377](https://github.com/Gr33ndev/OpenAmpere/issues/377) |

## Behaviour

| Fact | Status | Source |
|---|---|---|
| Modbus TCP on port 502; the device address is 1 or 2, depending on the installation (communication module). | derived | community; [devices.md](../../devices.md) |
| Names and meanings of the live registers were checked against SAJ's protocols for related inverters (R5/R6/C6 and H1). | derived | [registers.md](../../registers.md) |
| SAJ's H2 protocol lists the type codes 0x0055–0x005D; 0x005E is not in it. | documented | H2 protocol; [#379](https://github.com/Gr33ndev/OpenAmpere/issues/379) |
| A type code does not identify one power class: 0x005B covers 5 to 10 kW. | documented | H2 protocol; [#379](https://github.com/Gr33ndev/OpenAmpere/issues/379) |
| On a real device (type 0x005E) detection, live values and battery limits read correctly with this map. | confirmed | [#334](https://github.com/Gr33ndev/OpenAmpere/issues/334), [#339](https://github.com/Gr33ndev/OpenAmpere/issues/339) |
| The AS2 (0x0055, 0x0056) is read with the same map; no report from a real AS2 yet. | open | [#347](https://github.com/Gr33ndev/OpenAmpere/issues/347), [#348](https://github.com/Gr33ndev/OpenAmpere/issues/348), [#378](https://github.com/Gr33ndev/OpenAmpere/issues/378) |
| Running mode 3 (0x4004) means off-grid: the inverter supplies the house during a power cut. | derived | community |
| The summed grid counters 0x4167 ff. are missing on some firmware (the block is rejected). | derived | community; [devices.md](../../devices.md) |
| SAJ's counter names are the wrong way round from the owner's view: export is "sell", import is "feed-in". | derived | community |
| The battery limits are 0x3644 (on grid, = backup reserve) and 0x3645 (off grid, during a power cut), both in % SoC. **Turned out wrong:** up to 0.25.0 OpenAmpere read the reserve from 0x402C and the off-grid limit from 0x402A, so on a real device the two values appeared swapped compared with the manufacturer's app. | confirmed | [#334](https://github.com/Gr33ndev/OpenAmpere/issues/334), [#335](https://github.com/Gr33ndev/OpenAmpere/issues/335), [#337](https://github.com/Gr33ndev/OpenAmpere/issues/337) |
| The block 0x3644–0x3647 (on-grid depth, off-grid depth, charge limit, work mode) matched the manufacturer's app on a real device. | confirmed | [#334](https://github.com/Gr33ndev/OpenAmpere/issues/334) (diagnostics report), [#337](https://github.com/Gr33ndev/OpenAmpere/issues/337) |
| Some firmware rejects 0x3644–0x3647; then only the read-back registers 0x4022, 0x4029 and 0x402A are available. | derived | [#335](https://github.com/Gr33ndev/OpenAmpere/issues/335), [#337](https://github.com/Gr33ndev/OpenAmpere/issues/337) |
| In the read-back block of that device 0x402A equalled 0x3644. 0x402C equalled the off-grid depth and not 0x3651, but the off-grid depth had a value an unused register could also show, so this does not settle 0x402C. | confirmed (0x402A), open (0x402C) | [#334](https://github.com/Gr33ndev/OpenAmpere/issues/334), [#335](https://github.com/Gr33ndev/OpenAmpere/issues/335), [#337](https://github.com/Gr33ndev/OpenAmpere/issues/337) |
| Battery temperature 0x406E matched the value of another integration on a real device. (An earlier question whether this value is the battery or the BMS temperature, [#41](https://github.com/Gr33ndev/OpenAmpere/issues/41), is settled by this.) | confirmed | [#372](https://github.com/Gr33ndev/OpenAmpere/issues/372), [#376](https://github.com/Gr33ndev/OpenAmpere/issues/376) |
| SAJ has no "feed-in first" work mode; the modes are self-use, time-of-use, backup, passive and peak shaving. | documented | H2 protocol; [#377](https://github.com/Gr33ndev/OpenAmpere/issues/377) |
| 0x3651 is the reserve of the backup mode. On a real device it held a different value than 0x3644, so the two are separate settings. The backup mode keeps the battery full for a power cut only if 0x3651 is 100 %. | documented; confirmed (separate register) | H2 protocol; [#334](https://github.com/Gr33ndev/OpenAmpere/issues/334), [#380](https://github.com/Gr33ndev/OpenAmpere/issues/380) |
| In backup mode the battery supplies the house only down to 0x3651 and keeps the rest for a power cut; 0x3644 has no effect in that mode. evcc relies on this (backup mode with 0x3651 = lowest SoC for normal operation, = highest SoC to hold the battery). | open | H2 protocol; evcc `saj-h2.yaml`; [#380](https://github.com/Gr33ndev/OpenAmpere/issues/380), [#381](https://github.com/Gr33ndev/OpenAmpere/issues/381) |
| 0x365A limits the export only while 0x365C is 1 (total power) or 3 (per-phase power); with 0 there is no limit, with 2 the limit is a phase current in 0x365B. | documented | H2 protocol; [#377](https://github.com/Gr33ndev/OpenAmpere/issues/377) |
| SAJ has no remote control that ends by itself: the H2 protocol defines no timeout for passive mode (0x3636–0x363A), and owners report that passive mode stays on for hours after one write. | documented (no timeout), derived (owners) | H2 protocol; stanus74/home-assistant-saj-h2-modbus#157; [#377](https://github.com/Gr33ndev/OpenAmpere/issues/377) |
| No public SAJ document (H2 protocol, H1, AS1, R5/R6/C6) has a timeout, watchdog or validity field for passive mode or any power command. SAJ reportedly has an "External Dispatch / EMS control" specification for professional partners only, which OpenAmpere cannot use. | documented (none in public documents), derived (partner specification) | H2 protocol; stanus74/home-assistant-saj-h2-modbus#199 (discussion); research in [#377](https://github.com/Gr33ndev/OpenAmpere/issues/377) |
| Time-of-use charging: AppMode 1, seven charge slots of three registers each from 0x3606 (slot n at 0x3606 + 3 × (n−1)), each with start, end, day mask and power in %, switched on per slot by a bit in 0x3604. Outside the slots the inverter follows 0x365F. | documented | H2 protocol; [#377](https://github.com/Gr33ndev/OpenAmpere/issues/377) |
| Charge slots run on the inverter's clock (0x4000), not on the clock of the computer OpenAmpere runs on. | documented | H2 protocol; [#378](https://github.com/Gr33ndev/OpenAmpere/issues/378) |
| A slot with a day mask repeats every week until it is switched off or deleted. | derived | [#378](https://github.com/Gr33ndev/OpenAmpere/issues/378) |
| evcc's saj-h2 template controls the battery only through stored settings: normal = backup mode with 0x3651 = lowest SoC, hold = backup mode with 0x3651 = highest SoC, charge = AppMode 1 with 0x3604 = 1 and slot 1 over the whole day at 100 %. No watchdog (unlike its FoxESS H3 template). | derived | evcc-io/evcc `templates/definition/meter/saj-h2.yaml`; research in [#377](https://github.com/Gr33ndev/OpenAmpere/issues/377) |
| stanus74's "Charging Control" switch sets bit 0 of 0x3604 (slot 1) and AppMode 1, and on switching off writes 0x3604 = 0 and the previous AppMode; the owner sets the slot times. The switch counts as on only with bit and AppMode together. | derived | stanus74/home-assistant-saj-h2-modbus `charge_control.py`, `switch.py` at e83bfb5; research in [#377](https://github.com/Gr33ndev/OpenAmpere/issues/377) |
| The previous IQ box writes 0x3647 and 0x3644, and the block 0x8300–0x8306 every 1–3 s while charging. Whether 0x8300–0x8306 ends by itself (the only candidate for a remote control with a timeout) has not been tested. | derived | stanus74/home-assistant-saj-h2-modbus#18; [#377](https://github.com/Gr33ndev/OpenAmpere/issues/377) |
| Another device (the IQ box, the SAJ app) can set the work mode away from time-of-use or clear bit 6 of 0x3604 while OpenAmpere charges through slot 7; the slot then no longer charges. | derived | [#382](https://github.com/Gr33ndev/OpenAmpere/issues/382), [#383](https://github.com/Gr33ndev/OpenAmpere/issues/383); stanus74/home-assistant-saj-h2-modbus#18 |
| Writing 0x3644–0x3647 with FC06, one register at a time, is how the H2 protocol, stanus74 and evcc do it. Reading these registers is confirmed on a real device; OpenAmpere's writes were not yet confirmed on a real device when control was added. | documented | H2 protocol; stanus74; evcc; [#378](https://github.com/Gr33ndev/OpenAmpere/issues/378) |
| Reading is confirmed on a real 0x005E ("3P-X E2"); control is not yet confirmed on any SAJ, and nothing is confirmed on 0x005A–0x005D. README and website once showed a checkmark for all of SAJ, corrected to "not yet confirmed" and later to this state. | confirmed (reading on 0x005E), open (rest) | [#111](https://github.com/Gr33ndev/OpenAmpere/issues/111), [#173](https://github.com/Gr33ndev/OpenAmpere/issues/173), [#185](https://github.com/Gr33ndev/OpenAmpere/issues/185), [#469](https://github.com/Gr33ndev/OpenAmpere/issues/469); [devices.md](../../devices.md) |
| The previous IQ box keeps the inverter's Modbus TCP connection busy all the time, so OpenAmpere cannot connect while it is in the network. After the box was cut from the network, OpenAmpere detected the inverter and read live values a few minutes later. | confirmed (on 0x005E) | owner report by e-mail on a 0x005E, summarised in [#469](https://github.com/Gr33ndev/OpenAmpere/issues/469); [#311](https://github.com/Gr33ndev/OpenAmpere/issues/311) |

## Decisions in OpenAmpere

| Decision | Why | Issue/PR |
|---|---|---|
| Detection after FoxESS (unit 247): SAJ at unit 1, then 2. Block 0x8F00 (29 registers), then a plausibility check: 0x4004 ≤ 9 and 0x406F ≤ 10000. | The address depends on the installation; the check rejects devices that answer 0x8F00 but are no SAJ hybrid. | initial driver; [devices.md](../../devices.md) |
| Known codes get their name; other codes in 0x0050–0x006F are accepted with a warning in the log, read like an H2 and shown as "Typ 0x…"; codes outside the range are rejected. | New family members can be read without a release; a foreign device is not mistaken for an SAJ. | initial driver; [#379](https://github.com/Gr33ndev/OpenAmpere/issues/379) |
| Register map `saj_h2`, driver label "SAJ H2 / HS2"; the rated power comes from 0x8F01, not from the model name. | One type code covers several power classes. | [#379](https://github.com/Gr33ndev/OpenAmpere/issues/379) |
| 0x8F01 counts as rated power only within the power class in the name of the type (0x0055–0x005D, e.g. "5-10K": 5 to 10 kW). Otherwise (outside the class, 0x005E, an unknown type) the export limit is written only after the owner confirmed the rated power from the type plate in the app, also a lower one; the confirmed value is then the base of the per-mille conversion and of the charging power, as long as the device reports the value it was confirmed for. | The meaning of 0x8F01 has no source, and a wrong base makes the export limit wrong by the same factor while the read-back still matches. | [#492](https://github.com/Gr33ndev/OpenAmpere/issues/492) |
| The live blocks are read with every poll: 0x4004 (14), 0x4022 (11), 0x4069 (20), 0x40A0 (14), 0x40BF (64), 0x4167 (16), 0xA000 (18). A block the device rejects is skipped. | Optional blocks (summed counters) must not break the reading. | initial driver |
| Meter B (0x40A3) is read with every poll (it is in the block 0x40A0) but counted as solar power only when the owner switches on the second inverter in "Meine Anlage"; it is then shown as its own source. The app points to the switch when the meter reads power. | Not every system uses Meter B for an extra PV inverter, and the owner can name it. | [#419](https://github.com/Gr33ndev/OpenAmpere/issues/419) |
| No energy counter for Meter B is known: OpenAmpere integrates its power (not across gaps over 2 minutes) into a counter of its own. The quarter hours add it to PV (and so to the consumption) and keep it in `pv_external`; Home Assistant gets it as its own lifetime counter. | Without it, generation, consumption, self-sufficiency and savings would be too low. | [#419](https://github.com/Gr33ndev/OpenAmpere/issues/419) |
| Grid counters: the summed counters (all phases) if the device has them, otherwise L1. The source is fixed after the first reading. | Switching between them would look like a huge energy jump. | initial driver; [devices.md](../../devices.md) |
| Energy counters are treated with a 10 Wh step (0.01 kWh). | Resolution of the SAJ counters; the app shows the matching decimals. | [#194](https://github.com/Gr33ndev/OpenAmpere/issues/194), [#195](https://github.com/Gr33ndev/OpenAmpere/issues/195) |
| 0x3644–0x3647 are read only for the battery settings, not with every poll; a rejected read is remembered. Without them the on-grid limit comes from 0x402A, the charge limit from 0x4029, the work mode from 0x4022; the off-grid limit stays unknown. | Firmware that rejects them cannot break the live values; no guessed limit. | [#335](https://github.com/Gr33ndev/OpenAmpere/issues/335), [#337](https://github.com/Gr33ndev/OpenAmpere/issues/337) |
| 0x402C is not used. | Its meaning is not certain (see Behaviour). | [#335](https://github.com/Gr33ndev/OpenAmpere/issues/335) |
| Diagnostics check "Speicher-Grenzen (SAJ)" shows 0x3644–0x3647, 0x3651 and 0x4029–0x402C side by side; owners compare them with the manufacturer's app. | To confirm which register holds which limit. | [#334](https://github.com/Gr33ndev/OpenAmpere/issues/334), [#337](https://github.com/Gr33ndev/OpenAmpere/issues/337) |
| Control only for 0x005A–0x005E (battery settings, export limit, grid charging), with the same checks as FoxESS (master switch, test mode, limits, read-back, control log). AS2 and other codes stay read-only. Before 0.29.0 the SAJ driver was read-only; control was planned only after verification on real devices. | H2 and HS2 are in the protocol and controlled the same way by stanus74 and evcc; 0x005E's settings matched the manufacturer's app. No evidence for the AS2. | [#113](https://github.com/Gr33ndev/OpenAmpere/issues/113), [#211](https://github.com/Gr33ndev/OpenAmpere/issues/211), [#377](https://github.com/Gr33ndev/OpenAmpere/issues/377), [#378](https://github.com/Gr33ndev/OpenAmpere/issues/378) |
| Work modes offered: self-use (0) and backup (2) only; other modes are refused. | Time-of-use is OpenAmpere's own tool for grid charging, passive belongs to external controllers, peak shaving is not needed. | [#377](https://github.com/Gr33ndev/OpenAmpere/issues/377), [#378](https://github.com/Gr33ndev/OpenAmpere/issues/378) |
| A work mode changed while OpenAmpere charges (AppMode temporarily 1) is stored and applied when charging ends; the settings show the owner's mode, not time-of-use. | Writing it right away would stop the charging. | [#378](https://github.com/Gr33ndev/OpenAmpere/issues/378) |
| Limits: charge limit 10–100 %; reserve and off-grid limit by the common rules (off-grid limit from 0 %, reserve at least 10 %, lower only with the owner's confirmation, [#313](https://github.com/Gr33ndev/OpenAmpere/issues/313)). | Same rules as FoxESS. | [#69](https://github.com/Gr33ndev/OpenAmpere/issues/69), [#313](https://github.com/Gr33ndev/OpenAmpere/issues/313), [#378](https://github.com/Gr33ndev/OpenAmpere/issues/378) |
| 0x3651 is shown and set as "Reserve im Modus Notstromreserve" while the work mode is backup: 10–100 %, not above the charge limit, with test mode, read-back, control log and overwrite check. Home Assistant gets a `backup_soc` number only when the device reports it. | In backup mode it is the reserve that applies; FoxESS has no such setting. | [#380](https://github.com/Gr33ndev/OpenAmpere/issues/380), [#381](https://github.com/Gr33ndev/OpenAmpere/issues/381) |
| Export limit: written rounded down to the per-mille step (step = rated power / 1000, rounded up, shown in the app); a read-back up to one step below the request counts as the same limit. The limit is written before the mode; if 0x365C is not 1 or 3 it is set to 1. With 0x365C = 0 the limit counts as the rated power; 0x365C = 2 is shown as not supported and nothing is written. 0x365C is read and shown. Legal checks and confirmations are the FoxESS ones. | Never more feed-in than set; a limit that does not apply must not look like one that does. | [#377](https://github.com/Gr33ndev/OpenAmpere/issues/377), [#378](https://github.com/Gr33ndev/OpenAmpere/issues/378) |
| Grid charging through a short charge slot of the time-of-use mode, not passive mode. The issue planned slot 1; the implementation uses **slot 7** (0x3618–0x361A, bit 6 of 0x3604). | Passive mode would keep charging after OpenAmpere stops; a slot ends by itself. Slot 7 leaves the owner's slots (usually the first) alone. This is the documented exception to "continuous control only via a remote control with a watchdog" (AGENTS.md). | [#377](https://github.com/Gr33ndev/OpenAmpere/issues/377), [#378](https://github.com/Gr33ndev/OpenAmpere/issues/378) |
| The slot covers today from now for 10 minutes (never past 23:59; the next day gets a new slot) and is extended when fewer than 5 minutes are left; only the end moves, so a stored register is written about every 5 minutes. Power = rated power × % rounded up, 1–100 %. | If OpenAmpere stops, charging ends at most 10 minutes later; few writes to stored registers. | [#377](https://github.com/Gr33ndev/OpenAmpere/issues/377), [#378](https://github.com/Gr33ndev/OpenAmpere/issues/378) |
| Start order: slot registers, 0x365F = 1, bit 6 of 0x3604, AppMode 1 last. With every command, 0x365F, the bit and AppMode are set again where they differ, and a slot changed while switched off is written again. | The slot holds OpenAmpere's values before it is switched on; like the FoxESS remote control, OpenAmpere takes over again after another device changed something. | [#378](https://github.com/Gr33ndev/OpenAmpere/issues/378), [#382](https://github.com/Gr33ndev/OpenAmpere/issues/382), [#383](https://github.com/Gr33ndev/OpenAmpere/issues/383) |
| The owner's AppMode, 0x365F and slot-7 words are kept in the `remote_command` meta from the start of a session (also when someone changes them in between) and put back when charging ends, also after a restart. A work mode the owner chooses while OpenAmpere charges is not written but kept for the end of charging, and stored in the meta right away ([#493](https://github.com/Gr33ndev/OpenAmpere/issues/493)). After a restart, before the first charging command, the kept values are taken over from the meta while slot 7 still holds OpenAmpere's words, is on and AppMode is 1; otherwise the new mode is written right away ([#497](https://github.com/Gr33ndev/OpenAmpere/issues/497)). The takeover also happens when the app reads the settings, so it shows the owner's mode instead of none ([#500](https://github.com/Gr33ndev/OpenAmpere/issues/500)). | Before [#383](https://github.com/Gr33ndev/OpenAmpere/issues/383) a slot switched off by another device made the next command look like a new start, AppMode 1 was stored as the owner's mode and the inverter stayed in time-of-use after charging. | [#378](https://github.com/Gr33ndev/OpenAmpere/issues/378), [#382](https://github.com/Gr33ndev/OpenAmpere/issues/382), [#383](https://github.com/Gr33ndev/OpenAmpere/issues/383) |
| Slot 7 is never taken over while it is switched on with other values (the app says to free it in the SAJ app). Release only while slot 7 still holds OpenAmpere's values: clear bit 6, put back AppMode (if 1), 0x365F and the owner's slot words, then check that bit 6 is off. | Release only what OpenAmpere started. | [#378](https://github.com/Gr33ndev/OpenAmpere/issues/378), [#383](https://github.com/Gr33ndev/OpenAmpere/issues/383) |
| OpenAmpere's new slot words are kept before they are written. While a slot write may have been cut off (written, but the answer timed out), each slot word may be the one before or OpenAmpere's new one and the slot still counts as OpenAmpere's; the `remote_command` meta keeps this also after a failed command, so a release after a restart puts the owner's slot back as well. | A write that reached the inverter without an answer would otherwise leave a weekly grid charge switched on. | [#491](https://github.com/Gr33ndev/OpenAmpere/issues/491) |
| The inverter clock (0x4000) is read before each charging command; an implausible clock (no valid date) stops the command. The issue planned to refuse a clock that is far off; the code does not compare it with real time. | The slot follows the inverter's clock. | [#377](https://github.com/Gr33ndev/OpenAmpere/issues/377), [#378](https://github.com/Gr33ndev/OpenAmpere/issues/378) |
| AppMode 3 (passive) is reported as "another device controls the inverter". | Only external controllers use passive mode. | [#378](https://github.com/Gr33ndev/OpenAmpere/issues/378) |
| The charging page says that charging can go on up to 10 minutes after OpenAmpere stops, and that the slot repeats weekly until OpenAmpere runs again or it is deleted in the manufacturer's app. | The owner must know what stays on the inverter. | [#378](https://github.com/Gr33ndev/OpenAmpere/issues/378) |
| 0x8300–0x8306 is not used. | Undocumented and untested. | [#377](https://github.com/Gr33ndev/OpenAmpere/issues/377) |
| Diagnostics check "Steuerung (SAJ)": slot 7 on/off, its length and power (no times or days), 0x365F, 0x365A/0x365C and whether the clock is readable (not the time). | Times and days would tell when the owner charges. | [#378](https://github.com/Gr33ndev/OpenAmpere/issues/378) |

## Open questions

- Sign of the battery power 0x40A6 (+ = discharging per evcc) and of the grid power 0x40AD (+ = import per evcc) on a
  real device ([registers.md](../../registers.md), evcc-io/evcc saj-h2 template).
- Whether the PV energy counter 0x40BF/0x40C5 covers all MPPTs ([registers.md](../../registers.md)).
- Whether 0x8F01 is the rated power in W on every type: diagnostics reports from devices of different power classes
  would settle it ([#492](https://github.com/Gr33ndev/OpenAmpere/issues/492)).
- Size of the export limit step of 0x365A on a real device (per mille of 0x8F01 as in the protocol), and which value
  0x365C has on EKD units ([#377](https://github.com/Gr33ndev/OpenAmpere/issues/377)).
- Whether extending a running charge slot keeps charging without a gap ([#377](https://github.com/Gr33ndev/OpenAmpere/issues/377),
  [#378](https://github.com/Gr33ndev/OpenAmpere/issues/378)).
- Behaviour of 0x3651 in backup mode: does the battery really supply the house down to 0x3651 and ignore 0x3644
  ([#380](https://github.com/Gr33ndev/OpenAmpere/issues/380), [#381](https://github.com/Gr33ndev/OpenAmpere/issues/381))?
- Meaning of 0x402C: a diagnostics report in which the off-grid depth, 0x3651 and 0x3644 all differ would settle it;
  meaning of 0x4029 and 0x402B ([#334](https://github.com/Gr33ndev/OpenAmpere/issues/334)).
- OpenAmpere's writes to 0x3644–0x3647, 0x3651, 0x365A/0x365C and the charge slot are not yet confirmed on a real
  device ([#378](https://github.com/Gr33ndev/OpenAmpere/issues/378)).
- Which lowest off-grid limit an SAJ accepts; if a device rejects 0 %, the limit must come from the driver
  ([#69](https://github.com/Gr33ndev/OpenAmpere/issues/69)).
- Settled: the old [registers.md](../../registers.md) said 0x3644/0x3645 are not in a public SAJ protocol and that
  there is none for the H2/HS2; both were written before the H2 protocol was found during
  [#377](https://github.com/Gr33ndev/OpenAmpere/issues/377). The protocol lists 0x3644 (on-grid discharge depth),
  0x3645 (off-grid discharge depth), 0x3646 (charge limit) and 0x3647 (AppMode). The measurement registers have not
  been compared with it one by one.
- The clock check refuses only an invalid date; a clock that is far off from real time is not detected, although
  [#377](https://github.com/Gr33ndev/OpenAmpere/issues/377) planned it.
- Whether the AS2 (0x0055, 0x0056) answers the same map, and whether it could get control.
- Whether 0x8300–0x8306 is a remote control that ends by itself (untested, stanus74/home-assistant-saj-h2-modbus#18).

## Sources

- **H2 protocol:** SAJ, "H2-3~6K-S2 single phase communication protocol" (2022-12-02), shipped with
  stanus74/home-assistant-saj-h2-modbus. Named, not copied.
- **H1 protocol** and the protocols for the R5/R6/C6 and AS1: SAJ's Modbus protocols for related inverters, used to
  check names and meanings.
- **Community** (credited in [NOTICE](../../../NOTICE)):
  - stanus74/home-assistant-saj-h2-modbus (MIT), including `charge_control.py` and `switch.py` at e83bfb5, issues
    stanus74/home-assistant-saj-h2-modbusstanus74/home-assistant-saj-h2-modbus#18 and stanus74/home-assistant-saj-h2-modbus#157 there, discussion stanus74/home-assistant-saj-h2-modbus#199
  - dboeni/home-assistant-ampere-storage-pro-modbus (MIT)
  - litronics/EKD_Telegraf_Modbus (Apache-2.0)
  - evcc-io/evcc, template `templates/definition/meter/saj-h2.yaml` (MIT)
- **OpenAmpere issues and pull requests:** [#41](https://github.com/Gr33ndev/OpenAmpere/issues/41),
  [#69](https://github.com/Gr33ndev/OpenAmpere/issues/69), [#111](https://github.com/Gr33ndev/OpenAmpere/issues/111),
  [#113](https://github.com/Gr33ndev/OpenAmpere/issues/113), [#173](https://github.com/Gr33ndev/OpenAmpere/issues/173),
  [#185](https://github.com/Gr33ndev/OpenAmpere/issues/185), [#194](https://github.com/Gr33ndev/OpenAmpere/issues/194),
  [#195](https://github.com/Gr33ndev/OpenAmpere/issues/195), [#211](https://github.com/Gr33ndev/OpenAmpere/issues/211), [#313](https://github.com/Gr33ndev/OpenAmpere/issues/313),
  [#334](https://github.com/Gr33ndev/OpenAmpere/issues/334) (diagnostics report of a real device),
  [#335](https://github.com/Gr33ndev/OpenAmpere/issues/335), [#337](https://github.com/Gr33ndev/OpenAmpere/issues/337),
  [#339](https://github.com/Gr33ndev/OpenAmpere/issues/339), [#347](https://github.com/Gr33ndev/OpenAmpere/issues/347),
  [#348](https://github.com/Gr33ndev/OpenAmpere/issues/348), [#372](https://github.com/Gr33ndev/OpenAmpere/issues/372),
  [#376](https://github.com/Gr33ndev/OpenAmpere/issues/376), [#377](https://github.com/Gr33ndev/OpenAmpere/issues/377)
  (control research), [#378](https://github.com/Gr33ndev/OpenAmpere/issues/378), [#379](https://github.com/Gr33ndev/OpenAmpere/issues/379),
  [#380](https://github.com/Gr33ndev/OpenAmpere/issues/380), [#381](https://github.com/Gr33ndev/OpenAmpere/issues/381),
  [#382](https://github.com/Gr33ndev/OpenAmpere/issues/382), [#383](https://github.com/Gr33ndev/OpenAmpere/issues/383)
- **Code:** `src/openampere/drivers/saj/registers.py`, `driver.py`; `src/openampere/diagnostics.py` (SAJ checks);
  `src/openampere/control.py` (limit checks).
