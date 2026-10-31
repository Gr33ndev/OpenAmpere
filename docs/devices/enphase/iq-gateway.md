# Enphase IQ Gateway with IQ Batteries

> Not official manufacturer information: compiled by the OpenAmpere contributors, can be incomplete or wrong. No
> affiliation with Enphase or EKD; not legal advice. See the [device knowledge README](../README.md).

**Applies to:** Enphase IQ Gateway (Envoy, software 7 and later) with IQ Batteries and IQ microinverters (or a string
inverter with only the battery from Enphase) · **Sold as:**
Enphase IQ Battery (EKD from late 2022) · **OpenAmpere:** driver `enphase` (`src/openampere/drivers/enphase/`),
"register map" `enphase_local` · **Support:** display only · **Confirmed on a real device:** no (built from Enphase's
tech brief, open projects and the simulator)

## Summary

- **Not Modbus:** JSON over HTTPS with a self-signed certificate from the IQ Gateway; OpenAmpere pins it when the owner
  enters the token (#539).
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
| On software 7 and later the gateway answers `/ivp/livedata/status` without IQ Batteries too, with `meters.storage.agg_p_mw` 0. | derived | pyenphase and Home Assistant test fixtures |
| EKD systems may have IQ7 or IQ8 microinverters (IQ8 only shipped in Germany from mid-2023), or a string inverter with only the battery from Enphase. With a string inverter and no production CT, the PV power from `production.json` misses the string inverter. | open | research in [#522](https://github.com/Gr33ndev/OpenAmpere/issues/522) (pv-magazine.de, 2023-06-26) |
| Whether the gateway keeps its self-signed certificate across a firmware update, a reboot or a factory reset. With the pin (#539) a new certificate means the owner enters the token again, usually a new one from entrez.enphaseenergy.com. | open | – |
| A battery needs an IQ Gateway Metered; EKD's systems most likely have IQ Battery 3T/10T and no backup (System Controller). | documented (gateway), derived (models) | Enphase IQ Gateway datasheet; research in [#522](https://github.com/Gr33ndev/OpenAmpere/issues/522) |

## Decisions in OpenAmpere

| Decision | Why | Issue/PR |
|---|---|---|
| Display only: every write method refuses, `supports_control` is off. | Nothing confirmed on a device; newer gateway software refuses local writes. | [#522](https://github.com/Gr33ndev/OpenAmpere/issues/522) |
| The token is a setting of its own (`inverter.token`), stored encrypted and bound to the gateway's address (a new address needs the token again); it is sent only to the gateway. The setup test of the stored address uses the stored token. | It gives access to the gateway; like every secret in OpenAmpere. | [#522](https://github.com/Gr33ndev/OpenAmpere/issues/522) |
| The app says plainly when the gateway refuses the token, and the diagnostics show how many days it has left (a warning 30 days before), never the date: it would tell the day the owner made the token. | A token that ran out stops every reading; the owner must get a new one from Enphase. | [#522](https://github.com/Gr33ndev/OpenAmpere/issues/522) |
| The days left are also on "Mehr → Meine Anlage" (a warning from 30 days before, with the way to the setup), in `/api/status` and the external API (`token_days_left`), as a diagnostic sensor in Home Assistant, and in one ntfy message 14 days before (with the messages about the connection, again for a new token). In days, as in the diagnostics. The date is not hidden there: the status is open on the home network, and with today's date the days give it; it is a fact about the plant like the others there, and only the shared report must not tell it. | The owner rarely opens the diagnostics; the token runs out once a year. | [#538](https://github.com/Gr33ndev/OpenAmpere/issues/538) |
| Only when the owner chooses "Enphase"; port 502 (the setup's default) stands for 443, then 80, HTTPS first. The token goes only over HTTPS, never over plain HTTP; a gateway that answers only plain HTTP gets a message of its own, not "token refused". | It does not answer on the Modbus port. | [#522](https://github.com/Gr33ndev/OpenAmpere/issues/522) |
| The certificate is pinned on first use: the setup test with a newly typed token records the SHA-256 fingerprint of the certificate it sent the token to, the setup saves it with the token (`inverter.cert_sha256`, bound to the address like the token, saved only together with a token). The token then goes only to that certificate, checked after the TLS handshake and before anything is sent; another certificate stops every request with a message to enter the token again. A token from `config.yaml` without a fingerprint trusts the first certificate after each start. | The certificate is self-signed with nothing to check it against, so someone in the home network who takes over the address (ARP spoofing) could get the token, valid for a year. Unlike Senec (not pinned, #521) the owner can pair again in the app: entering the token again takes the new certificate, e.g. after a replaced gateway. | [#539](https://github.com/Gr33ndev/OpenAmpere/issues/539) |
| Asked at most every 15 s (`MIN_POLL_S`). | The gateway is slow to answer; no limit is documented. | [#522](https://github.com/Gr33ndev/OpenAmpere/issues/522) |
| Battery values are optional, each on its own: a gateway without IQ Batteries still shows PV and, with CTs, grid and house; its 0 W from livedata is no battery until the inventory listed one, after that 0 W is an idle battery. | Not every Enphase system has a battery. | [#522](https://github.com/Gr33ndev/OpenAmpere/issues/522) |

## Open questions

- Everything: no owner has tried it yet. Whether an owner can still log in to Enphase after EKD's insolvency and
  whether the token there is valid for a year.
- The signs of net-consumption and `storage.agg_p_mw` on a real system; whether EKD's systems have consumption CTs.
- Whether the token a newer gateway (software D8) can create locally works without internet.

## Sources

- Enphase, "Accessing IQ Gateway local APIs or local UI with token-based authentication" (tech brief, 2023-01);
  IQ Gateway datasheet.
- pyenphase/pyenphase (MIT), evcc-io/evcc template `enphase` (MIT): endpoints, fields and signs. Facts only, see NOTICE.
- home-assistant/core `enphase_envoy` (Apache-2.0): polling interval, local writes.
- Research comment in [#522](https://github.com/Gr33ndev/OpenAmpere/issues/522).
