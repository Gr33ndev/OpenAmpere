# FoxESS H3: Modbus registers

Also applies to identical devices sold under other names.

As of: 2026-10-02. Sources (facts only, no code adopted):
- [foxess_modbus](https://github.com/nathanmarlor/foxess_modbus) (MIT): `entities/entity_descriptions.py`, `inverter_profiles.py`, `remote_control_manager.py`
- further community documentation on FoxESS H3-based storage systems (register addresses as facts only)

**Still open, must be verified on a real device:**
1. Which register map the E3 uses (model string in 30000).
2. Whether reading happens via FC03 (holding) or FC04 (input). foxess_modbus reads the new map via FC03, other sources via FC04. OpenAmpere tries both automatically.

## Connection

- Modbus TCP, port 502, device address (unit ID) 247
- **Only a few TCP connections allowed.** An energy manager that is already connected probably holds one already. Therefore establish only one persistent connection and send all requests one after another.
- Wait 1 s after establishing the connection, 30 ms after each request. At most 100 registers per read request. Polling rate 5–10 s.
- 32-bit values: the high-order word is at the lower address.

## Model detection

- 30000–30015: model string as ASCII, 2 characters per register, strip leading spaces. 30016 ff.: serial number (new map).
- Regex in foxess, checked in this order:
  - `^H3-([\d.]+)-(?:Smart|M)` → H3 Smart (new map)
  - `^[HP]3-Pro-([\d.]+)` → H3 Pro (new map)
  - `^H3-([\d.]+)` → classic H3 (old map)
- Firmware versions: old map 30016–30018, new map 36001–36003.

## New map (H3 Smart/Pro, probably E3)

| Value | Register | Type / scaling |
|---|---|---|
| PV1–4 power | 39279/81/83/85 (2 reg. each) | I32, W |
| PV total (E3) | 39118 | I32 |
| MPPT1 / MPPT2 | 39329 / 39333 | I32, W |
| PV voltage / current | 39070/72/74/76 / 39071/73/75/77 | 0.1 V / 0.01 A |
| Grid total | 38814 | I32, 0.1 W, **+ = export** |
| Grid L1/L2/L3 | 38816–38821 | I32, 0.1 W |
| Meter 2 (external PV) | 38914 ff., active if 38901 = 1 | I32, 0.1 W |
| House consumption | 39225 (L1–L3: 39219–39224) | I32, W |
| Battery power | 39237 | I32, W, **+ = discharging** |
| BMS1 connected | 37002 (BMS2: 37700) | Flag |
| Battery U / I / T | 37609 / 37610 / 37611 | 0.1 |
| State of charge / SoH | 37612 / 37624 | % |
| Cell voltage max/min | 37619 / 37620 | mV |
| Remaining capacity | 37632 | |
| Status / grid disconnection / alarms | 39063 / 39065 bit 0 / 39067–39069 | |

Energy counters, each U32 with 0.01 kWh, as a pair (total, today):

| PV | Charge | Discharge | Export | Grid import | Yield | Consumption |
|---|---|---|---|---|---|---|
| 39601 / 39603 | 39605 / 39607 | 39609 / 39611 | 39613 / 39615 | 39617 / 39619 | 39621 / 39623 | 39629 / 39631 |

### Writing (new map)

| Setting | Register | Values |
|---|---|---|
| Work mode | 49203 (holding, FC06) | 1 self-consumption, 2 feed-in priority, 3 backup/emergency power, 4 peak shaving |
| Max. charge/discharge current | 46607 / 46608 | |
| Min SoC / max SoC / min SoC on grid | 46609 / 46610 / 46611 | % |
| Remote control | 46001 on/off, 46002 timeout, 46003–46004 active power (I32, W, + = discharge/export) | |
| Import/export limit | 46501–46502 / 46616–46617 | I32, W (according to foxess only on Smart) |

The export limit (46616–46617) can be set in OpenAmpere under **Mehr → Einspeisebegrenzung** (More → Export limit). It has not yet been verified on a real E3. Raising it is only possible with control enabled and with the declaration that the grid operator's written approval is on hand. The reference number of this approval is logged. Lowering it is possible at any time. The old register map has no known register for this.

## Old map (classic H3)

| Value | Register | Scaling |
|---|---|---|
| PV1 U/I/P, PV2 U/I/P | 31000–31005 | 0.1 V / 0.1 A / W |
| Grid CT L1–L3 | 31026–31028 | W, + = export |
| Load L1–L3 | 31029–31031 | W (no total register) |
| Battery U / I / P / T / SoC | 31034 / 31035 / 31036 / 31037 / 31038 | P: + = discharging |
| Status / faults | 31041 / 31044–31051 | |
| SoH (FW ≥ 1.80) | 31090 | % |

Energy counters: U32 + daily value, 0.1 kWh

| | Total | Today |
|---|---|---|
| PV | 32000 | 32002 |
| Charge | 32003 | 32005 |
| Discharge | 32006 | 32008 |
| Export | 32009 | 32011 |
| Grid import | 32012 | 32014 |
| Load | 32021 | 32023 |

Writing:

| Setting | Register | Values |
|---|---|---|
| Work mode | 41000 | 0 self-consumption, 1 feed-in, 2 backup, 4 peak shaving |
| Charge/discharge current | 41007 / 41008 | 0.1 A |
| Min SoC / max SoC / min SoC on grid | 41009 / 41010 / 41011 | % |
| Remote control | 44000 on/off, 44001 timeout, 44002–44003 active power | I32, W |

With the old map, read each register in the 41xxx range individually. Do not read 41001–41006, 41012–41013 and 41015; they are invalid.

## Rules for writing (from foxess_modbus)

- Remote control: write the timeout first, then enable, each individually with FC06. Write the active power with FC16, 2 registers starting with the high-order word.
- **Set a fallback work mode before activating.** If the watchdog expires, the inverter then behaves sensibly.
- Only switch off remote control if we switched it on ourselves. Other energy managers and the FoxESS cloud use the same registers.
- The inverter does not respect max SoC during forced charging. The software must stop on its own at SoC ≥ max SoC.
- Read the value back after writing. The read-back may be delayed by a few seconds.
- The H3 has no registers for scheduled charging windows. Charging by electricity price therefore runs via remote control.


## SAJ H2 / HS2

The facts come from community projects; sources and licenses are listed in [NOTICE](../NOTICE). The driver is located in `src/openampere/drivers/saj/`.

- **Connection:** FC03, port 502, device address **1 or 2**. Which one depends on the installation; detection tries both.
- **Detection:** block 0x8F00 with 29 registers:
  - device type, rated power in W, communication version
  - serial number in 0x8F03–0x8F0C
  - product code and firmware versions
  
  Accepted are the known type codes 0x0055–0x005D as well as codes from the family range 0x0050–0x006F, the latter with a warning in the log. Additional plausibility check: work mode 0x4004 ≤ 9 and state of charge 0x406F ≤ 10000.
- **Measurements:**

  | Value | Register | Scaling / sign |
  |---|---|---|
  | Temperatures | 0x4010 (heat sink), 0x4011 (ambient) | ×0.1 °C |
  | Battery U / I | 0x4069 / 0x406A | ×0.1 V / ×0.01 A |
  | Battery temperature | 0x406E | ×0.1 °C |
  | State of charge | 0x406F | ×0.01 % |
  | PV1–4 | 0x4071–0x407C | U ×0.1 V, I ×0.01 A, P in W |
  | House | 0x40A0 | W |
  | PV total | 0x40A5 | W |
  | Battery power | 0x40A6 | W, + = discharging |
  | Grid | 0x40AD | W, + = import |

- **Counters:** U32 in 0.01 kWh starting at 0x40BF. For grid import and export across all three phases, the total counters starting at 0x4167 apply; they are missing on some firmware, in which case the L1 counters are used. Note: SAJ calls export "sell" and grid import "feed-in".
- **Still to be verified on a real device:**
  - the signs of battery and grid power
  - whether the PV counter covers all MPPTs
- **Control:** deliberately locked in OpenAmpere. Known, but unverified:
  - AppMode 0x3647
  - SoC limits 0x3644–0x3646
  - export limit 0x365A in per mille of rated power
