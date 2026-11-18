# RCT Power Storage DC, Storage AC and Power Inverter

> Not official manufacturer information: compiled by the OpenAmpere contributors, can be incomplete or wrong. No
> affiliation with RCT Power GmbH, its insolvency administrator or EKD; not legal advice. See the
> [device knowledge README](../README.md).

**Applies to:** RCT Power Storage DC (4.0 to 12.0, also the "LAN" variants), Power Storage AC and Power Inverter with
the RCT app's local protocol · **Sold as:** RCT Power systems through installers · **OpenAmpere:** driver `rct`
(`src/openampere/drivers/rct/`), "register map" `rct_serial` · **Support and confirmation:** kept in
[support.json](../support.json)

## Summary

- **Why:** RCT Power GmbH (Konstanz) filed for insolvency in September 2026. The installed systems keep running, but
  service, app and portal are uncertain. OpenAmpere reads the inverter locally, without RCT's servers ([#646](https://github.com/Gr33ndev/OpenAmpere/issues/646)).
- **Not Modbus TCP:** the inverters speak RCT's own protocol over TCP, **port 8899**, the same the RCT app uses in the
  home network. Port 502 is closed. Modbus RTU exists only on the RS485 terminal.
- The owner chooses "RCT Power" in the setup (advanced settings); detection over Modbus and the network scan do not
  find it. Port 502 (the setup's default) stands for 8899.
- **No object names the model:** OpenAmpere reads the serial number and the control software version and shows
  "Power Storage / Power Inverter".
- Only the inverter OpenAmpere is connected to is read. In a plant of several RCT inverters (master and slaves), each
  further one can be added with its own address as a further inverter.

## The insolvency

| Fact | Status | Source |
|---|---|---|
| Insolvency proceedings for RCT Power GmbH, Konstanz (commercial register Freiburg im Breisgau, HRB 713720): Amtsgericht Konstanz, file number K 42 IN 457/26, published 2026-09-15. | documented | [insolvenzbekanntmachungen.de](https://neu.insolvenzbekanntmachungen.de/ap/suche.jsf) (court: Konstanz, enter the file number) |
| A provisional insolvency administrator was appointed on 2026-09-14; the search for an investor began. Installed systems keep running; service, warranty, app and portal are open. | documented | [pv magazine, 2026-09-16](https://www.pv-magazine.de/2026/09/16/speicheranbieter-rct-power-meldet-insolvenz-an/); [Photon, 2026-09-23](https://www.photon.info/news/investorensuche-fuer-insolvente-rct-power-gmbh-hat-begonnen/) |
| Battery errors such as "BAT_INTERN Error Class 3" could so far only be reset by RCT's service through a remote connection. OpenAmpere does not do this and writes nothing to the battery management. | derived | owners' reports in the Photovoltaikforum, 2026-10 |

## Models

| Model | Variants | Period (about) | Battery | Network | Status | Source |
|---|---|---|---|---|---|---|
| Power Storage DC, first generation | 4.0, 5.0, 6.0 (3-phase) | 2016–2019 | yes (Power Battery, high voltage) | WLAN; early units without a LAN port | documented | RCT datasheet "Power Storage DC" (04/2018); RCT Power Portal manual (LAN port from certain serial numbers on) |
| Power Storage DC | 4.0, 6.0, 8.0, 10.0 (3-phase; 8.0 and 10.0 with two battery towers) | about 2019–2026 | yes | WLAN and LAN | documented | RCT datasheets and manuals (download area of rct-power.com) |
| Power Storage DC LAN | 6.0, 8.0, 10.0 | 2025–2026 | yes | LAN only, no WLAN | documented | RCT datasheet "Power Storage 6–12" (2507) |
| Power Storage DC 12.0 | 12.0 | announced 2026 | yes | LAN | documented (announced) | RCT datasheet "Power Storage 6–12" (2507) |
| Power Storage AC | 6.0 (older sizes unknown) | about 2017–2026 | yes (AC-coupled, no PV inputs) | WLAN and LAN | documented | RCT compatibility matrix TD25 (02-2024) |
| Power Inverter | 4.0, 6.0 (5.0 in certificates) | about 2016–2026 | no | WLAN and LAN | documented | RCT compatibility matrix TD25 (02-2024) |
| Power Storage DC 6.0 SP | 6.0, single-phase | shown 2024 | yes | unknown | open: whether it speaks the same protocol | trade fair report (TaiyangNews, 2024) |
| Power Storage Flex | 15.0 to 29.9 | 2025–2026 | yes (Power Battery Flex) | WLAN/LAN/4G stick, its own monitoring platform | open: probably another platform with its own interface | RCT manual "Power Storage Flex" (26V1) |

The app, the portal and the master–slave plants cover Storage DC, Storage AC and Power Inverter alike (compatibility
matrix TD25). Power Battery, Power Switch (backup box) and Power Sensor (current transformers) are accessories without a
network interface of their own; the inverter reports their values.

## Protocol

RCT Power's "Serial Communication Protocol", document version 1.14 (2022-02-11). It is not in RCT's public download
area; owners and integrators got it from RCT on request. Facts in own words, see the [README](../README.md#sources).

| Fact | Status | Source |
|---|---|---|
| TCP, default port 8899; the same protocol as the WLAN board uses with the app. | documented | RCT protocol 1.14 |
| Frame: start byte `+` (0x2B), command, length (2 bytes for the long commands), object id (4 bytes), data, CRC-16. Big endian; floats IEEE 754 single precision. One zero byte may come before a frame. | documented | RCT protocol 1.14 |
| CRC-16/CCITT, polynomial 0x1021, start 0xFFFF, over command, length, id and data; padded with one zero byte to an even length. | documented | RCT protocol 1.14 (with two example frames, OpenAmpere's tests check both) |
| `+` and `-` (0x2D) inside a frame are sent as `-` followed by the byte; the escape is not part of the CRC. | documented | RCT protocol 1.14 |
| Commands: 0x01 read, 0x02 write, 0x03 long write, 0x05 response, 0x06 long response (texts, arrays, values over 251 bytes), 0x08 read periodically. Plus 0x40: the same for a further device of a plant, with its 4-byte address. | documented | RCT protocol 1.14 |
| An object the device does not know gets no answer. | derived | python-rctclient documentation (behaviour only, no code) |

## Values

Objects as RCT's document names them; "Registers" here are 4-byte object ids. All read only.

| Object id | Name | Meaning | Type, unit, sign | Status | Source |
|---|---|---|---|---|---|
| 0x7924ABD9 | `inverter_sn` | Serial number | text | documented | RCT protocol 1.14 |
| 0xDDD1C2D0 | `svnversion` | Control software version | text | documented | RCT protocol 1.14 |
| 0xB5317B78, 0xAA9AA253 | `dc_conv.dc_conv_struct[0/1].p_dc` | Power of solar generator A and B | float, W | documented | RCT protocol 1.14 |
| 0xB298395D, 0x5BB8075A | `dc_conv.dc_conv_struct[0/1].u_sg_lp` | Voltage of solar generator A and B | float, V | documented | RCT protocol 1.14 |
| 0x1AC87AA0 | `g_sync.p_ac_load_sum_lp` | Household load ("load household minus external power") | float, W | documented; with an S0 meter open | RCT protocol 1.14 |
| 0x91617C58 | `g_sync.p_ac_grid_sum_lp` | Total grid power, **+ = import** | float, W | documented (meaning); derived (sign) | RCT protocol 1.14; evcc `rct-power` meter (MIT) |
| 0x400F015B | `g_sync.p_acc_lp` | Battery power, **+ = discharging** | float, W | documented | RCT protocol 1.14 |
| 0x4E49AEC5 | `g_sync.p_ac_sum` | Real power of the inverter | float, W | documented | RCT protocol 1.14 |
| 0x959930BF | `battery.soc` | State of charge | float, 0 to 1 | documented (meaning); derived (range) | RCT protocol 1.14; evcc `rct-power` meter |
| 0x381B8BF9 | `battery.soh` | State of health | float, 0 to 1 | documented (meaning); derived (range) | RCT protocol 1.14 |
| 0x65EED11B, 0x21961B58 | `battery.voltage`, `battery.current` | Battery voltage and current | float, V, A; sign of the current open | documented | RCT protocol 1.14 |
| 0x902AFAFB | `battery.temperature` | Battery temperature | float, °C | documented | RCT protocol 1.14 |
| 0xF79D41D9 | `db.temp1` | Heat sink temperature | float, °C | documented | RCT protocol 1.14 |
| 0x5F33284E | `prim_sm.state` | Inverter state (enumeration, meanings not known) | uint8 | documented | RCT protocol 1.14 |
| 0xFC724A9E, 0x68EEFD3D | `energy.e_dc_total[0/1]` | Lifetime energy of solar generator A and B | float, Wh | documented | RCT protocol 1.14 |
| 0x62FBE7DC, 0x44D4C533 | `energy.e_grid_load_total`, `energy.e_grid_feed_total` | Lifetime energy from and to the grid | float, Wh | documented | RCT protocol 1.14 |
| 0xEFF4B537 | `energy.e_load_total` | Lifetime household energy | float, Wh | documented | RCT protocol 1.14 |
| 0x5570401B, 0xA9033880 | `battery.stored_energy`, `battery.used_energy` | Lifetime energy into and out of the battery | float, Wh | documented | RCT protocol 1.14 |
| 0xCE266F0F, 0x97997C93 | `power_mng.soc_min`, `power_mng.soc_max` | The owner's lower and upper state of charge targets | float, 0 to 1 | documented | RCT protocol 1.14 |
| 0xE96F1844 | `io_board.s0_external_power` | A further PV inverter on the S0 input | float, W | documented; not read yet | RCT protocol 1.14 |

## Decisions in OpenAmpere

| Decision | Why | Issue/PR |
|---|---|---|
| Every write method refuses, `supports_control` is off. | Whether the inverter has a remote control with a watchdog timeout (needed for any continuous control) is open. | [#646](https://github.com/Gr33ndev/OpenAmpere/issues/646) |
| Only when the owner chooses "RCT Power"; port 502 stands for 8899. Its error messages name the RCT inverter, not Modbus. | It does not answer on the Modbus port. | [#646](https://github.com/Gr33ndev/OpenAmpere/issues/646) |
| One TCP connection, kept open; all objects of a read are requested at once, the answers collected for up to 2 s. An object without answer stays empty; if nothing answers, the read failed and the connection is opened again. | Like the app; a device without battery or with older firmware may not know every object. | [#646](https://github.com/Gr33ndev/OpenAmpere/issues/646) |
| On every new connection the serial number is read first and compared with the owner's inverter. | Nothing is read from another device at the same address ([#567](https://github.com/Gr33ndev/OpenAmpere/issues/567)). | [#646](https://github.com/Gr33ndev/OpenAmpere/issues/646) |
| Plant frames (further devices behind the master) are ignored. | One driver reads one inverter; further ones get their own address. | [#646](https://github.com/Gr33ndev/OpenAmpere/issues/646) |
| The S0 input (a further PV inverter) is not read yet. | How it adds up with the household load needs a device with one. | [#646](https://github.com/Gr33ndev/OpenAmpere/issues/646) |
| The diagnostics show the objects of the last read with RCT's own signs and units (powers rounded) and which did not answer; not the serial number and no counters. No extra request. | So the first owner's report can confirm the signs. | [#646](https://github.com/Gr33ndev/OpenAmpere/issues/646) |
| No search for a new address when the device is unreachable, and the device query (Mehr → Geräteabfrage) does not read it: it says so. | Both read Modbus registers only. A fixed IP address in the router keeps it reachable. | [#646](https://github.com/Gr33ndev/OpenAmpere/issues/646) |
| No GPL or LGPL code or tables: the object ids come from RCT's document. | Projects whose license does not allow reuse are not a source ([README](../README.md#sources)). | [#646](https://github.com/Gr33ndev/OpenAmpere/issues/646) |

## Open questions

- Every value above on a real device.
- The sign of `g_sync.p_ac_grid_sum_lp` and `battery.current` on a real device.
- How many TCP connections the inverter takes at once (the app and OpenAmpere together).
- Which object tells the model, and what `prim_sm.state` reports in a power cut (for the outage detection).
- Whether Power Storage DC 6.0 SP and Power Storage Flex speak this protocol.

## Sources

- RCT Power GmbH, "Serial Communication Protocol", document version 1.14 (2022-02-11): handed to owners and
  integrators on request; named, not quoted or linked.
- RCT Power datasheets, manuals and the compatibility matrix TD25 (download area of rct-power.com): models and
  interfaces.
- evcc-io/evcc, meter `rct-power` (MIT): the sign of the grid power and the range of the state of charge.
- python-rctclient documentation: behaviour only (no answer for unknown objects); its code and object table
  (GPL-3.0) are not used.
- Insolvency: insolvenzbekanntmachungen.de, pv magazine and Photon, as cited above.
- Research comment in [#646](https://github.com/Gr33ndev/OpenAmpere/issues/646).
