# go-e Charger

> Not official manufacturer information: compiled by the OpenAmpere contributors, can be incomplete or wrong. No
> affiliation with go-e or EKD; not legal advice. See the [device knowledge README](../README.md).

**Applies to:** go-e Charger wallboxes (HOME, Gemini and later; older hardware only through API v1) · **Sold as:** go-e
Charger (not sold by EKD) · **OpenAmpere:** wallbox module `goe` (`src/openampere/wallbox/goe.py`) · **Support:**
display only (live view, found by the search in the wallbox setup) · **Confirmed on a real device:** partly (found by the
search and shown in the live view; the values while a car charges not yet)

## Summary

- The go-e Charger has a local HTTP API on port 80, without a login. It is **off until the owner switches it on in
  the go-e app**: API v2 on newer hardware, API v1 on all hardware (the only one of the oldest).
- OpenAmpere asks `GET /api/status` with a filter (API v2) and, if that gives no go-e answer, `GET /status` (API v1).
  It only sends these two GET requests: no `/api/set`, no `/mqtt`, so it never changes the charger.
- It shows the state, the power, current and voltage per phase, the energy and (API v2) the duration of the charge,
  the current offered to the car and the highest current. Only the mode "manual".
- Modbus TCP, MQTT and OCPP exist as well, but are also off by default; OpenAmpere does not use them yet.
- Control (solar charging) is a separate step with its own issue: it needs the safety rules for writing, and what
  the charger does when OpenAmpere is gone has to be settled first.

## Keys read

API v2: `GET http://<address>/api/status?filter=car,nrg,wh,amp,ama,fwv,cdi`. API v1: `GET http://<address>/status`
(the whole status, values as strings).

| Key | Meaning | Unit v2 | Unit v1 | Status | Source |
|---|---|---|---|---|---|
| `car` | 1 no car, 2 charging, 3 waiting for the car, 4 charge finished (car still connected); v2 also 0 unknown and 5 error | – | – | documented | go-e API v1 and v2 descriptions |
| `nrg` 0–2 | Voltage L1–L3 | V | V | documented (v1); derived (v2 unit) | go-e API descriptions; evcc `charger/go-e/types2.go` |
| `nrg` 4–6 | Current L1–L3 | A | 0.1 A | documented (v1); derived (v2 unit) | go-e API descriptions; evcc `charger/go-e/types2.go` |
| `nrg` 11 | Total power | W | 0.01 kW | documented (v1); derived (v2 unit) | go-e API descriptions; evcc `charger/go-e/types2.go` |
| `wh` | Energy since the car was connected | Wh | – | documented | go-e API v2 description |
| `dws` | Energy since the car was connected | – | 10 Ws | documented | go-e API v1 description; cathiele/goecharger |
| `cdi` | Duration of the charge: `{"type": 1, "value": ms}`, type 0 without a duration | ms | – | documented (keys); open (behaviour) | go-e API v2 description |
| `amp` | Current offered to the car | A | A | documented | go-e API descriptions |
| `ama` | Highest current set in the charger | A | – | documented | go-e API v2 description |
| `fwv` | Firmware version | text | text | documented | go-e API descriptions |
| `eto` | Total energy | Wh | 0.1 kWh | documented; **not read** (an exact counter identifies the owner) | go-e API descriptions |

## Behaviour

| Fact | Status | Source |
|---|---|---|
| The local HTTP API v2 has to be switched on in the go-e app; where the switch is depends on the app version (named "Internet → Advanced settings" or "Settings → Connection → API settings" in other projects). | documented (switch); open (menu) | go-e API v2 description; README of a Homebridge plugin; an installer's guide (2025) |
| The local HTTP API v1 has to be switched on in the app too. | documented | go-e API v1 description |
| No login on the local network. | documented | go-e API v1 and v2 descriptions |
| `filter` in the query (comma separated) works from firmware 051.4; go-e advises to read the whole status only rarely. | documented | go-e API v2 description |
| Hardware generations by the name they report over OCPP: V3 "go-eCharger" (HOME), V4 "go-eCharger_V4" (Gemini), V5 "go-eCharger_V5" (Gemini 2.0), "go-eCharger_Pro". Which generation HOME fix and Gemini flex are is open. | documented (names); open (variants) | go-e OCPP description |
| API v2 exists from hardware V3 (HOME) on; the older hardware V2 has only API v1 (firmware 033 and later). | documented (v2 for V3 and newer); derived (V2 only v1) | go-e API v1 and v2 descriptions; a forum post of a Homey developer |
| The charger comes in an 11 kW and a 22 kW variant (API v2 key `var`): at most 22 kW, which is why OpenAmpere takes a total above 30 kW as a wrong unit. | documented | go-e API v2 description |
| API v1: go-e recommends at least 5 s between two requests; no rate limit on the local network. OpenAmpere reads every 10 s. | documented | go-e API v1 description |
| How many HTTP clients may read at the same time is not documented. HTTP holds no connection between requests, so evcc and OpenAmpere reading the same charger should work. | open | – |
| evcc tells the APIs apart the same way: API v2 if `/api/status` answers, otherwise API v1. | derived | evcc documentation and `charger/go-e/` (MIT) |
| Modbus TCP: off by default, switched on in the app (needs a restart of the charger), port 502, unit id 1, firmware 040 and newer; the unit of the total power differs between go-e's two descriptions (0.01 kW and 0.01 W). | documented; open (unit) | go-e Modbus descriptions (v1 and v2) |
| V4 announces itself over mDNS as `_go-e_go-eCharger_V4._tcp` and `_http._tcp`, host name `go-echarger_<serial>`. Not used: the search asks the HTTP API instead, which also works in the Home Assistant app. | derived | a forum post of a Homey developer |
| On a real charger with OpenAmpere 0.41.0 (Docker), the search in the wallbox setup found the charger and the live view showed it without problems. Whether a car was charging, and so whether power, current and voltage are right, is not recorded. | confirmed (found and shown), open (values while charging) | owner report by e-mail, summarised in [#616](https://github.com/Gr33ndev/OpenAmpere/issues/616) |

## Decisions in OpenAmpere

| Decision | Why | Issue/PR |
|---|---|---|
| HTTP API (v2, then v1), not Modbus TCP. | Works on all hardware, needs no restart of the charger, is what owners already switch on for evcc and other tools, and uses only the Python standard library (no new dependency). | [#597](https://github.com/Gr33ndev/OpenAmpere/issues/597) |
| Display only: only GET of the status, only the mode "manual"; its power counts in the house load like any wallbox. | Nothing confirmed on a real device; control needs its own safety rules first. | [#597](https://github.com/Gr33ndev/OpenAmpere/issues/597) |
| The search in the setup tries port 80 of every address in the local networks and keeps those whose status answer looks like a go-e (keys `car` and `nrg`). | No multicast needed, so it also works in the Home Assistant app; a device without the API switched on is not found, and the app says to switch it on. | [#597](https://github.com/Gr33ndev/OpenAmpere/issues/597) |
| A voltage above 400 V, a current above 80 A, a power above 30 kW or a charge above 200 kWh is not shown; with such a power the state is unknown. | A wrong unit would give such values: better no value than a wrong one. | [#597](https://github.com/Gr33ndev/OpenAmpere/issues/597) |
| The total energy (`eto`) and the serial number (`sse`) are not asked for. | Not needed for the live view; an exact counter or serial identifies the owner. | [#597](https://github.com/Gr33ndev/OpenAmpere/issues/597) |
| No redirect is followed and no proxy used. | The charger is in the home network. | [#597](https://github.com/Gr33ndev/OpenAmpere/issues/597) |

## Open questions

- The units of API v2 (`nrg` in V, A, W) on a real charger, and the values while it charges on one phase.
- Whether a charge with "waiting for the car" (`car` 3) should show as waiting rather than plugged in.
- What `cdi` holds after a charge ends, and whether `wh` keeps the last charge after the car is unplugged.
- What firmware before 051.4 answers to the filter (OpenAmpere then falls back to API v1, which has to be on).
- Whether the charger answers reliably to two readers (evcc and OpenAmpere) every 10 s.

## Sources

- go-e, API descriptions on GitHub: `goecharger/go-eCharger-API-v1` and `goecharger/go-eCharger-API-v2` (HTTP,
  Modbus, OCPP; no license given): facts only, in own words.
- evcc-io/evcc, `charger/go-e/types2.go` and `api.go` (MIT): the units of API v2; evcc's go-e charger itself
  (`charger/go-e.go`) is "all rights reserved": nothing taken from it.
- cathiele/goecharger (MIT): the units of API v1.
- A Homebridge plugin's README and an installer's guide (2025) for the menu path; a Homey forum post for mDNS.
