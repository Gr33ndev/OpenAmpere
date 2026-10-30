# FoxESS H3, register map of newer firmware

> Not official manufacturer information: compiled by the OpenAmpere contributors, possibly incomplete or wrong.
> Changing inverter settings can affect warranty and grid connection rules. No affiliation with FoxESS or EKD, not
> legal advice. See [Device knowledge](../README.md).

**Applies to:** FoxESS H3 with newer firmware, H3 Smart, H3 Pro (probably the E3) · **Sold as:** see
[h3.md](h3.md) · **OpenAmpere:** driver `foxess`, register map `foxess_h3_new` · **Support:** display and control ·
**Confirmed on a real device:** partly (reading and control on an H3 with newer firmware; H3 Smart and H3 Pro not)

Family facts (detection, connection limits, remote control behaviour, decisions on grid charging) are on
[h3.md](h3.md); the older map is on [h3-legacy-map.md](h3-legacy-map.md).

## Summary

- Chosen when the energy counters at 39601 answer. Measurement registers are input registers (FC04) by default;
  OpenAmpere falls back to FC03 because the sources disagree.
- Has total registers for grid, house and PV power, up to four PV strings and three MPPT trackers, a second battery
  module (BMS2) and cell temperatures.
- Energy counters in 0.01 kWh.
- Settings and control are holding registers: work mode 49203, SoC limits 46609–46611, remote control 46001–46004,
  export limit 46616–46617.
- Reading confirmed on a real H3 ([#11](https://github.com/Gr33ndev/OpenAmpere/issues/11), [#16](https://github.com/Gr33ndev/OpenAmpere/issues/16)), control confirmed ([#196](https://github.com/Gr33ndev/OpenAmpere/issues/196)); the exact variant (H3, H3 Smart, H3 Pro or E3)
  is not stated in these reports.

## Registers

Read in blocks (start + count): 37002+1, 37609+24, 38309+8, 38814+8, 39063+15, 39118+2, 39141+1, 39219+20, 39279+8,
39327+12, 39601+32. Addresses inside a block that are not listed below are read but not used. Model string
30000–30015: see [h3.md](h3.md). FC04 = input register, FC03 = holding register.

| Register | Meaning | Type, scale, sign | R/W | Status | Source |
|---|---|---|---|---|---|
| 30016–30031 | Serial number (on this map only) | ASCII, 16 registers | R (detected FC, then FC03, FC04) | derived | nathanmarlor/foxess_modbus; [registers.py](../../../src/openampere/drivers/foxess/registers.py) |
| 36001–36003 | Firmware: master, slave, manager | U16; shown as master/100, slave/100, manager in hex | R (detected FC, then FC03, FC04) | derived | nathanmarlor/foxess_modbus; [driver.py](../../../src/openampere/drivers/foxess/driver.py) |
| 37002 | BMS1 connected, 1 = yes; at 0 OpenAmpere drops SoC and battery temperatures | U16, 0/1 | R FC04 (optional block) | confirmed (reads 1 with a connected battery) | nathanmarlor/foxess_modbus; [#19](https://github.com/Gr33ndev/OpenAmpere/issues/19), [#44](https://github.com/Gr33ndev/OpenAmpere/issues/44) |
| 37609 | Battery voltage | U16, ×0.1 V | R FC04 | derived | nathanmarlor/foxess_modbus |
| 37610 | Battery current | I16, ×0.1 A | R FC04 | derived | nathanmarlor/foxess_modbus |
| 37611 | "Battery temperature": temperature of the BMS electronics, not of the cells | I16, ×0.1 °C | R FC04 | confirmed (meaning) | nathanmarlor/foxess_modbus; [#16](https://github.com/Gr33ndev/OpenAmpere/issues/16), [#41](https://github.com/Gr33ndev/OpenAmpere/issues/41) |
| 37612 | State of charge | U16, % | R FC04 | derived | nathanmarlor/foxess_modbus |
| 37617 | Highest cell temperature | I16, ×0.1 °C | R FC04 | derived | nathanmarlor/foxess_modbus |
| 37618 | Lowest cell temperature | I16, ×0.1 °C | R FC04 | derived | nathanmarlor/foxess_modbus |
| 37619 / 37620 | Highest / lowest cell voltage | mV | read in the block, not used | derived | nathanmarlor/foxess_modbus; [docs/registers.md](../../registers.md) |
| 37624 | State of health (SoH) | U16, % | R FC04 | derived | nathanmarlor/foxess_modbus |
| 37632 | Remaining capacity | not stated in the sources | read in the block, not used | derived | nathanmarlor/foxess_modbus; [docs/registers.md](../../registers.md) |
| 37700 | BMS2 connected | flag | not read | derived | nathanmarlor/foxess_modbus; [docs/registers.md](../../registers.md) |
| 38309 | Second battery module (BMS2) temperature | I16, ×0.1 °C | R FC04 (optional block) | derived | nathanmarlor/foxess_modbus |
| 38315 | BMS2 highest cell temperature | I16, ×0.1 °C | R FC04 (optional block) | derived | nathanmarlor/foxess_modbus |
| 38316 | BMS2 lowest cell temperature | I16, ×0.1 °C | R FC04 (optional block) | derived | nathanmarlor/foxess_modbus |
| 38814–38815 | Grid power, total | I32, ×0.1 W, + = export (OpenAmpere negates it: + = import) | R FC04 | derived | nathanmarlor/foxess_modbus |
| 38816–38821 | Grid power L1, L2, L3 | I32 each, ×0.1 W | read in the block, not used | derived | nathanmarlor/foxess_modbus; [docs/registers.md](../../registers.md) |
| 38901 | Meter 2 (external PV) active, = 1 | flag | not read | derived | nathanmarlor/foxess_modbus; [docs/registers.md](../../registers.md) |
| 38914 ff. | Meter 2 (external PV) power | I32, ×0.1 W | not read | derived | nathanmarlor/foxess_modbus; [docs/registers.md](../../registers.md) |
| 39063 | Inverter state (status code) | U16 | R FC04 (optional block) | derived | nathanmarlor/foxess_modbus |
| 39065–39066 | Flags; bit 0 = off-grid (grid disconnected, backup operation) | U32 | R FC04 | derived; bit seen set on a real device, once spuriously ([#48](https://github.com/Gr33ndev/OpenAmpere/issues/48), [#50](https://github.com/Gr33ndev/OpenAmpere/issues/50), [#89](https://github.com/Gr33ndev/OpenAmpere/issues/89)) | nathanmarlor/foxess_modbus; [#89](https://github.com/Gr33ndev/OpenAmpere/issues/89), [#90](https://github.com/Gr33ndev/OpenAmpere/issues/90) |
| 39067 / 39068 / 39069 | Alarm words 1, 2, 3 | U16 each | R FC04 | derived | nathanmarlor/foxess_modbus |
| 39070 / 39071 | PV1 voltage / current | I16, ×0.1 V / I16, ×0.01 A | R FC04 | derived | nathanmarlor/foxess_modbus |
| 39072 / 39073 | PV2 voltage / current | I16, ×0.1 V / I16, ×0.01 A | R FC04 | derived | nathanmarlor/foxess_modbus |
| 39074 / 39075 | PV3 voltage / current | I16, ×0.1 V / I16, ×0.01 A | R FC04 | derived | nathanmarlor/foxess_modbus |
| 39076 / 39077 | PV4 voltage / current | I16, ×0.1 V / I16, ×0.01 A | R FC04 | derived | nathanmarlor/foxess_modbus |
| 39118–39119 | PV power, total (documented as "PV total (E3)") | I32, W | R FC04 (optional block) | derived | nathanmarlor/foxess_modbus; [docs/registers.md](../../registers.md) |
| 39141 | Inverter temperature | I16, ×0.1 °C | R FC04 (optional block) | confirmed (scale checked against another reader on an H3) | nathanmarlor/foxess_modbus; [#11](https://github.com/Gr33ndev/OpenAmpere/issues/11), [#26](https://github.com/Gr33ndev/OpenAmpere/issues/26) |
| 39219–39224 | House consumption L1, L2, L3 | I32 each, W | read in the block, not used | derived | nathanmarlor/foxess_modbus; [docs/registers.md](../../registers.md) |
| 39225–39226 | House consumption, total | I32, W | R FC04 | derived | nathanmarlor/foxess_modbus |
| 39237–39238 | Battery power | I32, W, + = discharging | R FC04 | derived | nathanmarlor/foxess_modbus |
| 39279 / 39281 / 39283 / 39285 | PV1, PV2, PV3, PV4 power (2 registers each) | I32, W | R FC04 (optional block) | derived | nathanmarlor/foxess_modbus |
| 39327 / 39328 / 39329–39330 | MPPT1 voltage / current / power | I16, ×0.1 V / I16, ×0.01 A / I32, W | R FC04 (optional block) | derived | nathanmarlor/foxess_modbus |
| 39331 / 39332 / 39333–39334 | MPPT2 voltage / current / power | I16, ×0.1 V / I16, ×0.01 A / I32, W | R FC04 (optional block) | derived | nathanmarlor/foxess_modbus |
| 39335 / 39336 / 39337–39338 | MPPT3 voltage / current / power | I16, ×0.1 V / I16, ×0.01 A / I32, W | R FC04 (optional block) | derived | nathanmarlor/foxess_modbus |
| 39601 / 39603 | PV energy: total / today | U32, 0.01 kWh | R FC04 | derived | nathanmarlor/foxess_modbus |
| 39605 / 39607 | Battery charge: total / today | U32, 0.01 kWh | R FC04 | derived | nathanmarlor/foxess_modbus |
| 39609 / 39611 | Battery discharge: total / today | U32, 0.01 kWh | R FC04 | derived | nathanmarlor/foxess_modbus |
| 39613 / 39615 | Grid export: total / today | U32, 0.01 kWh | R FC04 | derived | nathanmarlor/foxess_modbus |
| 39617 / 39619 | Grid import: total / today | U32, 0.01 kWh | R FC04 | derived | nathanmarlor/foxess_modbus |
| 39621 / 39623 | Yield: total / today | U32, 0.01 kWh | read in the block, not used | derived | nathanmarlor/foxess_modbus; [docs/registers.md](../../registers.md) |
| 39629 / 39631 | Consumption (inverter's load counter): total / today | U32, 0.01 kWh | R FC04 | derived | nathanmarlor/foxess_modbus |
| 46001 | Remote control, a bit field: bit 0 on, bit 1 sign convention (0: + = discharge/export), bits 3:2 target (00 AC, 01 battery, 10 grid (CT/meter), 11 AC with grid first). OpenAmpere writes 5 (on with target battery) where 46007 is readable, otherwise 1 (on with target AC), and 0 (off); the previous smartbox writes 5. What the targets mean for grid charging: [h3.md](h3.md#remote-control-grid-charging) ([#509](https://github.com/Gr33ndev/OpenAmpere/issues/509)) | U16 bit field | R FC03, W FC06 | confirmed (on/off); documented (bit field, changed in definition versions of 2024) | nathanmarlor/foxess_modbus; FoxESS Modbus interface definition V1.05.04.00, table 3-8; [#141](https://github.com/Gr33ndev/OpenAmpere/issues/141), [#196](https://github.com/Gr33ndev/OpenAmpere/issues/196) |
| 46002 | Remote control watchdog timeout | U16, s (OpenAmpere 180, allowed 10–3600) | R FC03, W FC06 | confirmed | nathanmarlor/foxess_modbus; [#141](https://github.com/Gr33ndev/OpenAmpere/issues/141), [#196](https://github.com/Gr33ndev/OpenAmpere/issues/196) |
| 46003–46004 | Remote control active power | I32, W, + = discharge/export, − = battery charges (sign not verified on every device) | W FC16, R FC03 (diagnostics) | confirmed (grid charging works) | nathanmarlor/foxess_modbus; [#196](https://github.com/Gr33ndev/OpenAmpere/issues/196) |
| 46007 | Remote control timeout countdown | U16, s | R FC03 (read-only per FoxESS); OpenAmpere reads it on its own, a refusal tolerated: readable means the newer layout of 46001 ([#509](https://github.com/Gr33ndev/OpenAmpere/issues/509)) | documented | FoxESS Modbus interface definition V1.05.04.00, table 3-8 |
| 46501–46502 | Import limit (per foxess_modbus only on the H3 Smart) | I32, W | not used | derived | nathanmarlor/foxess_modbus; [docs/registers.md](../../registers.md) |
| 46607 | Maximum charge current | U16, ×0.1 A | holding, writable; neither read nor written by OpenAmpere | derived | nathanmarlor/foxess_modbus |
| 46608 | Maximum discharge current | U16, ×0.1 A | holding, writable; neither read nor written by OpenAmpere | derived | nathanmarlor/foxess_modbus |
| 46609 | Outage floor (`min_soc`): lowest SoC in backup/off-grid operation | U16, % | R FC03, W FC06 | confirmed (0 % written via Modbus: open) | nathanmarlor/foxess_modbus; [#69](https://github.com/Gr33ndev/OpenAmpere/issues/69), [#72](https://github.com/Gr33ndev/OpenAmpere/issues/72), [#196](https://github.com/Gr33ndev/OpenAmpere/issues/196) |
| 46610 | Charge limit (`max_soc`); not respected during forced charging | U16, % | R FC03, W FC06 | confirmed | nathanmarlor/foxess_modbus; [#196](https://github.com/Gr33ndev/OpenAmpere/issues/196), [#242](https://github.com/Gr33ndev/OpenAmpere/issues/242) |
| 46611 | Backup reserve (`min_soc_on_grid`) | U16, % | R FC03, W FC06 | confirmed (also below 10 %) | nathanmarlor/foxess_modbus; [#196](https://github.com/Gr33ndev/OpenAmpere/issues/196), [#313](https://github.com/Gr33ndev/OpenAmpere/issues/313) |
| 46616–46617 | Export (feed-in) power limit | I32, W (OpenAmpere 0–99999 W and at most the rated power) | R FC03, W FC16 | confirmed | nathanmarlor/foxess_modbus (H3 Smart); [#196](https://github.com/Gr33ndev/OpenAmpere/issues/196) |
| 49203 | Work mode: 1 self-use, 2 feed-in first, 3 backup, 4 peak shaving | U16 | R FC03, W FC06 | confirmed | nathanmarlor/foxess_modbus; [#196](https://github.com/Gr33ndev/OpenAmpere/issues/196) |
| 20162 | Written as 1 by the previous smartbox; meaning unknown | – | neither read nor written by OpenAmpere | open | report in [docs/registers.md](../../registers.md) |

## Behaviour

| Fact | Status | Source |
|---|---|---|
| Reading with this map works on a real H3 ([#11](https://github.com/Gr33ndev/OpenAmpere/issues/11), [#16](https://github.com/Gr33ndev/OpenAmpere/issues/16)). Not every register above is checked one by one; the reports name the exact variant only as "H3 / H3 Smart / H3 Pro / E3". | confirmed | [#11](https://github.com/Gr33ndev/OpenAmpere/issues/11), [#16](https://github.com/Gr33ndev/OpenAmpere/issues/16), [#111](https://github.com/Gr33ndev/OpenAmpere/issues/111), [#173](https://github.com/Gr33ndev/OpenAmpere/issues/173), [#185](https://github.com/Gr33ndev/OpenAmpere/issues/185) |
| Control with this map works on a real system: work mode, backup reserve and limits, remote control for grid charging, export limit (earlier only partly confirmed, [#25](https://github.com/Gr33ndev/OpenAmpere/issues/25)). | confirmed | [#25](https://github.com/Gr33ndev/OpenAmpere/issues/25), [#196](https://github.com/Gr33ndev/OpenAmpere/issues/196), [#197](https://github.com/Gr33ndev/OpenAmpere/issues/197) |
| 39141 is in 0.1 °C like the other temperatures: raw 452 means 45.2 °C, matching a second readout tool. Read without the factor, it exceeded the 120 °C plausibility limit and was dropped. | confirmed | [#11](https://github.com/Gr33ndev/OpenAmpere/issues/11), [#26](https://github.com/Gr33ndev/OpenAmpere/issues/26) |
| 37611 read about 15 °C above the cell temperatures on one owner's system and matched what another dashboard calls "BMS1 internal": it is the BMS electronics. | confirmed | [#16](https://github.com/Gr33ndev/OpenAmpere/issues/16), [#41](https://github.com/Gr33ndev/OpenAmpere/issues/41) |
| The off-grid bit (39065 bit 0) was set in a single reading while the system exported several kW, followed by minutes without readings. | confirmed | [#89](https://github.com/Gr33ndev/OpenAmpere/issues/89), [#90](https://github.com/Gr33ndev/OpenAmpere/issues/90) |
| The second battery module registers (38309, 38315, 38316) read 0 or the block is rejected when there is no second module. | derived | [driver.py](../../../src/openampere/drivers/foxess/driver.py), [diagnostics.py](../../../src/openampere/diagnostics.py) |
| The export limit register 46616 is rejected with "illegal address" on devices without it. | derived | [driver.py](../../../src/openampere/drivers/foxess/driver.py) |
| One device read about 60000 W in 46616, far above its rated power. Probably the value for "no limit set"; not confirmed against the manufacturer's app. | open | diagnostics report, [#413](https://github.com/Gr33ndev/OpenAmpere/issues/413) |
| On one owner's H3, 46001 stayed 1 with 46002 = 180 and 0 W long after the watchdog time; see [h3.md](h3.md#remote-control-grid-charging). | confirmed | [#141](https://github.com/Gr33ndev/OpenAmpere/issues/141), [#142](https://github.com/Gr33ndev/OpenAmpere/issues/142) |
| Reading 46001 right after writing 0 may show a delayed value; the delay is not quantified on a device (OpenAmpere polls for a few seconds). Under remote control a new backup reserve is taken over a few seconds late ([#322](https://github.com/Gr33ndev/OpenAmpere/issues/322)). | open | [#142](https://github.com/Gr33ndev/OpenAmpere/issues/142), [#322](https://github.com/Gr33ndev/OpenAmpere/issues/322) |
| Energy counters count in 0.01 kWh (10 Wh) units. Suspected: some newer firmware steps only by 0.1 kWh. | open | [#194](https://github.com/Gr33ndev/OpenAmpere/issues/194), [#195](https://github.com/Gr33ndev/OpenAmpere/issues/195) |

## Decisions in OpenAmpere

| Decision | Why | Issue/PR |
|---|---|---|
| Blocks 37002, 38309, 39063, 39118, 39141, 39279 and 39327 are optional: a rejected one is skipped. | Models without a second battery, MPPT details or other parts. | [registers.py](../../../src/openampere/drivers/foxess/registers.py) |
| PV inputs: the MPPT trackers (up to 3) are preferred because they correspond to module arrays; otherwise PV1–PV4. PV total from 39118, otherwise the sum of the inputs. | MPPTs match the owner's arrays. | [driver.py](../../../src/openampere/drivers/foxess/driver.py) |
| SoC and battery temperatures are dropped when 37002 is 0; BMS2 temperatures are dropped when all are 0. | No battery or no second module connected. | [driver.py](../../../src/openampere/drivers/foxess/driver.py) |
| Grid power is negated to + = import. | OpenAmpere's common sign convention. | [driver.py](../../../src/openampere/drivers/foxess/driver.py) |
| If 46616 is rejected, the export limit is reported as not supported. | Not every device has the register. | [driver.py](../../../src/openampere/drivers/foxess/driver.py) |
| OpenAmpere's remote command pattern is 46001 = 5 (or 1, see [h3.md](h3.md#decisions-in-openampere)) and 46002 = 180, with the power in 46003–46004; it recognises its own commands by it, 1 / 180 s left over by older versions included ([#509](https://github.com/Gr33ndev/OpenAmpere/issues/509)). | Release only what OpenAmpere started. | [#141](https://github.com/Gr33ndev/OpenAmpere/issues/141), [#142](https://github.com/Gr33ndev/OpenAmpere/issues/142) |
| Diagnostics (new map only): check the optional blocks 38309 and 39327; compare a 24-register block read at 37609 with single reads of 37609, 37612, 37624; check the scale of 39141 (raw values up to 90 count as ambiguous, the report shows raw and scaled value); show 37002 as yes/no. | Finds block-read and scaling problems from a shared report. | [diagnostics.py](../../../src/openampere/diagnostics.py), [#19](https://github.com/Gr33ndev/OpenAmpere/issues/19), [#26](https://github.com/Gr33ndev/OpenAmpere/issues/26), [#44](https://github.com/Gr33ndev/OpenAmpere/issues/44) |
| Diagnostics read 46616 and report it rounded to 100 W with only the high word, not the raw words. | The exact limit (60 % or 70 % of the kWp) reveals the system size. | [#149](https://github.com/Gr33ndev/OpenAmpere/issues/149), [#150](https://github.com/Gr33ndev/OpenAmpere/issues/150) |
| Diagnostics show a value above the rated power as information ("probably no limit set"), only a negative value as a warning. | A device reported about 60000 W, far above its rated power; more than the rated power cannot be exported anyway. | [#413](https://github.com/Gr33ndev/OpenAmpere/issues/413) |
| Diagnostics read 46001–46004 as one FC03 block and tell three cases apart: off, on by OpenAmpere (charging or a leftover it switches off itself), on by another device (warning with the values). A background job reads the block read-only every 5 minutes to say how long it has been on. The check and the report decode 46001 (target, sign) and read 46007 on its own: a running countdown while OpenAmpere sends nothing means another writer or values restored after a restart, 0 while on means the watchdog left the switch on. 0 W is explained per target: the battery rests (target battery); the house runs from the grid and PV is curtailed once the battery is full (target AC) ([#509](https://github.com/Gr33ndev/OpenAmpere/issues/509)). | Shows who controls the battery, e.g. the previous smartbox. | [#126](https://github.com/Gr33ndev/OpenAmpere/issues/126), [#127](https://github.com/Gr33ndev/OpenAmpere/issues/127), [#135](https://github.com/Gr33ndev/OpenAmpere/issues/135), [#136](https://github.com/Gr33ndev/OpenAmpere/issues/136), [#142](https://github.com/Gr33ndev/OpenAmpere/issues/142) |

## Open questions

- Which register map the E3 uses (model string at 30000).
- FC03 or FC04 for the measurement registers: foxess_modbus reads this map with FC03, other sources with FC04
  (conflict; OpenAmpere tries both).
- Where the remote control addresses come from is not named in [#126](https://github.com/Gr33ndev/OpenAmpere/issues/126) and [#127](https://github.com/Gr33ndev/OpenAmpere/issues/127); [docs/registers.md](../../registers.md)
  credits foxess_modbus for the map.
- Whether the H3 Smart and H3 Pro behave the same; whether all variants have the export limit at 46616 (foxess_modbus
  names only the H3 Smart).
- Meaning of 20162, which the previous smartbox was reported to write.
- Unit of the remaining capacity at 37632.
- Whether some firmware counts in 0.1 kWh steps ([#194](https://github.com/Gr33ndev/OpenAmpere/issues/194)).

## Sources

- [nathanmarlor/foxess_modbus](https://github.com/nathanmarlor/foxess_modbus) (MIT, credited in
  [NOTICE](../../../NOTICE)): `entities/entity_descriptions.py`, `inverter_profiles.py`, `remote_control_manager.py`;
  facts only.
- Further community documentation on FoxESS H3-based storage systems (register addresses as facts only).
- OpenAmpere issues and pull requests linked above.
- Code: [registers.py](../../../src/openampere/drivers/foxess/registers.py),
  [driver.py](../../../src/openampere/drivers/foxess/driver.py),
  [diagnostics.py](../../../src/openampere/diagnostics.py).
