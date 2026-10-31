# Sales names and manufacturer models

> **Not official manufacturer information.** Compiled by the OpenAmpere contributors; can be incomplete or wrong. No
> affiliation with the manufacturers named here or with EKD, not legal advice. See [Device knowledge](../README.md).

**Applies to:** the names under which EKD sold storage systems, the manufacturer models behind them and the
OpenAmpere driver for each, and the hardware EKD sold since 2018 · **Sold as:** see the tables · **OpenAmpere:** drivers `foxess` (register maps
`foxess_h3_new`, `foxess_h3_legacy`) and `saj` (register map `saj_h2`) · **Support:** display and control, see the
device pages · **Confirmed on a real device:** partly

All product and company names are trademarks of their owners and are used here only to describe which devices
OpenAmpere works with.

## Summary

- The storage systems EKD sold under its own "Ampere" name since 2023 are standard hybrid inverters of FoxESS and SAJ;
  OpenAmpere talks to them directly with the same drivers as to devices from other sources.
- Before that, EKD sold storage systems of other makers (Senec, then Solarmax as "AMPERE.Storage", M-TEC, Enphase).
  OpenAmpere supports none of them yet; see [Hardware sold by EKD over time](#hardware-sold-by-ekd-over-time).
- "Ampere.StoragePro E3" is a FoxESS H3; which of its two register maps the E3 uses is still open.
- The older "Ampere.StoragePro", including the "E2", is an SAJ of the H2/HS2 family; the E2 reports type code 0x005E.
- "Ampere.IQ" is EKD's app, which works only through EKD's servers; the "IQ box" or "smartbox" is the energy manager
  installed with the systems ([iq-box.md](iq-box.md)).
- On the "Ampere.StoragePro" systems sold by EKD since 2023 (SAJ and FoxESS), Modbus TCP is already turned on; on
  the older systems of other makers it depends on the device (a Senec has no Modbus TCP at all).

## Sales names

| Sales name | Manufacturer model | OpenAmpere | Unit id | Status | Source |
|---|---|---|---|---|---|
| "Ampere.StoragePro E3" | FoxESS H3 family; 12 kW probably H3 Smart, 22 kW probably H3 Pro (derived from the datasheets, see [Hardware sold by EKD over time](#hardware-sold-by-ekd-over-time)) | driver `foxess`, map detected (`foxess_h3_new` or `foxess_h3_legacy`) | 247 | confirmed | [#11](https://github.com/Gr33ndev/OpenAmpere/issues/11), [#311](https://github.com/Gr33ndev/OpenAmpere/issues/311); [foxess/h3.md](../foxess/h3.md) |
| "Ampere.StoragePro" (older systems) | SAJ H2/HS2 family hybrid inverter | driver `saj`, map `saj_h2` | 1 or 2 (depends on the communication module) | confirmed | [#330](https://github.com/Gr33ndev/OpenAmpere/issues/330), [#334](https://github.com/Gr33ndev/OpenAmpere/issues/334), [#336](https://github.com/Gr33ndev/OpenAmpere/issues/336); [saj/h2-family.md](../saj/h2-family.md) |
| "Ampere Storage Pro E2" | SAJ with type code 0x005E ("3P-X E2", three-phase) | driver `saj`, map `saj_h2` | 1 or 2 | confirmed | [#339](https://github.com/Gr33ndev/OpenAmpere/issues/339), [#378](https://github.com/Gr33ndev/OpenAmpere/issues/378), [#379](https://github.com/Gr33ndev/OpenAmpere/issues/379); [saj/0x005e-3p-x-e2.md](../saj/0x005e-3p-x-e2.md) |
| "AMPERE.Storage" (without "Pro") | Solarmax MAX.STORAGE Ultimate (datasheet names Solarmax as maker; the model is derived from the specifications) | driver `solarmax`, display only ([solarmax/max-storage.md](../solarmax/max-storage.md)) | 1 | documented (maker), derived (model) | EKD datasheet "AMPERE.Storage", 2022-07 ([PDF](https://www.ekd-solar.de/wp-content/uploads/2022/08/Ampere.Storage_Datenblatt.pdf)) |
| "AMPERE.Charge" | Weidmüller AC Smart Value wallbox, 11 kW | wallbox module `weidmueller_ac_smart`, display only ([weidmueller/ac-smart.md](../weidmueller/ac-smart.md)); control through evcc (`weidmüller` template) | 255 | documented (product), derived (evcc template) | EKD datasheets "AMPERE.Charge", 2023-04 and 2024-01; [Photovoltaikforum thread on the Ampere.Charge](https://www.photovoltaikforum.com/thread/227016-ampere-charge-weidm%C3%BCller-wallbox-energiekonzepte-deutschland-ampere-storage-pro/); evcc-io/evcc template `templates/definition/charger/weidmüller.yaml` (requirement `sponsorship`) |
| "Ampere.ChargePro" | FoxESS AC EV Charger, 22 kW | wallbox module `foxess_ac` | 1 | documented (sales name), confirmed (FoxESS charger with EKD systems) | [amperesolar.de/chargepro](https://amperesolar.de/chargepro/); [foxess/ac-charger.md](../foxess/ac-charger.md) |
| "Ampere.IQ" | EKD's app; works only through EKD's servers | no driver; history import through EKD's customer API | – | derived | [README](../../../README.md) |
| "IQ box", "smartbox" | the energy manager installed with EKD systems | no driver; OpenAmpere detects its effects | – | derived | [iq-box.md](iq-box.md) |

## Manufacturer model names

FoxESS H3 family, as the driver tells them apart by the model string:

| Model | Model string | Register map | Status | Source |
|---|---|---|---|---|
| H3 (classic) | other H3 strings | `foxess_h3_new` or `foxess_h3_legacy`, detected | derived | `drivers/foxess/driver.py`; [docs/devices.md](../../devices.md) |
| H3 Smart | `H3-…-Smart` or `H3-…-M` | `foxess_h3_new` | derived | as above |
| H3 Pro | `H3-Pro-…` or `P3-Pro-…` | `foxess_h3_new` | derived | as above |

SAJ type codes (register 0x8F00 block), as the driver names them:

| Type code | Name | Control in OpenAmpere | Status | Source |
|---|---|---|---|---|
| 0x0055 | AS2 3–6K | read only | derived | `drivers/saj/registers.py` |
| 0x0056 | AS2 5–10K | read only | derived | as above |
| 0x005A | H2 3–6K S2 | yes | derived | as above |
| 0x005B | H2 5–10K S3 | yes | derived | as above |
| 0x005C | HS2 3–6K S2 | yes | derived | as above |
| 0x005D | HS2 5–10K S3 | yes | derived | as above |
| 0x005E | "3P-X E2" (not in SAJ's protocol) | yes | confirmed (reported by owners of an E2) | [#339](https://github.com/Gr33ndev/OpenAmpere/issues/339), [#379](https://github.com/Gr33ndev/OpenAmpere/issues/379) |

Other codes in the SAJ family range 0x0050–0x006F are read with the same map and a warning in the log
([docs/devices.md](../../devices.md)).

## Hardware sold by EKD over time

Collected from EKD's datasheets and press releases, trade press and owners' reports in forums (2026-10). Periods are
approximate: most come from datasheet dates, and when EKD stopped selling a product is mostly unknown. "Local
interface" says what an owner's software could use, not what OpenAmpere does.

<!-- generated:ekd from docs/devices/support.json by scripts/device-support.mjs; edit it there -->

Storage systems and hybrid inverters:

| Period (about) | Sold as | Manufacturer model | Local interface | OpenAmpere | Status | Source |
|---|---|---|---|---|---|---|
| 2018–2019 | – | Senec.Home V2.1 (AC-coupled, external PV inverter on RS485) | local web interface `lala.cgi` (JSON) | not supported | open: no source shows EKD selling it | energie-experten.org, "SENEC" |
| 2020 – mid-2022, some into 2023 | Senec storage, EKD as "SENEC-Fachpartner", with SENEC.Cloud | Senec.Home V3 hybrid and hybrid duo (DC hybrid) | `lala.cgi`, HTTPS only with a self-signed certificate since about firmware 825, some fields gone | not supported | documented (EKD website 2021–2022 in the web archive, Senec datasheets hosted by EKD); derived (owners' reports, end of the period) | [pv magazine, 2022-06-16](https://www.pv-magazine.de/2022/06/16/ekd-beteiligt-sich-an-solarmax/); [Photovoltaikforum on a Senec V3 hybrid duo bought through EKD](https://www.photovoltaikforum.com/thread/222119-suche-ersatz-f%C3%BCr-senec-v3-hybrid-duo/); marq24/ha-senec-v3 and mchwalisz/pysenec (both Apache-2.0, for `lala.cgi`) |
| mid-2022 – mid-2023 | "AMPERE.Storage" | Solarmax MAX.STORAGE Ultimate: 10 kW, three-phase, 2 MPPT, 6–15 kWh in 3 kWh packs | Modbus TCP and RTU, KNX (datasheet); battery control only after Solarmax unlocked "Connectivity+" | driver `solarmax`, display only ([solarmax/max-storage.md](../solarmax/max-storage.md)). Not yet confirmed | documented (maker, interfaces), derived (model, control, end of the period) | EKD datasheet "AMPERE.Storage", 2022-07; evcc template for the Solarmax MAX.STORAGE |
| from 2022-10 | M-TEC Energy-Butler | M-TEC Energy-Butler GEN3: 10 kW hybrid, about 11 kWh | Modbus over TCP through the Wi-Fi/LAN module, port 5743 (502 on newer module firmware); unit id (252, 247 or 255) and framing (RTU frames or plain Modbus TCP) differ between sources. The inverter is a Solinteg. | not supported | documented (sale), derived (interface), open (unit id, framing) | [EKD news on the M-TEC Energy-Butler](https://www.ekd-solar.de/news/energiekonzepte-deutschland-erweitert-portfolio-mit-modularem-energy-butler-von-m-tec/); croedel/MTECmqtt up to commit 32cdf27 (MIT); evcc template `solinteg-gen3` (MIT); [#520](https://github.com/Gr33ndev/OpenAmpere/issues/520) |
| from 2022-10 | Enphase IQ Battery | Enphase IQ Battery (AC-coupled); which models is not stated | IQ Gateway (Envoy) local HTTPS API; since firmware 7 a token from the owner's Enphase account is needed | not supported | documented (cooperation), open (models) | EKD press release on the Enphase cooperation, 2022-10 ([PDF](https://www.ekd-solar.de/wp-content/uploads/2022/10/EKD_Enphase_Neue_Kooperation.pdf)); pyenphase/pyenphase (MIT) |
| from 2022-08 | VARTA element backup | VARTA element backup (AC-coupled) | not looked into | not supported | open: only a datasheet hosted by EKD, no owner's report | VARTA datasheet "element backup" on ekd-solar.de, 2022-08 (web archive) |
| 2023-04 – about 2024 | "AMPERE.Storage.Pro", "Ampere.StoragePro", type "ASP 10KW-3P-X E2" | SAJ H2/HS2 family (the E2 reports type code 0x005E); 6–20 kWh | Modbus TCP 502 | driver `saj`. Monitoring confirmed and in daily use on a "3P-X E2" (older "Ampere.StoragePro"); control not yet confirmed | documented (start, type string, capacity), derived (end), confirmed (SAJ, 0x005E) | EKD datasheets "AMPERE.Storage.Pro", 2023-04 (Rev02: 6–20 kWh), and "AMPERE.StoragePro", 2023-07 (Rev03: type "ASP 10KW-3P-X E2"); see [Sales names](#sales-names) |
| about 2024 – 2026 | "Ampere.StoragePro E3", 12 kW ("ASP-12KW-3P-X-A-E3") | FoxESS H3 family, probably H3 Smart (3 MPPT) | Modbus TCP 502 | driver `foxess`. Monitoring and control confirmed | confirmed (FoxESS H3), derived (H3 Smart, start of the period) | see [Sales names](#sales-names); [Photovoltaikforum on the maker of the E3](https://www.photovoltaikforum.com/thread/235451-wer-ist-der-tats%C3%A4chliche-hersteller-vom-neuen-speicher-der-firma-ekd-solar/); amperesolar.de datasheet "Ampere.StoragePro E3 12 kW", 2026-05 |
| 2025 – 2026 | "Ampere.StoragePro E3", 22 kW ("ASP-22KW-3P-A-X-E3"; the datasheets put A and X in the other order than for the 12 kW type) | FoxESS, probably H3 Pro (4 MPPT and 22 kW, more than any H3 Smart) | Modbus TCP | driver `foxess` should detect it as H3 Pro. Not yet confirmed | open: no owner's report; derived (period) | amperesolar.de datasheet "Ampere.StoragePro E3 22 kW", 2026-05 |

Wallboxes:

| Period (about) | Sold as | Manufacturer model | Local interface | OpenAmpere | Status | Source |
|---|---|---|---|---|---|---|
| 2020–2022 | Senec wallbox | SENEC.Wallbox | not looked into | not supported | open: only a datasheet hosted by EKD; derived (period) | Senec datasheet "SENEC.Wallbox" on ekd-solar.de, 2020-03 (web archive) |
| 2023–2024 | "AMPERE.Charge" | Weidmüller AC Smart Value, 11 kW | Modbus TCP 502, Modbus RTU, OCPP 1.6J | wallbox module `weidmueller_ac_smart`, display only ([weidmueller/ac-smart.md](../weidmueller/ac-smart.md)); control through evcc (needs an evcc sponsor token). Not yet confirmed | documented (product, interfaces, start), derived (evcc, sponsor token, end of the period) | see [Sales names](#sales-names); evcc-io/evcc template `templates/definition/charger/weidmüller.yaml` (requirement `sponsorship`) |
| about 2024 – 2026 | "Ampere.ChargePro" | FoxESS AC EV Charger, 22 kW | Modbus TCP 502, OCPP 1.6 | wallbox module `foxess_ac`. Monitoring confirmed on a charger of an EKD system; control not yet confirmed | documented (sales name), confirmed (FoxESS charger with EKD systems), derived (period) | [foxess/ac-charger.md](../foxess/ac-charger.md) |

Around the storage system:

| Period (about) | Sold as | Manufacturer model | Local interface | OpenAmpere | Status | Source |
|---|---|---|---|---|---|---|
| 2018–2022 | – | PV inverter next to a Senec, e.g. Fronius Symo with Datamanager 2.0, coupled to the Senec over RS485 | Fronius Solar API (HTTP, JSON); Modbus TCP probably not free while the Senec reads over Modbus RTU | not supported | derived (coupling); open: no source ties Fronius to EKD | [Photovoltaikforum on a Fronius Symo at a Senec over RS485](https://www.photovoltaikforum.com/thread/148472-fronius-symo-rs485-an-senec-speicher-per-funk-oder-powerline/); Fronius Datamanager 2.0 manual, Modbus settings |
| 2023–2026 | "Ampere.IQ", "IQ box", "smartbox" | Kiwigrid energy manager | REST in the LAN; no Modbus server of its own (it is a Modbus TCP client of the inverter and the charger) | no driver; see [iq-box.md](iq-box.md) | derived | evcc-io/evcc discussion 8394; [Photovoltaikforum on the Ampere Storage Pro](https://www.photovoltaikforum.com/thread/222147-ekd-speicher-ampere-storage-pro-ein-chinamodell-und-weitere-fragen-dazu/) |
| 2023–2024 | "AMPERE.StoragePro Meter" | Chint DTSU666 grid meter (SAJ systems) | RS485 to the inverter | read through the inverter | derived | as above |
| from 2024-08 | "Ampere.Offgrid" | three-phase 63 A transfer switch for backup power; maker not stated | none | nothing to read | documented | EKD datasheet "Ampere.Offgrid", 2024-08 |
| about 2022 – 2026 | "Ampere.SolarPro" | PV modules of SOLYCO Solar AG | none | nothing to read | documented (maker), derived (period) | EKD datasheets "Ampere.SolarPro" |
| from 2025 | heat pumps of partner makers | Aira, Defro, Galvany, S&K, WKD, Heat Hero | SG Ready | SG Ready through a relay ([consumers.md](consumers.md)). Not yet confirmed | documented (partners), derived (period) | [ekd-solar.de/waermepumpe](https://www.ekd-solar.de/waermepumpe/) |
| – | heating rod | my-PV, model not stated | Modbus TCP | driver `mypv` ([mypv/elwa.md](../mypv/elwa.md)). Not yet confirmed | derived | Photovoltaikforum on the Ampere Storage Pro (as above) |

<!-- /generated:ekd -->

## Behaviour

| Fact | Status | Source |
|---|---|---|
| On the "Ampere.StoragePro" systems sold by EKD since 2023 (SAJ and FoxESS), Modbus TCP is already turned on; owners do not need to switch it on. This does not hold for the older systems of other makers. | derived | [#84](https://github.com/Gr33ndev/OpenAmpere/issues/84), [#85](https://github.com/Gr33ndev/OpenAmpere/issues/85); [README](../../../README.md) |
| EKD stands for Energiekonzepte Deutschland GmbH. It and other companies of its group filed for insolvency in early October 2026; the proceedings of seven companies run at the local court of Leipzig. | documented (public insolvency announcements) | [#85](https://github.com/Gr33ndev/OpenAmpere/issues/85), [#91](https://github.com/Gr33ndev/OpenAmpere/issues/91), [#92](https://github.com/Gr33ndev/OpenAmpere/issues/92); [README](../../../README.md) |
| EKD was founded in 2018 by the founder of Senec and sold Senec storage systems at first; until 2022 its website called it a Senec partner. | documented | [Wikipedia, "Energiekonzepte Deutschland"](https://de.wikipedia.org/wiki/Energiekonzepte_Deutschland); [pv magazine, 2022-06-16](https://www.pv-magazine.de/2022/06/16/ekd-beteiligt-sich-an-solarmax/) |
| EKD became majority owner of Solarmax in 2022; Solarmax filed for insolvency in late 2024, before the EKD group. | documented | [pv magazine, 2022-06-16](https://www.pv-magazine.de/2022/06/16/ekd-beteiligt-sich-an-solarmax/); pv magazine, 2024-12-06 |
| EKD switched off the storage function of "AMPERE.Storage" remotely in September 2025 and released it again after a fuse was fitted on site. | confirmed (owner reports) | [iq-box.md](iq-box.md); [Photovoltaikforum thread on the shutdown](https://www.photovoltaikforum.com/thread/252527-ekd-zwangsabschaltung-ampere-storage-zum-18-09-2025-ohne-vorherige-kundenbeteili/) |
| Senec stopped taking new customers in July 2026; app, service and the exchange of battery modules go on for existing systems. | documented (trade press) | [ess-news, 2026-07-14](https://www.ess-news.com/2026/07/14/enbw-scraps-senec-investor-search-shifts-storage-business-to-existing-customers/) |
| Energieversum, which also sold Senec storage, is a separate installer, not an EKD brand. | derived | company websites |
| "Ampere" is a sales name; the devices come from the makers in the tables above. The datasheets of the "Ampere.StoragePro E3" (2026-05) name AMPERE German Electric Innovation GmbH as maker ("Hersteller") although the device is a FoxESS; the 2023 datasheets of the SAJ-based "AMPERE.StoragePro" carry EKD's copyright instead. | documented | amperesolar.de datasheet "Ampere.StoragePro E3 12 kW", 2026-05; EKD datasheets "AMPERE.Storage.Pro", 2023-04, and "AMPERE.StoragePro", 2023-07 |

## Decisions in OpenAmpere

| Decision | Why | Issue/PR |
|---|---|---|
| The model string is only a hint; the driver checks which register map really answers and prefers the newer one. Owners can choose the map by hand. | Sales names and model strings do not tell the firmware's register map. | [docs/devices.md](../../devices.md) |
| Detection does not depend on a sales name: FoxESS at unit 247 first, then SAJ at 1 and 2, then Solarmax at 1 ([modbus.md](modbus.md)). | The same devices are sold under several names. | [docs/devices.md](../../devices.md) |
| App texts call the box the "former smartbox", without a manufacturer name; the README names companies only in the background and no-affiliation notes. | Names only descriptively. | [#53](https://github.com/Gr33ndev/OpenAmpere/issues/53) |
| A driver for a storage system EKD sold must never switch on a storage function that EKD switched off remotely (as with "AMPERE.Storage" in 2025); at most it reads. | The lock was announced as a safety retrofit; switching it off is the safe direction. | [#514](https://github.com/Gr33ndev/OpenAmpere/issues/514), [#515](https://github.com/Gr33ndev/OpenAmpere/pull/515) |

## Open questions

- Which register map (newer or older firmware) the "Ampere.StoragePro E3" uses, and which H3 variant it is. The E3
  is a FoxESS H3 ([#311](https://github.com/Gr33ndev/OpenAmpere/issues/311)), but its map is still open in
  [registers.md](../../registers.md) ([#185](https://github.com/Gr33ndev/OpenAmpere/issues/185)).
- Whether every "Ampere.StoragePro" system has Modbus TCP turned on is known only from owners' reports, not from a
  document.
- Whether EKD sold Senec.Home V2.1 in 2018–2019; the web archive has no copy of EKD's website before December 2021.
- Which PV inverters EKD installed next to a Senec, and whether the Senec reads a Fronius over the Datamanager's Modbus
  RTU or over Solar Net (only with Solar Net would Modbus TCP stay free).
- Whether "AMPERE.Storage" ships with Modbus TCP on, or only after "Connectivity+"; what the fuse fitted in 2025
  protects against, and whether systems without it are still locked.
- Which Enphase models EKD installed, and when EKD stopped selling M-TEC, Enphase and VARTA.
- Which FoxESS model is the 22 kW "E3" and which battery modules the "E3" uses (the 12 kW datasheet names both 3.3 kWh
  and 4.14 kWh); which grid meter "E3" systems have.
- How many systems EKD sold of each generation.

## Sources

- Issues and pull requests: [#11](https://github.com/Gr33ndev/OpenAmpere/issues/11),
  [#53](https://github.com/Gr33ndev/OpenAmpere/issues/53), [#84](https://github.com/Gr33ndev/OpenAmpere/issues/84),
  [#85](https://github.com/Gr33ndev/OpenAmpere/issues/85), [#91](https://github.com/Gr33ndev/OpenAmpere/issues/91),
  [#92](https://github.com/Gr33ndev/OpenAmpere/issues/92), [#185](https://github.com/Gr33ndev/OpenAmpere/issues/185),
  [#311](https://github.com/Gr33ndev/OpenAmpere/issues/311), [#330](https://github.com/Gr33ndev/OpenAmpere/issues/330),
  [#334](https://github.com/Gr33ndev/OpenAmpere/issues/334), [#336](https://github.com/Gr33ndev/OpenAmpere/issues/336),
  [#339](https://github.com/Gr33ndev/OpenAmpere/issues/339), [#378](https://github.com/Gr33ndev/OpenAmpere/issues/378),
  [#379](https://github.com/Gr33ndev/OpenAmpere/issues/379).
- Public insolvency announcements (court of Leipzig), linked from the [README](../../../README.md).
- EKD and Ampere datasheets and press releases on ekd-solar.de and amperesolar.de: "AMPERE.Storage" (2022-07),
  "AMPERE.Storage.Pro" (2023-04, Rev02), "AMPERE.StoragePro" (2023-07, Rev03), "AMPERE.Charge" (2023-04, 2024-01),
  "Ampere.StoragePro E3 12 kW" and "Ampere.StoragePro E3 22 kW" (both 2026-05), "Ampere.Offgrid"
  (2024-08), "Ampere.SolarPro", M-TEC Energy-Butler (2022-10), Enphase cooperation (2022-10); older pages and
  datasheets (Senec, VARTA) through the web archive.
- Trade press: pv magazine (2022-06-16, 2024-12-06), ess-news (2026-07-14), energie-experten.org ("SENEC");
  Wikipedia, "Energiekonzepte Deutschland".
- Owners' reports in Photovoltaikforum threads 148472, 222119, 222147, 227016, 235451 and 252527 (linked above).
- [evcc-io/evcc discussion 8394](https://github.com/evcc-io/evcc/discussions/8394): owners on the IQ box and its REST interface.
- Projects, used for interfaces only: marq24/ha-senec-v3 and mchwalisz/pysenec (Apache-2.0), croedel/MTECmqtt up to
  commit 32cdf27 (MIT; LGPL-3.0 after it), pyenphase/pyenphase (MIT), evcc-io/evcc templates (MIT).
- Code: `src/openampere/drivers/registry.py`, `src/openampere/drivers/foxess/driver.py`,
  `src/openampere/drivers/saj/registers.py`.
