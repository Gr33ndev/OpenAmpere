# Netze BW meter portal

> **Not official information.** Compiled by the OpenAmpere contributors; can be incomplete or wrong. No affiliation
> with Netze BW, not legal advice. See [Device knowledge](../README.md).

**Applies to:** the customer portal of the grid operator Netze BW (Baden-Württemberg) and the smart meters
("intelligentes Messsystem") it lists · **Sold as:** – · **OpenAmpere:** grid meter provider `netze_bw`,
`src/openampere/gridmeter/netze_bw.py` · **Support:** reading the meter's quarter-hour values (import and feed-in) ·
**Confirmed on a real device:** partly (portal behaviour seen on real accounts in [#67](https://github.com/Gr33ndev/OpenAmpere/issues/67); OpenAmpere's own import not yet
tested against the portal by the maintainers)

## Summary

- There is no official API. OpenAmpere logs into the customer portal with the owner's own portal login and reads
  the portal's internal interface, so it can break without warning when the portal changes.
- One smart meter shows up twice: once for grid import (OBIS 1.8.0) and once for feed-in (2.8.0).
- Values exist only since the smart meter was installed. A day is published at no fixed time, often hours after it
  ended, sometimes only the next afternoon or later.
- Values come from the CSV download of quarter-hour values; a day counts only when all its quarter hours are there.
- Two-factor login or a captcha cannot be handled.

## Behaviour

| Fact | Status | Source |
|---|---|---|
| The portal has no documented API. Its web app reads data from its own backend under `/bff` after a login through Auth0 (`login.netze-bw.de`) with e-mail and password. | derived | cygnusb/ha-netze-bw; [#60](https://github.com/Gr33ndev/OpenAmpere/issues/60), [#63](https://github.com/Gr33ndev/OpenAmpere/issues/63) |
| The internal interface lists the installations and gives measurements per meter for consumption and feed-in, as daily or quarter-hour values. | derived | cygnusb/ha-netze-bw; [#60](https://github.com/Gr33ndev/OpenAmpere/issues/60) |
| A login with two-factor authentication or a captcha cannot be done by a program. | derived | [#60](https://github.com/Gr33ndev/OpenAmpere/issues/60) |
| Grid import (OBIS 1.8.0) and feed-in (2.8.0) of one smart meter are two installations with their own ids and the same meter number. Merging them by meter number loses one direction. | confirmed | [#67](https://github.com/Gr33ndev/OpenAmpere/issues/67), [#68](https://github.com/Gr33ndev/OpenAmpere/issues/68) |
| Only active installations of type "IMS" (intelligentes Messsystem) have quarter-hour values. | confirmed | [#67](https://github.com/Gr33ndev/OpenAmpere/issues/67) |
| The portal session can still be valid (`/bff/auth/user` answers 200) while the meter values answer 401. A complete new login helps. | confirmed | [#67](https://github.com/Gr33ndev/OpenAmpere/issues/67), [#68](https://github.com/Gr33ndev/OpenAmpere/issues/68) |
| Values exist only from the installation of the smart meter on. | confirmed | [#67](https://github.com/Gr33ndev/OpenAmpere/issues/67) |
| A day is published hours after it ended, at no fixed time, sometimes only in the afternoon of the next day or later. Until then the overview shows 0 kWh for it. | confirmed | [#67](https://github.com/Gr33ndev/OpenAmpere/issues/67), [#68](https://github.com/Gr33ndev/OpenAmpere/issues/68) |
| A complete day has 96 quarter hours, 92 or 100 on the days the clock changes. | confirmed | [#67](https://github.com/Gr33ndev/OpenAmpere/issues/67), [#68](https://github.com/Gr33ndev/OpenAmpere/issues/68) |
| CSV download: separated by `;`, columns "Datum" (dd.mm.yyyy) and "Uhrzeit" (hh:mm) in German local time. The first row is the meter reading at the start; every further row is the energy in kWh (decimal comma) of the quarter hour that ends at its time. | confirmed | [#67](https://github.com/Gr33ndev/OpenAmpere/issues/67) (a working importer shared by a user) |

## Decisions in OpenAmpere

| Decision | Why | Issue/PR |
|---|---|---|
| Fetch every 3 hours; after an error try again after an hour. | The operator publishes the previous day at no fixed time. | [#63](https://github.com/Gr33ndev/OpenAmpere/issues/63), [#68](https://github.com/Gr33ndev/OpenAmpere/issues/68) |
| Read a week of quarter hours per request and walk back week by week until the meter's installation (at most about 400 days); the last 7 days are read again. | Covers the running billing year; the operator may still correct recent days. | [#63](https://github.com/Gr33ndev/OpenAmpere/issues/63), [#68](https://github.com/Gr33ndev/OpenAmpere/issues/68) |
| Use only complete days; a day drops out again if a later answer is incomplete. A missing value stays unknown, never 0. | Half-published days would look like low consumption. | [#68](https://github.com/Gr33ndev/OpenAmpere/issues/68) |
| Keep import and feed-in apart by the installation's own id, not by meter number. | Both directions share the meter number. | [#68](https://github.com/Gr33ndev/OpenAmpere/issues/68) |
| On a 401 for meter values, drop the session and log in from scratch (once per request). | The session check alone is not reliable. | [#68](https://github.com/Gr33ndev/OpenAmpere/issues/68) |
| After the portal refuses the login, do not retry by itself until the owner changes the login data. | Repeated failed logins could lock the owner's account. | [#63](https://github.com/Gr33ndev/OpenAmpere/issues/63), [#68](https://github.com/Gr33ndev/OpenAmpere/issues/68) |
| Send an honest user agent that names OpenAmpere instead of a desktop browser string. | Pretending to be a browser can break the portal's terms of use. | [#174](https://github.com/Gr33ndev/OpenAmpere/issues/174), [#180](https://github.com/Gr33ndev/OpenAmpere/issues/180) |
| Take only facts from other projects, no code. | License and own words; credited in [NOTICE](../../../NOTICE). | [#63](https://github.com/Gr33ndev/OpenAmpere/issues/63) |

## Open questions

- Whether the portal or its Auth0 login turns away clients that do not look like a browser. The honest user agent was
  not tested against the real portal; this needs an owner with an account
  ([#174](https://github.com/Gr33ndev/OpenAmpere/issues/174), [#180](https://github.com/Gr33ndev/OpenAmpere/issues/180)).
- OpenAmpere's import as a whole has not been tested against the real portal by the maintainers; the portal behaviour
  above comes from real accounts and a user's importer in [#67](https://github.com/Gr33ndev/OpenAmpere/issues/67)
  ([#63](https://github.com/Gr33ndev/OpenAmpere/issues/63), [#68](https://github.com/Gr33ndev/OpenAmpere/issues/68)).
- What the portal answers for periods before the meter's installation: OpenAmpere's test model answers HTTP 400; not
  seen on the real portal ([#68](https://github.com/Gr33ndev/OpenAmpere/issues/68)).

## Sources

- cygnusb/ha-netze-bw (MIT): login flow and list of installations, facts only (also where the former desktop browser
  user agent came from, [#174](https://github.com/Gr33ndev/OpenAmpere/issues/174)).
- A working importer a user shared in [#67](https://github.com/Gr33ndev/OpenAmpere/issues/67): CSV format, facts only.
- Issues and pull requests: [#60](https://github.com/Gr33ndev/OpenAmpere/issues/60),
  [#63](https://github.com/Gr33ndev/OpenAmpere/issues/63), [#67](https://github.com/Gr33ndev/OpenAmpere/issues/67),
  [#68](https://github.com/Gr33ndev/OpenAmpere/issues/68), [#174](https://github.com/Gr33ndev/OpenAmpere/issues/174),
  [#180](https://github.com/Gr33ndev/OpenAmpere/issues/180).
- Code: `src/openampere/gridmeter/netze_bw.py`, `src/openampere/gridmeter/__init__.py`; credits in
  [NOTICE](../../../NOTICE).
