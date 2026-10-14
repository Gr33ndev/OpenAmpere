# FoxESS H3, register map of older firmware

> Not official manufacturer information: compiled by the OpenAmpere contributors, possibly incomplete or wrong.
> Changing inverter settings can affect warranty and grid connection rules. No affiliation with FoxESS or EKD, not
> legal advice. See [Device knowledge](../README.md).

**Applies to:** classic FoxESS H3 with older firmware · **Sold as:** not stated in the sources (see [h3.md](h3.md)) ·
**OpenAmpere:** driver `foxess`, register map `foxess_h3_legacy` · **Support:** display and control (no export
limit) · **Confirmed on a real device:** no

Family facts (detection, connection limits, remote control behaviour, decisions on grid charging) are on
[h3.md](h3.md); the newer map is on [h3-new-map.md](h3-new-map.md).

## Summary

- Chosen when the new map's counters (39601) do not answer but the counters at 32000 do. Measurement registers are
  holding registers (FC03) by default; OpenAmpere falls back to FC04.
- No total registers for grid and house: OpenAmpere adds up the three phases. Only two PV inputs.
- Energy counters in 0.1 kWh; daily values are 16-bit.
- Settings at 41000–41011 (read each register on its own; some addresses in this range are invalid), remote control
  at 44000–44003. No known export limit register.
- Nothing on this map is confirmed on a real device yet, neither reading nor control.

## Registers

Read in blocks (start + count): 31000+52, 31090+1 (optional), 32000+24. Addresses inside a block that are not listed
below are read but not used. Model string 30000–30015: see [h3.md](h3.md). All registers here are holding registers.

| Register | Meaning | Type, scale, sign | R/W | Status | Source |
|---|---|---|---|---|---|
| 30016–30018 | Firmware: master, slave, manager (on the new map these addresses hold the serial number) | U16; shown as master/100, slave/100, manager in hex | R (detected FC, then FC03, FC04) | derived | nathanmarlor/foxess_modbus; [registers.py](../../../src/openampere/drivers/foxess/registers.py) |
| 31000 / 31001 / 31002 | PV1 voltage / current / power | I16, ×0.1 V / I16, ×0.1 A / I16, W | R FC03 | derived | nathanmarlor/foxess_modbus |
| 31003 / 31004 / 31005 | PV2 voltage / current / power | I16, ×0.1 V / I16, ×0.1 A / I16, W | R FC03 | derived | nathanmarlor/foxess_modbus |
| 31026 / 31027 / 31028 | Grid CT L1 / L2 / L3 | I16, W, + = export | R FC03 | derived | nathanmarlor/foxess_modbus |
| 31029 / 31030 / 31031 | Load L1 / L2 / L3 (no total register) | I16, W | R FC03 | derived | nathanmarlor/foxess_modbus |
| 31032 | Inverter temperature | I16, ×0.1 °C | R FC03 | derived | nathanmarlor/foxess_modbus |
| 31033 | Ambient temperature | I16, ×0.1 °C | R FC03 | derived | nathanmarlor/foxess_modbus |
| 31034 | Battery voltage | I16, ×0.1 V | R FC03 | derived | nathanmarlor/foxess_modbus |
| 31035 | Battery current | I16, ×0.1 A | R FC03 | derived | nathanmarlor/foxess_modbus |
| 31036 | Battery power | I16, W, + = discharging | R FC03 | derived | nathanmarlor/foxess_modbus |
| 31037 | Battery temperature | I16, ×0.1 °C | R FC03 | derived | nathanmarlor/foxess_modbus |
| 31038 | State of charge | U16, % | R FC03 | derived | nathanmarlor/foxess_modbus |
| 31041 | Inverter state (status) | U16 | R FC03 | derived | nathanmarlor/foxess_modbus |
| 31044 / 31045 / 31047 | Fault words, read as alarm words 1, 2, 3 | U16 each | R FC03 | derived | nathanmarlor/foxess_modbus |
| 31046, 31048–31051 | Further fault words (the fault words span 31044–31051) | U16 each | read in the block, not used | derived | nathanmarlor/foxess_modbus; [docs/registers.md](../../registers.md) |
| 31090 | State of health (SoH), only firmware 1.80 or newer | U16, % | R FC03 (optional block) | derived | nathanmarlor/foxess_modbus |
| 32000 / 32002 | PV energy: total / today | U32 / U16, 0.1 kWh | R FC03 | derived | nathanmarlor/foxess_modbus |
| 32003 / 32005 | Battery charge: total / today | U32 / U16, 0.1 kWh | R FC03 | derived | nathanmarlor/foxess_modbus |
| 32006 / 32008 | Battery discharge: total / today | U32 / U16, 0.1 kWh | R FC03 | derived | nathanmarlor/foxess_modbus |
| 32009 / 32011 | Grid export: total / today | U32 / U16, 0.1 kWh | R FC03 | derived | nathanmarlor/foxess_modbus |
| 32012 / 32014 | Grid import: total / today | U32 / U16, 0.1 kWh | R FC03 | derived | nathanmarlor/foxess_modbus |
| 32021 / 32023 | Load: total / today | U32 / U16, 0.1 kWh | R FC03 | derived | nathanmarlor/foxess_modbus |
| 41000 | Work mode: 0 self-use, 1 feed-in first, 2 backup, 4 peak shaving | U16 | R FC03, W FC06 | derived | nathanmarlor/foxess_modbus |
| 41001–41006 | Invalid: must not be read | – | never read | derived | nathanmarlor/foxess_modbus; [docs/registers.md](../../registers.md) |
| 41007 | Maximum charge current | U16, ×0.1 A | holding; neither read nor written by OpenAmpere | derived | nathanmarlor/foxess_modbus |
| 41008 | Maximum discharge current | U16, ×0.1 A | holding; neither read nor written by OpenAmpere | derived | nathanmarlor/foxess_modbus |
| 41009 | Outage floor (`min_soc`) | U16, % | R FC03, W FC06 | derived | nathanmarlor/foxess_modbus |
| 41010 | Charge limit (`max_soc`) | U16, % | R FC03, W FC06 | derived | nathanmarlor/foxess_modbus |
| 41011 | Backup reserve (`min_soc_on_grid`) | U16, % | R FC03, W FC06 | derived | nathanmarlor/foxess_modbus |
| 41012–41013, 41015 | Invalid: must not be read | – | never read | derived | nathanmarlor/foxess_modbus; [docs/registers.md](../../registers.md) |
| 44000 | Remote control on/off | U16 | R FC03, W FC06 | derived | nathanmarlor/foxess_modbus; [#127](https://github.com/Gr33ndev/OpenAmpere/issues/127) |
| 44001 | Remote control watchdog timeout | U16, s | R FC03, W FC06 | derived | nathanmarlor/foxess_modbus; [#127](https://github.com/Gr33ndev/OpenAmpere/issues/127) |
| 44002–44003 | Remote control active power | I32, W, + = discharge/export, − = battery charges | W FC16, R FC03 (diagnostics) | derived | nathanmarlor/foxess_modbus; [#127](https://github.com/Gr33ndev/OpenAmpere/issues/127) |

## Behaviour

| Fact | Status | Source |
|---|---|---|
| There is no total register for grid or house power, only the three phases; and only two PV inputs. | derived | nathanmarlor/foxess_modbus; [docs/registers.md](../../registers.md), [docs/devices.md](../../devices.md) |
| Registers in the 41xxx range must be read one at a time; 41001–41006, 41012–41013 and 41015 are invalid. | derived | nathanmarlor/foxess_modbus; [docs/registers.md](../../registers.md) |
| No register for the export limit is known on this map. | derived | [docs/registers.md](../../registers.md), [docs/devices.md](../../devices.md) |
| The remote control block (on/off, timeout, power) sits at 44000–44003, the counterpart of 46001–46004 on the new map. | derived | nathanmarlor/foxess_modbus; [#127](https://github.com/Gr33ndev/OpenAmpere/issues/127) |
| The energy counters count in 0.1 kWh (100 Wh) steps, so counter-based energy has no meaningful second decimal. | derived | nathanmarlor/foxess_modbus; [#194](https://github.com/Gr33ndev/OpenAmpere/issues/194), [#195](https://github.com/Gr33ndev/OpenAmpere/issues/195) |
| Battery health (31090) needs firmware 1.80 or newer. | derived | nathanmarlor/foxess_modbus; [docs/devices.md](../../devices.md) |

## Decisions in OpenAmpere

| Decision | Why | Issue/PR |
|---|---|---|
| Grid power = −(31026 + 31027 + 31028) (+ = import), house power = 31029 + 31030 + 31031, PV power = PV1 + PV2. | No total registers on this map. | [driver.py](../../../src/openampere/drivers/foxess/driver.py) |
| 31090 is an optional block: skipped if rejected. | Older firmware has no SoH. | [registers.py](../../../src/openampere/drivers/foxess/registers.py) |
| Battery settings are read one register at a time with FC03 (work mode, 41009, 41010, 41011). | 41xxx must be read individually. | [driver.py](../../../src/openampere/drivers/foxess/driver.py) |
| The export limit is reported as not supported. | No known register. | [driver.py](../../../src/openampere/drivers/foxess/driver.py) |
| Counter step 100 Wh (`energy_step_wh`); counter-based energy is shown with one decimal. | 0.1 kWh counters. | [#194](https://github.com/Gr33ndev/OpenAmpere/issues/194), [#195](https://github.com/Gr33ndev/OpenAmpere/issues/195) |
| The FoxESS detail checks of the diagnostics (optional blocks, block reads, temperature scale, export limit, remote control) run only for the new map. The background job that watches the remote control (read-only, every 5 minutes) reads 44000–44003 on this map too. | The detail checks are built on new-map addresses. | [diagnostics.py](../../../src/openampere/diagnostics.py) |

## Open questions

- Reading and control on this map are not confirmed on a real device
  ([#111](https://github.com/Gr33ndev/OpenAmpere/issues/111), [#185](https://github.com/Gr33ndev/OpenAmpere/issues/185),
  [#196](https://github.com/Gr33ndev/OpenAmpere/issues/196), [#197](https://github.com/Gr33ndev/OpenAmpere/issues/197)).
  A diagnostics report from an H3 with older firmware would help.
- Whether the remote control at 44000–44003 behaves like the new map's (watchdog, values left on after it,
  [#141](https://github.com/Gr33ndev/OpenAmpere/issues/141)).
- Whether the work mode values (0, 1, 2, 4) and the SoC limits behave as documented.
- Whether any firmware of this map has an export limit register.

## Sources

- [nathanmarlor/foxess_modbus](https://github.com/nathanmarlor/foxess_modbus) (MIT, credited in
  [NOTICE](../../../NOTICE)): `entities/entity_descriptions.py`, `inverter_profiles.py`, `remote_control_manager.py`;
  facts only.
- OpenAmpere issues and pull requests linked above.
- Code: [registers.py](../../../src/openampere/drivers/foxess/registers.py),
  [driver.py](../../../src/openampere/drivers/foxess/driver.py).
