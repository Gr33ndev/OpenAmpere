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
  OpenAmpere tells it apart from its own commands (1 and 180 s).
- On an SAJ it writes the work mode and the reserve, and while charging from the grid an undocumented block at
  0x8300 every 1–3 s.
- The EKD cloud's customer API blocks clients that poll too often; OpenAmpere's history import sends at most one
  request per 65 s.

## Registers

Registers the box writes, as far as known. OpenAmpere does not use the box's patterns.

| Register | Meaning | Type, scale, sign | R/W | Status | Source |
|---|---|---|---|---|---|
| FoxESS 46001 | Remote control on/off; the box writes 5 | U16 | W (by the box) | confirmed (values an owner read); that the box writes them: derived | [#141](https://github.com/Gr33ndev/OpenAmpere/issues/141), [#142](https://github.com/Gr33ndev/OpenAmpere/issues/142) |
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
| After the box is disconnected, the inverter keeps working with the settings stored last (including the export limit). | open (stated in the guide, not reported from a device) | [#311](https://github.com/Gr33ndev/OpenAmpere/issues/311), [#312](https://github.com/Gr33ndev/OpenAmpere/issues/312) |
| The box may be needed for something else, for example control by the grid operator. | open (depends on the installation) | [#25](https://github.com/Gr33ndev/OpenAmpere/issues/25), [#311](https://github.com/Gr33ndev/OpenAmpere/issues/311) |
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

## Decisions in OpenAmpere

| Decision | Why | Issue/PR |
|---|---|---|
| Re-read written battery settings and the export limit 45 s and again 150 s after a write (`VERIFY_AFTER_S`). An overwrite is logged, the app shows a dialog on every page that names the old smartbox as the usual cause with the remedies, and a notification "Einstellung überschrieben" can be sent. | The box's cycle is about 100 s, so the last check comes after more than one round. | [#53](https://github.com/Gr33ndev/OpenAmpere/issues/53), [#373](https://github.com/Gr33ndev/OpenAmpere/issues/373), [#374](https://github.com/Gr33ndev/OpenAmpere/issues/374) |
| Remedies offered: block the box's internet access in the router, or disconnect it (LAN cable or power) only if nothing else needs it, then check the export limit shown in OpenAmpere. OpenAmpere only gives the hint; it writes nothing for this. | The inverter keeps the values stored last; the box may serve the grid operator. | [#53](https://github.com/Gr33ndev/OpenAmpere/issues/53), [#311](https://github.com/Gr33ndev/OpenAmpere/issues/311), [#312](https://github.com/Gr33ndev/OpenAmpere/issues/312) |
| Texts say "former smartbox" without the manufacturer's name. | Names only descriptively. | [#53](https://github.com/Gr33ndev/OpenAmpere/issues/53) |
| When the inverter is busy, the message names other programs first and the IQ box second; a Modbus proxy is mentioned only as an option for advanced users. | Several reported cases were other programs, not the box; the box can undo a proxy setup. | [#311](https://github.com/Gr33ndev/OpenAmpere/issues/311), [#312](https://github.com/Gr33ndev/OpenAmpere/issues/312) |
| FoxESS: any remote control values other than OpenAmpere's own (1 and 180 s) count as another device; OpenAmpere never writes to or releases them. | Release only what OpenAmpere started. | [#141](https://github.com/Gr33ndev/OpenAmpere/issues/141), [#142](https://github.com/Gr33ndev/OpenAmpere/issues/142) |
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
- Whether and when the EKD cloud will be switched off ([#84](https://github.com/Gr33ndev/OpenAmpere/issues/84)).
- The ZIP edge cases above come from reviews, not from a real export ([#288](https://github.com/Gr33ndev/OpenAmpere/issues/288)).
- Whether the box only reads the FoxESS AC EV Charger or also controls it, and why the charger cannot be read while
  the box is powered (only one client, or concurrent clients mishandled;
  [#431](https://github.com/Gr33ndev/OpenAmpere/issues/431)).
- Who made the box is not stated in OpenAmpere's sources.

## Sources

- stanus74/home-assistant-saj-h2-modbus (MIT), issue stanus74/home-assistant-saj-h2-modbus#18: recorded Modbus
  traffic of the box on an SAJ; credited in [NOTICE](../../../NOTICE).
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
