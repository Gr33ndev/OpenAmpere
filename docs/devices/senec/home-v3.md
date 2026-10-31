# Senec.Home V3 and V2.1

> Not official manufacturer information: compiled by the OpenAmpere contributors, can be incomplete or wrong. No
> affiliation with Senec or EKD; not legal advice. See the [device knowledge README](../README.md).

**Applies to:** Senec.Home V3 hybrid, V3 hybrid duo (also LFP) and V2.1 with their local web interface · **Sold as:**
Senec storage systems (EKD sold them until about 2022) · **OpenAmpere:** driver `senec`
(`src/openampere/drivers/senec/`), "register map" `senec_lala` · **Support:** display only · **Confirmed on a real
device:** no (built from open projects and the simulator)

## Summary

- **Not Modbus:** the device has a local web interface, `lala.cgi`. A POST of JSON `{"SECTION": {"KEY": ""}}` returns the
  same JSON with the values filled in as typed hex strings.
- HTTPS with a **self-signed certificate** since about firmware 825 (2023), plain HTTP before and on the V2.1.
- The owner chooses "Senec" in the setup (advanced settings); detection over Modbus and the network scan do not find it.
- **No energy history:** firmware 825 emptied the statistics section, so there are no lifetime counters.
- Asking too often is said to restart the device or stop its cloud sync: OpenAmpere asks at most every 30 s.
- Senec V4, P4 and E4 have no local web interface and are out of scope.

## Values

Section `ENERGY` of `lala.cgi`, plus `FACTORY` for the identification. "Registers" here are JSON keys.

| Key | Meaning | Type, sign | R/W | Status | Source |
|---|---|---|---|---|---|
| `ENERGY.GUI_INVERTER_POWER` | PV power (with an external PV inverter the device measures, not always reliably) | float, W | R | derived | marq24/ha-senec-v3; evcc `senec-home` |
| `ENERGY.GUI_BAT_DATA_POWER` | Battery power, **+ = charging** | float, W | R | derived (evcc scales it by −1) | evcc `senec-home`; marq24/ha-senec-v3 |
| `ENERGY.GUI_GRID_POW` | Grid power, + = import | float, W | R | derived | evcc `senec-home`; marq24/ha-senec-v3 |
| `ENERGY.GUI_HOUSE_POW` | House consumption | float, W | R | derived | marq24/ha-senec-v3 |
| `ENERGY.GUI_BAT_DATA_FUEL_CHARGE` | State of charge | float, % | R | derived | evcc `senec-home`; marq24/ha-senec-v3 |
| `ENERGY.GUI_BAT_DATA_VOLTAGE`, `…_CURRENT` | Battery voltage and current | float, V, A | R | derived | marq24/ha-senec-v3 |
| `ENERGY.STAT_STATE` | System state (enumeration) | integer | R | derived; meanings not taken over | marq24/ha-senec-v3 |
| `FACTORY.SYS_TYPE` | System type: 15 V2.1, 17/19 V3 hybrid, 18 V3 hybrid duo, 20 V3 hybrid LFP, 21 V3 hybrid duo LFP | integer | R | derived | marq24/ha-senec-v3 `pysenec_ha/constants.py` |
| `FACTORY.DEVICE_ID` | Device id (treated like a serial number) | integer | R | derived | marq24/ha-senec-v3 |
| `ENERGY.SAFE_CHARGE_FORCE`, `…_PROHIBIT` | Force charging from the grid, back to normal; no watchdog known | integer | W | derived; not used by OpenAmpere | evcc `senec-home`; marq24/ha-senec-v3 |

Value types: `fl_` IEEE-754 float as 8 hex digits (big-endian), `u…_` unsigned and `i…_` signed integers in hex, `st_`
text. Something else (e.g. `VARIABLE_NOT_FOUND`) is no value.

## Behaviour

| Fact | Status | Source |
|---|---|---|
| Since about NPU 2411 a first request for `FACTORY` (`SYS_TYPE`, `COUNTRY`, `DEVICE_ID`) gives the session cookie the device wants, as its own web page does. | derived | marq24/ha-senec-v3 (`_init_gui_cookies`) |
| What a request with the session cookie from before a restart of the device gets is not known (an error answer or empty values); OpenAmpere's simulator answers it with 403. | open | assumption for the simulator; marq24/ha-senec-v3 (cookie since NPU 2411) |
| The device's web page splits requests into parts below 600 characters with about 200 ms between them. | derived | marq24/ha-senec-v3 (`_request_senec_lala_chunked`, 2026-08) |
| Firmware 825–827 (2023-08) emptied `STATISTIC`: no energy counters any more. | derived | marq24/ha-senec-v3#4 |
| Polling too often can restart the device or stop its cloud sync; ioBroker polls every 10 s, Home Assistant every 30 s (V3) or 60 s (V2); two clients at the same time were linked to repeated errors. | derived | nobl/ioBroker.senec (MIT); marq24/ha-senec-v3#235 (2026-09) |
| The Senec app and mein-senec.de work only through Senec's cloud; firmware updates come automatically. | documented | Senec datasheets V2.1 (TD220-020.28, 2023-06) and V3 hybrid (TD220-022.13, 2019-12) |
| A separate PV inverter (e.g. a Fronius Symo with Datamanager 2.0) is coupled to the Senec over RS485 ("Solar Net") or measured with extra meters; its own Solar API stays readable. | documented | Senec list of compatible inverters (TD130-002.14, 2016-07); Senec installation manual V3; Fronius Solar API V1 |
| A local "SENEC OpenAPI" on V3 (TCP 4424) needs the owner's approval in mein-senec.de, so the cloud. | derived, open | marq24/ha-senec-v3#241 (2026-10) |

## Decisions in OpenAmpere

| Decision | Why | Issue/PR |
|---|---|---|
| Display only: every write method refuses, `supports_control` is off. | Nothing confirmed on a device; the known writes have no watchdog. | [#521](https://github.com/Gr33ndev/OpenAmpere/issues/521) |
| Only when the owner chooses "Senec"; port 502 (the setup's default) stands for 443 and then 80, HTTPS before HTTP. Its error messages name the web interface, not Modbus. | It does not answer on the Modbus port, so the other drivers' detection would stop there. | [#521](https://github.com/Gr33ndev/OpenAmpere/issues/521) |
| The certificate is not checked, and not pinned after the first contact either (as #521 first planned). | It is self-signed with nothing to check it against. A pinned certificate would lock the owner out after every firmware update or reset that brings a new one, with no way to pair again in the app; and what an attacker in the home network could gain is only wrong values on the display: OpenAmpere only reads and sends nothing secret. | [#521](https://github.com/Gr33ndev/OpenAmpere/issues/521) |
| Asked at most every 30 s (`MIN_POLL_S`), also after an error (no quick retries; the reconnect waits at least as long), one request at a time, below 600 characters, the session cookie kept until a reconnect. | What the open projects found the device stands. | [#521](https://github.com/Gr33ndev/OpenAmpere/issues/521) |
| Error answers of the device (403, 404) are closed at once. | Unclosed, they hold a connection to the device until the error object is collected. | [#540](https://github.com/Gr33ndev/OpenAmpere/issues/540) |
| No counters: no energy history for this device. | The device has none since firmware 825; integrating the power is a follow-up. | [#521](https://github.com/Gr33ndev/OpenAmpere/issues/521) |
| The diagnostics show the `ENERGY` values of the last read with Senec's own signs (powers rounded) and how the device answered (scheme, port), not the device id; the report sends no request of its own. | So the first owner's report can confirm the signs. | [#521](https://github.com/Gr33ndev/OpenAmpere/issues/521) |
| A redirect of the web interface (301/302/303) is not followed; it counts as an error answer and is closed at once: during the detection the device answers but is no Senec, while reading it is not answering. | Requests stay at the address the owner set, like every other outgoing request (`outbound.py`). | [#548](https://github.com/Gr33ndev/OpenAmpere/issues/548) |
| No search for a new address when the device is unreachable, and the device query (Mehr → Geräteabfrage) does not read it: it says so. | Both read Modbus registers only; this device has none. A fixed IP address in the router keeps it reachable. | [#547](https://github.com/Gr33ndev/OpenAmpere/issues/547) |

## Open questions

- Everything: no owner has tried it yet. Whether a V2.1 answers on HTTP or HTTPS, and whether newer firmware still
  answers `lala.cgi` at all.
- The signs of `GUI_BAT_DATA_POWER` and `GUI_GRID_POW` on a real device.
- Which polling interval is safe next to the Senec cloud sync.
- What `STAT_STATE` reports in a power cut (for the outage detection).

## Sources

- marq24/ha-senec-v3 (Apache-2.0) and mchwalisz/pysenec (Apache-2.0): request format, value types, keys, system types,
  cookie and request size. Facts only, see NOTICE.
- evcc-io/evcc, template `senec-home` (MIT): keys and signs.
- nobl/ioBroker.senec (MIT): polling intervals.
- Senec datasheets and installation manual, Fronius Solar API V1: as cited above.
- Research comment in [#521](https://github.com/Gr33ndev/OpenAmpere/issues/521).
