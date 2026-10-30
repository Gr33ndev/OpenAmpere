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
- On the systems sold by EKD, Modbus TCP is already turned on.

## Sales names

| Sales name | Manufacturer model | OpenAmpere | Unit id | Status | Source |
|---|---|---|---|---|---|
| "Ampere.StoragePro E3" | FoxESS H3 family (whether H3, H3 Smart or H3 Pro is not stated) | driver `foxess`, map detected (`foxess_h3_new` or `foxess_h3_legacy`) | 247 | confirmed | [#11](https://github.com/Gr33ndev/OpenAmpere/issues/11), [#311](https://github.com/Gr33ndev/OpenAmpere/issues/311); [foxess/h3.md](../foxess/h3.md) |
| "Ampere.StoragePro" (older systems) | SAJ H2/HS2 family hybrid inverter | driver `saj`, map `saj_h2` | 1 or 2 (depends on the communication module) | confirmed | [#330](https://github.com/Gr33ndev/OpenAmpere/issues/330), [#334](https://github.com/Gr33ndev/OpenAmpere/issues/334), [#336](https://github.com/Gr33ndev/OpenAmpere/issues/336); [saj/h2-family.md](../saj/h2-family.md) |
| "Ampere Storage Pro E2" | SAJ with type code 0x005E ("3P-X E2", three-phase) | driver `saj`, map `saj_h2` | 1 or 2 | confirmed | [#339](https://github.com/Gr33ndev/OpenAmpere/issues/339), [#378](https://github.com/Gr33ndev/OpenAmpere/issues/378), [#379](https://github.com/Gr33ndev/OpenAmpere/issues/379); [saj/0x005e-3p-x-e2.md](../saj/0x005e-3p-x-e2.md) |
| "AMPERE.Storage" (without "Pro") | Solarmax MAX.STORAGE Ultimate (datasheet names Solarmax as maker; the model is derived from the specifications) | no driver | – | documented (maker), derived (model) | EKD datasheet "AMPERE.Storage", 2022-07 ([PDF](https://www.ekd-solar.de/wp-content/uploads/2022/08/Ampere.Storage_Datenblatt.pdf)) |
| "AMPERE.Charge" | Weidmüller AC Smart Value wallbox, 11 kW | no driver; through evcc (`weidmüller` template) | – | documented | EKD datasheets "AMPERE.Charge", 2023-04 and 2024-01; [Photovoltaikforum thread on the Ampere.Charge](https://www.photovoltaikforum.com/thread/227016-ampere-charge-weidm%C3%BCller-wallbox-energiekonzepte-deutschland-ampere-storage-pro/) |
| "Ampere.ChargePro" | FoxESS AC EV Charger, 22 kW | wallbox module `foxess_ac` | 1 | documented (specifications), confirmed (FoxESS charger with EKD systems) | [amperesolar.de/chargepro](https://amperesolar.de/chargepro/); [foxess/ac-charger.md](../foxess/ac-charger.md) |
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

Storage systems and hybrid inverters:

| Period (about) | Sold as | Manufacturer model | Local interface | OpenAmpere | Status | Source |
|---|---|---|---|---|---|---|
| 2018–2019 | – | Senec.Home V2.1 (AC-coupled, external PV inverter on RS485) | local web interface `lala.cgi` (JSON) | not supported | open: no source shows EKD selling it | energie-experten.org, "SENEC" |
| 2020 – mid-2022, some into 2023 | Senec storage, EKD as "SENEC-Fachpartner", with SENEC.Cloud | Senec.Home V3 hybrid and hybrid duo (DC hybrid) | `lala.cgi`, HTTPS only with a self-signed certificate since about firmware 825, some fields gone | not supported | documented (EKD website 2021–2022 in the web archive, Senec datasheets hosted by EKD); derived (owners' reports) | [pv magazine, 2022-06-16](https://www.pv-magazine.de/2022/06/16/ekd-beteiligt-sich-an-solarmax/); [Photovoltaikforum on a Senec V3 hybrid duo bought through EKD](https://www.photovoltaikforum.com/thread/222119-suche-ersatz-f%C3%BCr-senec-v3-hybrid-duo/); marq24/ha-senec-v3 (Apache-2.0) |
| mid-2022 – mid-2023 | "AMPERE.Storage" | Solarmax MAX.STORAGE Ultimate: 10 kW, three-phase, 2 MPPT, 6–15 kWh in 3 kWh packs | Modbus TCP and RTU, KNX (datasheet); battery control only after Solarmax unlocked "Connectivity+" | not supported | documented (maker, interfaces), derived (model, control) | EKD datasheet "AMPERE.Storage", 2022-07; evcc template for the Solarmax MAX.STORAGE |
| from 2022-10 | M-TEC Energy-Butler | M-TEC Energy-Butler GEN3: 10 kW hybrid, about 11 kWh | Modbus RTU frames over TCP through the Wi-Fi/LAN module, port 5743 (502 on newer firmware), slave id 252 | not supported | documented (sale), derived (interface) | [EKD news on the M-TEC Energy-Butler](https://www.ekd-solar.de/news/energiekonzepte-deutschland-erweitert-portfolio-mit-modularem-energy-butler-von-m-tec/); croedel/M-TEC-API (MIT); evcc template `m-tec-energy-butler-gen3` |
| from 2022-10 | Enphase IQ Battery | Enphase IQ Battery (AC-coupled); which models is not stated | IQ Gateway (Envoy) local HTTPS API; since firmware 7 a token from the owner's Enphase account is needed | not supported | documented (cooperation), open (models) | EKD press release on the Enphase cooperation, 2022-10 ([PDF](https://www.ekd-solar.de/wp-content/uploads/2022/10/EKD_Enphase_Neue_Kooperation.pdf)); pyenphase/pyenphase (MIT) |
| from 2022-08 | VARTA element backup | VARTA element backup (AC-coupled) | not looked into | not supported | open: only a datasheet hosted by EKD, no owner's report | VARTA datasheet "element backup" on ekd-solar.de, 2022-08 (web archive) |
| 2023-04 – 2024 | "AMPERE.Storage.Pro", "Ampere.StoragePro", type "ASP 10KW-3P-X E2" | SAJ HS2 family, type code 0x005E; 6–20 kWh | Modbus TCP 502 | driver `saj` | confirmed | see [Sales names](#sales-names) |
| 2024-08 – 2026 | "Ampere.StoragePro E3", 12 kW ("ASP-12KW-3P-X-A-E3") | FoxESS H3 family, probably H3 Smart (3 MPPT) | Modbus TCP 502 | driver `foxess` | confirmed (FoxESS H3), derived (H3 Smart) | see [Sales names](#sales-names); amperesolar.de datasheet "Ampere.StoragePro 12 kW", 2026-06 |
| 2025 – 2026 | "Ampere.StoragePro E3", 22 kW ("ASP-22KW-3P-A-X-E3") | FoxESS, probably H3 Pro (4 MPPT and 22 kW, more than any H3 Smart) | Modbus TCP | driver `foxess` (H3 Pro) | open: no owner's report | amperesolar.de datasheet "Ampere.StoragePro 22 kW", 2026-05 |

Wallboxes:

| Period (about) | Sold as | Manufacturer model | Local interface | OpenAmpere | Status | Source |
|---|---|---|---|---|---|---|
| 2020–2022 | Senec wallbox | SENEC.Wallbox | not looked into | not supported | open: only a datasheet hosted by EKD | Senec datasheet "SENEC.Wallbox" on ekd-solar.de, 2020-03 (web archive) |
| 2023–2024 | "AMPERE.Charge" | Weidmüller AC Smart Value, 11 kW | Modbus TCP 502, Modbus RTU, OCPP 1.6J | through evcc (needs an evcc sponsor token); OCPP check read-only | documented | see [Sales names](#sales-names) |
| 2024–2026 | "Ampere.ChargePro" | FoxESS AC EV Charger, 22 kW | Modbus TCP 502, OCPP 1.6 | wallbox module `foxess_ac` | confirmed | [foxess/ac-charger.md](../foxess/ac-charger.md) |

Around the storage system:

| Period (about) | Sold as | Manufacturer model | Local interface | OpenAmpere | Status | Source |
|---|---|---|---|---|---|---|
| 2018–2022 | – | PV inverter next to a Senec, e.g. Fronius Symo with Datamanager 2.0, coupled to the Senec over RS485 | Fronius Solar API (HTTP, JSON); Modbus TCP probably not free while the Senec reads over Modbus RTU | not supported | derived (coupling); open: no source ties Fronius to EKD | [Photovoltaikforum on a Fronius Symo at a Senec over RS485](https://www.photovoltaikforum.com/thread/148472-fronius-symo-rs485-an-senec-speicher-per-funk-oder-powerline/); Fronius Datamanager 2.0 manual, Modbus settings |
| 2023–2026 | "Ampere.IQ", "IQ box", "smartbox" | Kiwigrid energy manager | REST in the LAN; no Modbus | no driver; see [iq-box.md](iq-box.md) | derived | evcc-io/evcc discussion 8394; [Photovoltaikforum on the Ampere Storage Pro](https://www.photovoltaikforum.com/thread/222147-ekd-speicher-ampere-storage-pro-ein-chinamodell-und-weitere-fragen-dazu/) |
| 2023–2024 | "AMPERE.StoragePro Meter" | Chint DTSU666 grid meter (SAJ systems) | RS485 to the inverter | read through the inverter | derived | as above |
| from 2024-08 | "Ampere.Offgrid" | three-phase 63 A transfer switch for backup power; maker not stated | none | nothing to read | documented | EKD datasheet "Ampere.Offgrid", 2024-08 |
| 2022–2026 | "Ampere.SolarPro" | PV modules of SOLYCO Solar AG | none | nothing to read | documented | EKD datasheets "Ampere.SolarPro" |
| from 2025 | heat pumps of partner makers | Aira, Defro, Galvany, S&K, WKD, Heat Hero | SG Ready | SG Ready through a relay ([consumers.md](consumers.md)) | documented | [ekd-solar.de/waermepumpe](https://www.ekd-solar.de/waermepumpe/) |
| – | heating rod | my-PV, model not stated | Modbus TCP | driver `mypv` ([mypv/elwa.md](../mypv/elwa.md)) | derived | Photovoltaikforum on the Ampere Storage Pro (as above) |

## Behaviour

| Fact | Status | Source |
|---|---|---|
| On the storage systems sold by EKD, Modbus TCP is already turned on; owners do not need to switch it on. | derived | [#84](https://github.com/Gr33ndev/OpenAmpere/issues/84), [#85](https://github.com/Gr33ndev/OpenAmpere/issues/85); [README](../../../README.md) |
| EKD stands for Energiekonzepte Deutschland GmbH. It and other companies of its group filed for insolvency in early October 2026; the proceedings of seven companies run at the local court of Leipzig. | documented (public insolvency announcements) | [#85](https://github.com/Gr33ndev/OpenAmpere/issues/85), [#91](https://github.com/Gr33ndev/OpenAmpere/issues/91), [#92](https://github.com/Gr33ndev/OpenAmpere/issues/92); [README](../../../README.md) |
| EKD was founded in 2018 by the founder of Senec and sold Senec storage systems at first; until 2022 its website called it a Senec partner. | documented | [Wikipedia, "Energiekonzepte Deutschland"](https://de.wikipedia.org/wiki/Energiekonzepte_Deutschland); [pv magazine, 2022-06-16](https://www.pv-magazine.de/2022/06/16/ekd-beteiligt-sich-an-solarmax/) |
| EKD became majority owner of Solarmax in 2022; Solarmax filed for insolvency in late 2024, before the EKD group. | documented | [pv magazine, 2022-06-16](https://www.pv-magazine.de/2022/06/16/ekd-beteiligt-sich-an-solarmax/); pv magazine, 2024-12-06 |
| EKD switched off the storage function of "AMPERE.Storage" remotely in September 2025 and released it again after a fuse was fitted on site. OpenAmpere must not switch on a storage system that was locked this way. | confirmed (owner reports) | [iq-box.md](iq-box.md); [Photovoltaikforum thread on the shutdown](https://www.photovoltaikforum.com/thread/252527-ekd-zwangsabschaltung-ampere-storage-zum-18-09-2025-ohne-vorherige-kundenbeteili/) |
| Senec stopped taking new customers in July 2026; app, service and the exchange of battery modules go on for existing systems. The EKD insolvency does not affect Senec. | documented (trade press) | [ess-news, 2026-07-14](https://www.ess-news.com/2026/07/14/enbw-scraps-senec-investor-search-shifts-storage-business-to-existing-customers/) |
| Energieversum, which also sold Senec storage, is a separate installer, not an EKD brand. | derived | company websites |
| The products named "Ampere" (storage, wallbox, modules, backup switch) come from AMPERE German Electric Innovation GmbH and were sold only through EKD. | documented (datasheets name it as maker), derived (sold only through EKD) | amperesolar.de datasheets |

## Decisions in OpenAmpere

| Decision | Why | Issue/PR |
|---|---|---|
| The model string is only a hint; the driver checks which register map really answers and prefers the newer one. Owners can choose the map by hand. | Sales names and model strings do not tell the firmware's register map. | [docs/devices.md](../../devices.md) |
| Detection does not depend on a sales name: FoxESS at unit 247 first, then SAJ at 1 and 2 ([modbus.md](modbus.md)). | The same devices are sold under several names. | [docs/devices.md](../../devices.md) |
| App texts call the box the "former smartbox", without a manufacturer name; the README names companies only in the background and no-affiliation notes. | Names only descriptively. | [#53](https://github.com/Gr33ndev/OpenAmpere/issues/53) |

## Open questions

- Which register map (newer or older firmware) the "Ampere.StoragePro E3" uses, and which H3 variant it is. The E3
  is a FoxESS H3 ([#311](https://github.com/Gr33ndev/OpenAmpere/issues/311)), but its map is still open in
  [registers.md](../../registers.md) ([#185](https://github.com/Gr33ndev/OpenAmpere/issues/185)).
- Who made the IQ box is not stated in OpenAmpere's sources.
- Whether every system sold by EKD has Modbus TCP turned on is known only from owners' reports, not from a document.
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
  "AMPERE.Charge" (2023-04, 2024-01), "Ampere.StoragePro" 12 kW (2026-06) and 22 kW (2026-05), "Ampere.Offgrid"
  (2024-08), "Ampere.SolarPro", M-TEC Energy-Butler (2022-10), Enphase cooperation (2022-10); older pages and
  datasheets (Senec, VARTA) through the web archive.
- Trade press: pv magazine (2022-06-16, 2024-12-06), ess-news (2026-07-14), energie-experten.org ("SENEC");
  Wikipedia, "Energiekonzepte Deutschland".
- Owners' reports in Photovoltaikforum threads 148472, 222119, 222147, 227016, 235451 and 252527 (linked above).
- Projects, used for interfaces only: marq24/ha-senec-v3 and mchwalisz/pysenec (Apache-2.0), croedel/M-TEC-API (MIT),
  pyenphase/pyenphase (MIT), evcc-io/evcc templates (MIT).
- Code: `src/openampere/drivers/registry.py`, `src/openampere/drivers/foxess/driver.py`,
  `src/openampere/drivers/saj/registers.py`.
