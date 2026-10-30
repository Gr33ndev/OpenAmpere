# Previous EKD smartbox ("IQ box") and the EKD cloud

> **Not official information.** Compiled by the OpenAmpere contributors; can be incomplete or wrong. No affiliation
> with EKD or the manufacturers named here, not legal advice. See [Device knowledge](../README.md).

**Applies to:** the energy manager ("smartbox", "IQ box") installed with the storage systems sold by EKD, the EKD
cloud behind it, its customer API and the previous app · **Sold as:** part of "Ampere.StoragePro" and
"Ampere.StoragePro E3" systems · **OpenAmpere:** no driver, OpenAmpere never talks to the box; it detects the box's
effects (`control.py`, `diagnostics.py`, `drivers/modbus.py`) and imports the cloud history (`cloud_import.py`) ·
**Support:** – · **Confirmed on a real device:** partly

The name "IQ box" or "smartbox" is used here only to describe the device. App texts call it the "former smartbox"
without a manufacturer name.

## Summary

- While it is connected, the box fetches its target settings from the EKD cloud about every 100 seconds and writes
  them to the inverter. Changes made in OpenAmpere (backup reserve, work mode, grid charging, export limit) jump back.
- Owners stop this by blocking the box's internet access in the router, or by disconnecting the box if nothing else
  needs it (for example control by the grid operator).
- On a FoxESS H3 the box holds one of the very few Modbus TCP connections all the time.
- While it is powered, OpenAmpere cannot read the FoxESS AC EV Charger that came with the system ([#431](https://github.com/Gr33ndev/OpenAmpere/issues/431)).
- On a FoxESS H3 it uses the inverter's remote control with its own values (enable 5, timeout 30 s), which is how
  OpenAmpere tells it apart from its own commands (1 or 5 with 180 s; since OpenAmpere writes 5 as well, by the
  timeout, [#509](https://github.com/Gr33ndev/OpenAmpere/issues/509)).
- On an SAJ it writes the work mode and the reserve, and while charging from the grid an undocumented block at
  0x8300 every 1–3 s.
- The EKD cloud's customer API blocks clients that poll too often; OpenAmpere's history import sends at most one
  request per 65 s.
- No source shows that an inverter stops when the box is switched off for good; the box has no watchdog role in any
  document found. SAJ owners report their system running on without it; on a FoxESS H3 only short-term reports exist.
  The real risk is cutting the box off in the middle of one of its commands (see Behaviour).

## Registers

Registers the box writes, as far as known. OpenAmpere does not use the box's patterns.

| Register | Meaning | Type, scale, sign | R/W | Status | Source |
|---|---|---|---|---|---|
| FoxESS 46001 | Remote control, a bit field: bit 0 on, bits 3:2 target (00 AC, 01 battery, 10 grid, 11 AC with grid first); the box writes 5 = on with the battery as target | U16 bit field | W (by the box) | confirmed (values an owner read); that the box writes them: derived; bit field: documented | [#141](https://github.com/Gr33ndev/OpenAmpere/issues/141), [#142](https://github.com/Gr33ndev/OpenAmpere/issues/142); FoxESS Modbus interface definition V1.05.04.00, table 3-8 |
| FoxESS 46002 | Remote control timeout (watchdog); the box writes 30 | U16, s | W (by the box) | confirmed, as above | [#141](https://github.com/Gr33ndev/OpenAmpere/issues/141), [#142](https://github.com/Gr33ndev/OpenAmpere/issues/142) |
| FoxESS 46003–46004 | Remote control active power; the box puts its charge power here | I32, W | W (by the box) | confirmed, as above | [#141](https://github.com/Gr33ndev/OpenAmpere/issues/141), [#142](https://github.com/Gr33ndev/OpenAmpere/issues/142) |
| FoxESS 20162 | Unknown; the box writes 1 | – | W (by the box) | open (meaning unknown) | [#141](https://github.com/Gr33ndev/OpenAmpere/issues/141), [registers.md](../../registers.md) |
| SAJ 0x3644 | On-grid discharge depth (backup reserve), % SoC; set to 100 while charging, put back afterwards | U16, % | W (by the box) | derived | stanus74/home-assistant-saj-h2-modbus#18; [registers.md](../../registers.md); [#382](https://github.com/Gr33ndev/OpenAmpere/issues/382) |
| SAJ 0x3647 | Work mode (AppMode); set to 0 at the end of charging; also changes when 0x8300 is written | U16 | W (by the box) | derived | stanus74/home-assistant-saj-h2-modbus#18; [#382](https://github.com/Gr33ndev/OpenAmpere/issues/382), [#383](https://github.com/Gr33ndev/OpenAmpere/issues/383) |
| SAJ 0x8300–0x8306 | Undocumented charge block, written with FC16 while charging (see Behaviour); 0x8300 = 0 at the end | U16 each | W (by the box) | derived | stanus74/home-assistant-saj-h2-modbus#18 (recorded traffic); not in SAJ's H2 protocol ([#377](https://github.com/Gr33ndev/OpenAmpere/issues/377)) |

## Behaviour

### The box and the inverter

| Fact | Status | Source |
|---|---|---|
| The box fetches its target settings from the EKD cloud about every 100 s and writes them to the inverter, overwriting changes made by OpenAmpere (backup reserve, work mode, grid charging, export limit). Seen repeatedly in owners' questions. | confirmed | [#25](https://github.com/Gr33ndev/OpenAmpere/issues/25), [#53](https://github.com/Gr33ndev/OpenAmpere/issues/53), [#373](https://github.com/Gr33ndev/OpenAmpere/issues/373), [#374](https://github.com/Gr33ndev/OpenAmpere/issues/374) |
| Blocking the box's internet access in the router stops the resets: an owner kept a changed backup reserve and a switch to price-based grid charging. | confirmed | [#25](https://github.com/Gr33ndev/OpenAmpere/issues/25), [#53](https://github.com/Gr33ndev/OpenAmpere/issues/53) |
| With its internet access blocked, the box keeps reading along but gets no targets and no updates, and the manufacturer's app shows no current data. | derived | [#25](https://github.com/Gr33ndev/OpenAmpere/issues/25), [#53](https://github.com/Gr33ndev/OpenAmpere/issues/53) |
| On a FoxESS H3 the box keeps its own Modbus TCP connection open all the time and does not release it, so it can take one of the inverter's very few connection slots. OpenAmpere's "per poll" connection mode does not help against it. | confirmed | [#311](https://github.com/Gr33ndev/OpenAmpere/issues/311), [#312](https://github.com/Gr33ndev/OpenAmpere/issues/312) |
| The box is also configured as a Modbus TCP client of the FoxESS AC EV Charger ("Ampere.ChargePro Bridge", host `EVC-<serial>.local`, unit id 1, port 502, not configurable on its settings page). While the box is powered, OpenAmpere cannot read the charger; with the box cut from power the wallbox check reads it at once. Whether the charger allows only one client or mishandles concurrent clients, and whether the box also controls the charger, is open; see [ac-charger.md](../foxess/ac-charger.md). | configured as a client: derived; blocks access: confirmed (one device) | an owner's screenshot of the box's settings page, [#427](https://github.com/Gr33ndev/OpenAmpere/issues/427); owner's diagnostics report, [#431](https://github.com/Gr33ndev/OpenAmpere/issues/431) |
| Disconnecting the box from the network (its LAN cable or its power, not the inverter's) frees the slot. | confirmed | [#311](https://github.com/Gr33ndev/OpenAmpere/issues/311) |
| At one installation that routed everything through a Modbus proxy, the box set itself back to the inverter's own address after a while and took a slot again. A proxy is no lasting way to share the inverter with the box. | confirmed | [#311](https://github.com/Gr33ndev/OpenAmpere/issues/311), [#312](https://github.com/Gr33ndev/OpenAmpere/issues/312) |
| After the box is disconnected, the inverter keeps working with the settings stored last. SAJ: owners report the system running on with the box switched off (the battery went on discharging) or never having had a box. FoxESS H3: only reports of the first hours after cutting the box off, no long-term report. | confirmed (SAJ, owner reports), open (FoxESS long-term) | owner reports in [a Photovoltaikforum thread on bursts of discharge](https://www.photovoltaikforum.com/thread/244965-ekd-ampere-storage-pro-asp-10kw-3p-x-schubweise-entladung-ins-netz/), [page 11 of a Photovoltaikforum thread on the storage](https://www.photovoltaikforum.com/thread/222147-ekd-speicher-ampere-storage-pro-ein-chinamodell-und-weitere-fragen-dazu/?pageNo=11) and [a Home Assistant community thread](https://community.home-assistant.io/t/integration-of-ampere-iq-inverter-battery-and-kiwigrid-energy-manager/684978); [#311](https://github.com/Gr33ndev/OpenAmpere/issues/311), [#312](https://github.com/Gr33ndev/OpenAmpere/issues/312), [#469](https://github.com/Gr33ndev/OpenAmpere/issues/469) |
| Neither inverter has a documented function that stops or limits it when an external controller such as the box falls silent. Kiwigrid's installation guide for the Energy Manager RailX (version 014, 2025) mentions no watchdog, fallback or loss of communication for connected inverters or batteries. FoxESS's only mechanism is the timeout of the remote control (see below); SAJ has none (see [h2-family.md](../saj/h2-family.md)). | documented (none in the documents named) | Kiwigrid, installation guide Energy Manager RailX, version 014; FoxESS Modbus interface definition V1.05.04.00; SAJ H2 protocol |
| The grid meter is read by the inverter (its values are in the inverter's registers), not by the box, so self-consumption and the export limit do not depend on the box. FoxESS reports a lost meter as an alarm ("Meter Lost"); on the FoxESS H1 a lost CT switches the inverter off, for the H3 this is not documented. Whether any EKD installation wires the meter through the box is open. | derived | [a Home Assistant community thread](https://community.home-assistant.io/t/integration-of-ampere-iq-inverter-battery-and-kiwigrid-energy-manager/684978) (SAJ meter registers); FoxESS Modbus interface definition V1.05.04.00; FoxESS H1/AC1 G100 declaration |
| The export limit stays in force without the box: it is stored in the inverter and works with the meter the inverter reads itself (row above). No owner report shows it from a device. | derived | [foxess/h3.md](../foxess/h3.md), [saj/h2-family.md](../saj/h2-family.md); [#311](https://github.com/Gr33ndev/OpenAmpere/issues/311), [#312](https://github.com/Gr33ndev/OpenAmpere/issues/312) |
| FoxESS: per FoxESS's Modbus definition 46001 is a bit field (bit 0 on, bits 3:2 target: 00 AC, 01 battery, 10 grid, 11 AC with grid first). The box's 5 therefore means remote control on with the battery as target. The FoxESS definition does not say what happens when the timeout ends; foxess_modbus expects the remote control to end and the inverter to go back to its own work mode, so the box's 30 s timeout would end it soon after the box falls silent. If it stays on as in [#141](https://github.com/Gr33ndev/OpenAmpere/issues/141), the battery would stay at the box's last power (0 W or charging) while PV and the house supply go on. Not seen on a device. | documented (bit field), derived (behaviour) | FoxESS Modbus interface definition V1.05.04.00, table 3-8 (bit field); nathanmarlor/foxess_modbus (behaviour after the timeout); [foxess/h3.md](../foxess/h3.md#remote-control-grid-charging) |
| SAJ: when the box writes 0x8300 at the start of a charge, the work mode 0x3647 changes to 3 (passive mode) by itself; the box ends a charge with 0x8300 = 0 and 0x3647 = 0 written back to back. Cut off in the middle of a charge, the inverter may therefore keep passive mode and the reserve 0x3644 at 100 %, and the battery would not discharge until someone changes them. | derived | stanus74/home-assistant-saj-h2-modbus#18 (recorded traffic); [#377](https://github.com/Gr33ndev/OpenAmpere/issues/377) |
| The box may be needed for something else, for example control by the grid operator (§ 14a EnWG for a controllable device such as a wallbox or heat pump, § 9 EEG for feed-in). It can only do that where a smart meter gateway with a control box at the meter exists and is connected to the box (a cable on its inputs, or EEBus in the home network). No installation has confirmed it either way; owners check it with the steps in the README before disconnecting the box. | open (depends on the installation) | [#25](https://github.com/Gr33ndev/OpenAmpere/issues/25), [#311](https://github.com/Gr33ndev/OpenAmpere/issues/311), [#452](https://github.com/Gr33ndev/OpenAmpere/issues/452) |
| FoxESS: the box drives the remote control with 46001 = 5, 46002 = 30 s, its charge power in 46003–46004, and also writes 20162 = 1. | confirmed (owner's report) | [#141](https://github.com/Gr33ndev/OpenAmpere/issues/141), [#142](https://github.com/Gr33ndev/OpenAmpere/issues/142) |
| FoxESS: a remote control that is on with values other than OpenAmpere's usually means the box is following targets from its cloud; it can overwrite grid charging and battery settings. | derived | [#126](https://github.com/Gr33ndev/OpenAmpere/issues/126), [#127](https://github.com/Gr33ndev/OpenAmpere/issues/127), [#136](https://github.com/Gr33ndev/OpenAmpere/issues/136), [#142](https://github.com/Gr33ndev/OpenAmpere/issues/142) |
| SAJ: the box writes the work mode 0x3647 and the on-grid reserve 0x3644. It can switch an SAJ away from time-of-use mode while OpenAmpere charges through a charge slot. | derived | stanus74/home-assistant-saj-h2-modbus#18; [#382](https://github.com/Gr33ndev/OpenAmpere/issues/382), [#383](https://github.com/Gr33ndev/OpenAmpere/issues/383) |
| SAJ, grid charging by the box: it sets 0x3644 to 100, then writes 0x8300–0x8306 with FC16. From the grid only, every 3 s, with values in the pattern 3, 2, about the charge power, …, 600, 10000, 10000. With PV, 0x8300–0x8304 every second. At the end it writes 0x8300 = 0 and 0x3647 = 0 and puts 0x3644 back. | derived | stanus74/home-assistant-saj-h2-modbus#18 (recorded traffic); research in [#377](https://github.com/Gr33ndev/OpenAmpere/issues/377) |

### The EKD cloud and the previous app

| Fact | Status | Source |
|---|---|---|
| The EKD cloud (behind the Ampere.IQ app) has a customer API that EKD documented for customers at developer.ekd-solar.de; access uses a personal key from the owner's app. | documented | EKD's customer API documentation; [README](../../../README.md) |
| The customer API blocks clients that poll too often. Requests carry a fixed client type value (`de.ekd.customer.apiclient`) that the API requires. | documented | EKD's customer API documentation; `cloud_import.py` |
| The history can be fetched in two steps: energy values in quarter hours, then the battery state of charge per day. | confirmed | [#17](https://github.com/Gr33ndev/OpenAmpere/issues/17), [#42](https://github.com/Gr33ndev/OpenAmpere/issues/42) |
| The previous app apparently calculated house consumption as the balance of the other energy flows, so its value differed from the inverter's own consumption counter. | derived | [#15](https://github.com/Gr33ndev/OpenAmpere/issues/15), [#36](https://github.com/Gr33ndev/OpenAmpere/issues/36); see Open questions |
| The previous app offered three grid charging levels at 50, 75 and 100 % of the charge power the battery's datasheet permits. | derived | [#23](https://github.com/Gr33ndev/OpenAmpere/issues/23), [#49](https://github.com/Gr33ndev/OpenAmpere/issues/49) |
| In September 2025 EKD switched off the storage function of the older "AMPERE.Storage" (SolarMax-based, neither SAJ nor FoxESS) remotely, announced one day before and citing a regulatory component to be retrofitted. This has nothing to do with switching the box off, but may be where the story "the system stops by itself" comes from. | confirmed (owner reports), derived (link to the rumour) | owner reports in [a Photovoltaikforum thread on the shutdown](https://www.photovoltaikforum.com/thread/252527-ekd-zwangsabschaltung-ampere-storage-zum-18-09-2025-ohne-vorherige-kundenbeteili/) |

## Decisions in OpenAmpere

| Decision | Why | Issue/PR |
|---|---|---|
| Re-read written battery settings and the export limit 45 s and again 150 s after a write (`VERIFY_AFTER_S`). An overwrite is logged, the app shows a dialog on every page that names the old smartbox as the usual cause with the remedies, and a notification "Einstellung überschrieben" can be sent. | The box's cycle is about 100 s, so the last check comes after more than one round. | [#53](https://github.com/Gr33ndev/OpenAmpere/issues/53), [#373](https://github.com/Gr33ndev/OpenAmpere/issues/373), [#374](https://github.com/Gr33ndev/OpenAmpere/issues/374) |
| Remedies offered: block the box's internet access in the router, or disconnect it (LAN cable or power) only if nothing else needs it, then check the export limit shown in OpenAmpere. OpenAmpere only gives the hint; it writes nothing for this. | The inverter keeps the values stored last; the box may serve the grid operator. | [#53](https://github.com/Gr33ndev/OpenAmpere/issues/53), [#311](https://github.com/Gr33ndev/OpenAmpere/issues/311), [#312](https://github.com/Gr33ndev/OpenAmpere/issues/312) |
| Texts say "former smartbox" without the manufacturer's name. | Names only descriptively. | [#53](https://github.com/Gr33ndev/OpenAmpere/issues/53) |
| When the inverter is busy, the message names other programs first and the IQ box second; a Modbus proxy is mentioned only as an option for advanced users. | Several reported cases were other programs, not the box; the box can undo a proxy setup. | [#311](https://github.com/Gr33ndev/OpenAmpere/issues/311), [#312](https://github.com/Gr33ndev/OpenAmpere/issues/312) |
| FoxESS: any remote control values other than OpenAmpere's own (1 or 5 with 180 s) count as another device; OpenAmpere never writes to or releases them. | Release only what OpenAmpere started. | [#141](https://github.com/Gr33ndev/OpenAmpere/issues/141), [#142](https://github.com/Gr33ndev/OpenAmpere/issues/142), [#509](https://github.com/Gr33ndev/OpenAmpere/issues/509) |
| FoxESS: the diagnostics show a warning when another device holds the remote control, explain the usual cause (the box) and what the owner can do. | OpenAmpere cannot change this state itself; the owner can. | [#126](https://github.com/Gr33ndev/OpenAmpere/issues/126), [#127](https://github.com/Gr33ndev/OpenAmpere/issues/127) |
| SAJ: while charging, OpenAmpere sets its own work mode and charge slot again with every command and keeps the owner's values from the start of the session. | Another device such as the box may change them meanwhile. | [#382](https://github.com/Gr33ndev/OpenAmpere/issues/382), [#383](https://github.com/Gr33ndev/OpenAmpere/issues/383) |
| SAJ: the undocumented block 0x8300–0x8306 is not used; grid charging uses a charge slot that ends by itself. | Not in SAJ's protocol; nobody has tested whether the block ends by itself. | [#377](https://github.com/Gr33ndev/OpenAmpere/issues/377), [#378](https://github.com/Gr33ndev/OpenAmpere/issues/378) |
| Cloud import: at most one request per 65 s, in the background, surviving restarts and resuming where it stopped. Imported rows never overwrite quarter hours OpenAmpere recorded itself. | The API blocks clients that poll too often; local measurements are better. | [#17](https://github.com/Gr33ndev/OpenAmpere/issues/17), [#42](https://github.com/Gr33ndev/OpenAmpere/issues/42) |
| Import of an exported ZIP of the cloud data: entries that are a list instead of an object, days with missing fields, damaged entries, non-finite values, negative energy and SoC outside 0–100 % are skipped; uploads up to 500 MB, streamed to disk. | Found in reviews, to keep a broken file from breaking the import. | [#223](https://github.com/Gr33ndev/OpenAmpere/issues/223), [#230](https://github.com/Gr33ndev/OpenAmpere/issues/230), [#287](https://github.com/Gr33ndev/OpenAmpere/issues/287), [#288](https://github.com/Gr33ndev/OpenAmpere/issues/288) |
| Texts never claim that the EKD cloud will be switched off; they say OpenAmpere keeps working without it. | It is not known; OpenAmpere does not depend on the cloud. | [#84](https://github.com/Gr33ndev/OpenAmpere/issues/84), [#85](https://github.com/Gr33ndev/OpenAmpere/issues/85) |

## Open questions

- What FoxESS register 20162, written as 1 by the box, means ([#141](https://github.com/Gr33ndev/OpenAmpere/issues/141)).
- Whether the SAJ block 0x8300–0x8306 ends by itself when the writes stop; 0x8304 = 600 might be a timeout in seconds
  (assumption, stanus74/home-assistant-saj-h2-modbus#18, [#377](https://github.com/Gr33ndev/OpenAmpere/issues/377)).
- Whether the inverter keeps exactly the last settings after the box is disconnected; no owner has reported it yet
  ([#312](https://github.com/Gr33ndev/OpenAmpere/issues/312)).
- Conflict about the consumption value: [#15](https://github.com/Gr33ndev/OpenAmpere/issues/15) says the previous app's
  balance was higher than the inverter's consumption counter, while
  [#346](https://github.com/Gr33ndev/OpenAmpere/issues/346) found the inverter's counter about 50 % higher than the
  balance, which the manufacturer's app showed. Possibly different devices or apps; see [modbus.md](modbus.md).
- Whether the box acts as the energy manager (HEMS) for the grid operator: it would get "reduce power" from the smart
  meter gateway through a control box at the meter, confirm it and limit the system until the end of the reduction.
  Public sources describe this path (the signal goes from the gateway's CLS channel to a control box and from there to
  an HEMS or straight to the device, today mostly through relay contacts, a digital interface such as EEBus or Modbus
  as a next step), but nothing shows that the box does it. Kiwigrid presents taking these signals from the gateway as
  new with its RailX+ (2026), which makes it less likely for the older box (derived). Hypothesis, status open
  ([#452](https://github.com/Gr33ndev/OpenAmpere/issues/452)).
- Whether a FoxESS H3 runs normally for weeks without the box, and whether the box's remote control (5/30 s) ends by
  itself when the box is cut off in the middle of a command.
- Whether any EKD installation wires the grid meter through the box instead of to the inverter.
- Whether and when the EKD cloud will be switched off ([#84](https://github.com/Gr33ndev/OpenAmpere/issues/84)).
- The ZIP edge cases above come from reviews, not from a real export ([#288](https://github.com/Gr33ndev/OpenAmpere/issues/288)).
- Whether the box only reads the FoxESS AC EV Charger or also controls it, and why the charger cannot be read while
  the box is powered (only one client, or concurrent clients mishandled;
  [#431](https://github.com/Gr33ndev/OpenAmpere/issues/431)).
- Who made the box is not stated in OpenAmpere's sources.

## Sources

- stanus74/home-assistant-saj-h2-modbus (MIT), issue stanus74/home-assistant-saj-h2-modbus#18: recorded Modbus
  traffic of the box on an SAJ; credited in [NOTICE](../../../NOTICE).
- On control by the grid operator: [a public video of a German grid operator (2025)](https://www.youtube.com/watch?v=bs27GahT8Sg) on how the § 14a signal gets
  from the smart meter gateway to an energy manager or the device, and the public fact sheets of the German Energy
  Agency (dena, 2025) on the CLS unit and on energy managers; summarised in own words
  ([#452](https://github.com/Gr33ndev/OpenAmpere/issues/452)).
- Kiwigrid, [installation guide Energy Manager RailX](https://kiwigrid.com/docs/kiwigrid/de/OBJ_DCL-0000000142-014-_DE__WEBAPPHTML5/index.html)
  (version 014, 2025), and [pv magazine on the RailX+ (2026)](https://www.pv-magazine.de/2026/02/09/kiwigrid-integriert-steuerbox-und-energiemanagement-in-einem-geraet/).
- FoxESS, ["FOX commercial inverter Modbus interface definition" V1.05.04.00](https://www.foxesscommunity.com/download/file.php?id=3478)
  (2025), table 3-8; FoxESS, ["H1 and AC1 series inverter G100 Declaration"](https://portal.segen.co.uk/reseller/docs/H1-and-AC1-series-inverter-G100_Declaration-20210414.pdf) (2021-04-14, published by a UK distributor) (behaviour on a lost CT, other models).
- SAJ, "H2-3~6K-S2 single phase communication protocol" (2022), the H2 protocol named on
  [saj/h2-family.md](../saj/h2-family.md#sources).
- Owner reports in public threads of the Photovoltaikforum and the Home Assistant community, linked in the tables;
  summarised in own words, without personal data.
- EKD's customer API documentation (developer.ekd-solar.de), as used by the cloud import. Nothing from the Ampere.IQ
  app or its code.
- Issues and pull requests: [#15](https://github.com/Gr33ndev/OpenAmpere/issues/15),
  [#17](https://github.com/Gr33ndev/OpenAmpere/issues/17), [#23](https://github.com/Gr33ndev/OpenAmpere/issues/23),
  [#25](https://github.com/Gr33ndev/OpenAmpere/issues/25), [#36](https://github.com/Gr33ndev/OpenAmpere/issues/36),
  [#42](https://github.com/Gr33ndev/OpenAmpere/issues/42), [#49](https://github.com/Gr33ndev/OpenAmpere/issues/49),
  [#53](https://github.com/Gr33ndev/OpenAmpere/issues/53), [#84](https://github.com/Gr33ndev/OpenAmpere/issues/84),
  [#85](https://github.com/Gr33ndev/OpenAmpere/issues/85), [#126](https://github.com/Gr33ndev/OpenAmpere/issues/126),
  [#127](https://github.com/Gr33ndev/OpenAmpere/issues/127), [#136](https://github.com/Gr33ndev/OpenAmpere/issues/136),
  [#141](https://github.com/Gr33ndev/OpenAmpere/issues/141), [#142](https://github.com/Gr33ndev/OpenAmpere/issues/142),
  [#223](https://github.com/Gr33ndev/OpenAmpere/issues/223), [#230](https://github.com/Gr33ndev/OpenAmpere/issues/230),
  [#287](https://github.com/Gr33ndev/OpenAmpere/issues/287), [#288](https://github.com/Gr33ndev/OpenAmpere/issues/288),
  [#311](https://github.com/Gr33ndev/OpenAmpere/issues/311), [#312](https://github.com/Gr33ndev/OpenAmpere/issues/312),
  [#346](https://github.com/Gr33ndev/OpenAmpere/issues/346), [#373](https://github.com/Gr33ndev/OpenAmpere/issues/373),
  [#374](https://github.com/Gr33ndev/OpenAmpere/issues/374), [#377](https://github.com/Gr33ndev/OpenAmpere/issues/377),
  [#378](https://github.com/Gr33ndev/OpenAmpere/issues/378), [#382](https://github.com/Gr33ndev/OpenAmpere/issues/382),
  [#383](https://github.com/Gr33ndev/OpenAmpere/issues/383), [#427](https://github.com/Gr33ndev/OpenAmpere/issues/427),
  [#431](https://github.com/Gr33ndev/OpenAmpere/issues/431); an owner's diagnostics report from the wallbox check (not
  linked).
- Device pages: [foxess/h3.md](../foxess/h3.md), [saj/h2-family.md](../saj/h2-family.md).
- Code: `src/openampere/control.py`, `src/openampere/diagnostics.py`, `src/openampere/drivers/modbus.py`,
  `src/openampere/drivers/foxess/driver.py`, `src/openampere/drivers/saj/driver.py`, `src/openampere/cloud_import.py`.
