# Enphase IQ Gateway with IQ Batteries

> Not official manufacturer information: compiled by the OpenAmpere contributors, can be incomplete or wrong. No
> affiliation with Enphase or EKD; not legal advice. See the [device knowledge README](../README.md).

**Applies to:** Enphase IQ Gateway (Envoy, software 7 and later) with IQ Batteries and IQ microinverters · **Sold as:**
Enphase IQ Battery (EKD from late 2022) · **OpenAmpere:** driver `enphase` (`src/openampere/drivers/enphase/`),
"register map" `enphase_local` · **Support:** display only · **Confirmed on a real device:** no (built from Enphase's
tech brief, open projects and the simulator)

## Summary

- **Not Modbus:** JSON over HTTPS with a self-signed certificate from the IQ Gateway.
- **Needs a token from Enphase's cloud:** since gateway software 7 every request needs an access token (JWT). The owner
  gets it once with their Enphase login on entrez.enphaseenergy.com; for an owner account it is valid for **one
  year**. This is the one device in OpenAmpere that needs the manufacturer's cloud, once a year.
- The owner chooses "Enphase" in the setup (advanced settings) and enters the token; detection over Modbus and the
  network scan do not find the gateway.
- **No energy history:** there are no lifetime counters for the battery.
- OpenAmpere only reads; newer gateway software does not accept local writes anyway.

## Values

| Endpoint / field | Meaning | Type, sign | R/W | Status | Source |
|---|---|---|---|---|---|
| `/info` `sn`, `software` | Serial number and software of the gateway (XML, no token needed) | text | R | derived | pyenphase |
| `/production.json?details=1` `production[]` with `measurementType` "production" (`activeCount` ≥ 1), else `type` "inverters" | PV power | W | R | derived | evcc `enphase`; pyenphase |
| `/production.json?details=1` `consumption[]` "net-consumption" `wNow` | Grid power, + = import (needs consumption CTs) | W | R | derived | evcc `enphase` |
| `/production.json?details=1` `consumption[]` "total-consumption" `wNow` | House consumption (needs consumption CTs) | W | R | derived | pyenphase |
| `/ivp/livedata/status` `meters.storage.agg_p_mw` | Battery power, + = discharging | mW | R | documented (endpoint), derived (sign) | Enphase tech brief; evcc `enphase` |
| `/ivp/ensemble/inventory` `devices[].percentFull` | State of charge per battery; OpenAmpere shows the average | % | R | derived | evcc `enphase`; pyenphase |
| `/admin/lib/tariff` | Battery profile, reserve, charge from grid | JSON | W | derived; not used (refused locally from gateway software 8.2.4225) | pyenphase; Home Assistant docs |

## Behaviour

| Fact | Status | Source |
|---|---|---|
| From gateway software 7 on, local access needs a JWT as `Authorization: Bearer`; an owner token is valid for 1 year, an installer token for 12 hours (also a self-installer account unless Enphase support changes it). A new token needs the cloud again. The gateway uses a self-signed certificate. | documented | Enphase tech brief "Accessing IQ Gateway local APIs or local UI with token-based authentication" (2023-01) |
| The gateway announces itself over mDNS as `_enphase-envoy._tcp`; its label says "IQ Gateway" (older "Envoy"). | derived | Home Assistant manifest; evcc |
| No rate limit is documented; Home Assistant polls every 60 s and pyenphase notes that the gateway can be slow to answer. | derived | home-assistant/core `enphase_envoy` (Apache-2.0); pyenphase (MIT) |
| A battery needs an IQ Gateway Metered; EKD's systems most likely have IQ Battery 3T/10T and no backup (System Controller). | documented (gateway), derived (models) | Enphase IQ Gateway datasheet; research in [#522](https://github.com/Gr33ndev/OpenAmpere/issues/522) |

## Decisions in OpenAmpere

| Decision | Why | Issue/PR |
|---|---|---|
| Display only: every write method refuses, `supports_control` is off. | Nothing confirmed on a device; newer gateway software refuses local writes. | [#522](https://github.com/Gr33ndev/OpenAmpere/issues/522) |
| The token is a setting of its own (`inverter.token`), stored encrypted and bound to the gateway's address (a new address needs the token again); it is sent only to the gateway. The setup test of the stored address uses the stored token. | It gives access to the gateway; like every secret in OpenAmpere. | [#522](https://github.com/Gr33ndev/OpenAmpere/issues/522) |
| The app says plainly when the gateway refuses the token, and the diagnostics show how many days it has left (a warning 30 days before), never the date: it would tell the day the owner made the token. | A token that ran out stops every reading; the owner must get a new one from Enphase. | [#522](https://github.com/Gr33ndev/OpenAmpere/issues/522) |
| Only when the owner chooses "Enphase"; port 502 (the setup's default) stands for 443, then 80, HTTPS first. The token goes only over HTTPS, never over plain HTTP. The certificate is not checked yet; pinning it on the first contact is [#539](https://github.com/Gr33ndev/OpenAmpere/issues/539). | It does not answer on the Modbus port; the certificate is self-signed with nothing to check it against. Someone in the home network who takes over the address could get the token; pinning closes that. | [#522](https://github.com/Gr33ndev/OpenAmpere/issues/522) |
| Asked at most every 15 s (`MIN_POLL_S`). | The gateway is slow to answer; no limit is documented. | [#522](https://github.com/Gr33ndev/OpenAmpere/issues/522) |
| Battery values are optional, each on its own: a gateway without IQ Batteries still shows PV and, with CTs, grid and house; its 0 W from livedata is no battery. | Not every Enphase system has a battery. | [#522](https://github.com/Gr33ndev/OpenAmpere/issues/522) |

## Open questions

- Everything: no owner has tried it yet. Whether an owner can still log in to Enphase after EKD's insolvency and
  whether the token there is valid for a year.
- The signs of net-consumption and `storage.agg_p_mw` on a real system; whether EKD's systems have consumption CTs.
- Whether the token a newer gateway (software D8) can create locally works without internet.
- A reminder in the app (not only the diagnostics) before the token runs out ([#538](https://github.com/Gr33ndev/OpenAmpere/issues/538)).

## Sources

- Enphase, "Accessing IQ Gateway local APIs or local UI with token-based authentication" (tech brief, 2023-01);
  IQ Gateway datasheet.
- pyenphase/pyenphase (MIT), evcc-io/evcc template `enphase` (MIT): endpoints, fields and signs. Facts only, see NOTICE.
- home-assistant/core `enphase_envoy` (Apache-2.0): polling interval, local writes.
- Research comment in [#522](https://github.com/Gr33ndev/OpenAmpere/issues/522).
