# Wallboxes through evcc, OCPP and directly

> **Not official manufacturer information.** Compiled by the OpenAmpere contributors; can be incomplete or wrong. No
> affiliation with evcc or the manufacturers named here, not legal advice. See [Device knowledge](../README.md).

**Applies to:** wallboxes and vehicles supported by evcc; wallboxes that speak OCPP 1.6J (read-only check) · **Sold
as:** – · **OpenAmpere:** `src/openampere/evcc.py` (evcc's REST API), `src/openampere/ocpp.py` (OCPP listener for the
diagnostics) · **Support:** control through evcc; OCPP read-only · **Confirmed on a real device:** no

## Summary

- Most wallboxes are controlled through evcc. OpenAmpere gives evcc the site meters (grid, PV, battery), so evcc needs
  no Modbus connection of its own to the inverter.
- A few wallboxes OpenAmpere reads itself, without evcc: it controls the portable dé wallbox over Tuya LAN, see
  [tuya/de-portable.md](../tuya/de-portable.md), and only shows the FoxESS AC EV Charger over Modbus TCP, never writing
  to it ([foxess/ac-charger.md](../foxess/ac-charger.md), [#439](https://github.com/Gr33ndev/OpenAmpere/issues/439)); its charging modes
  only simulate the control and log what OpenAmpere would set ([#440](https://github.com/Gr33ndev/OpenAmpere/issues/440)).
  Several of them, and an evcc wallbox next to them, share the place "wallbox" in the order of the solar surplus; one
  OpenAmpere only shows counts in the house load but is never acted on.
- Which wallboxes work is decided by evcc. Many need a paid evcc sponsor token; the public trial token runs out after
  some days, and evcc then no longer starts the wallbox.
- In evcc a loadpoint with the heating feature is a heat pump or heating rod; its "SoC" values are temperatures.
- For a quick check, a wallbox can be pointed at OpenAmpere as an OCPP 1.6J central system for at most 15 minutes.
  OpenAmpere only asks questions and never starts, stops or limits charging.
- The FoxESS AC EV Charger has its own page: [foxess/ac-charger.md](../foxess/ac-charger.md).

## Behaviour

| Fact | Status | Source |
|---|---|---|
| evcc requires a paid sponsor token for many wallboxes, among them the FoxESS AC EV Charger. With an expired or missing token evcc refuses to start the wallbox. | derived | evcc documentation; [#317](https://github.com/Gr33ndev/OpenAmpere/issues/317), [#318](https://github.com/Gr33ndev/OpenAmpere/issues/318), [#319](https://github.com/Gr33ndev/OpenAmpere/issues/319) |
| The public test token ("trial") is valid only for a limited time: "a few days" in the code, about ten days in [#317](https://github.com/Gr33ndev/OpenAmpere/issues/317). | derived | [#317](https://github.com/Gr33ndev/OpenAmpere/issues/317); `evcc.py` |
| evcc reports the token in `/api/state` under `sponsor` (`status.name`, `expiresAt`, `expiresSoon`; name `trial` for the test token). | derived | evcc documentation; [#319](https://github.com/Gr33ndev/OpenAmpere/issues/319) |
| EKD sold the Weidmüller AC Smart Value (11 kW) as "AMPERE.Charge" (2023–2024). It speaks Modbus TCP (port 502), Modbus RTU and OCPP 1.6J; evcc controls it with its `weidmüller` template, which needs a sponsor token. OpenAmpere has no driver of its own for it. | documented | EKD datasheets "AMPERE.Charge", 2023-04 and 2024-01; [Photovoltaikforum on the Ampere.Charge](https://www.photovoltaikforum.com/thread/227016-ampere-charge-weidm%C3%BCller-wallbox-energiekonzepte-deutschland-ampere-storage-pro/); [names.md](names.md) |
| evcc keeps reporting an expired test token: `status.name` stays `trial` with `expiresAt` in the past, so only the date shows that it has run out. | confirmed (owner report) | [#462](https://github.com/Gr33ndev/OpenAmpere/issues/462) |
| evcc's `sessionEnergy` and `sessionSolarPercentage` of a loadpoint count from connecting the car, and keep the last session's values after unplugging until the next connect. The charge notifications rely on this: the end message reports these values as the energy since plugging in, and a value lower than at the last end message counts as a new session. | open (not checked against evcc's code or a device) | [#451](https://github.com/Gr33ndev/OpenAmpere/issues/451), [#453](https://github.com/Gr33ndev/OpenAmpere/pull/453) |
| An evcc loadpoint with the charger feature "heating" is a heat pump or heating rod: its "SoC" and limit values are temperatures. | derived | [#245](https://github.com/Gr33ndev/OpenAmpere/issues/245), [#249](https://github.com/Gr33ndev/OpenAmpere/issues/249) |
| A wallbox can be given an OCPP 1.6J central system (WebSocket, subprotocol `ocpp1.6`) in the manufacturer's app. In its BootNotification it reports vendor, model and firmware version; it sends StatusNotification and MeterValues. | documented | OCPP 1.6 specification (Open Charge Alliance); [#328](https://github.com/Gr33ndev/OpenAmpere/issues/328) |
| While a BootNotification is answered with "Pending", the central system may only retrieve information. | documented | OCPP 1.6 specification; `ocpp.py` |
| Without a charging profile, a wallbox in OCPP mode charges without a limit. | open (not checked on a real wallbox) | [#328](https://github.com/Gr33ndev/OpenAmpere/issues/328), [#333](https://github.com/Gr33ndev/OpenAmpere/issues/333) |

## Decisions in OpenAmpere

| Decision | Why | Issue/PR |
|---|---|---|
| Wallboxes OpenAmpere does not know itself are controlled through evcc's REST API (until [#386](https://github.com/Gr33ndev/OpenAmpere/issues/386) the only way); OpenAmpere serves evcc the site meters at `/api/evcc/site`. | evcc supports many wallboxes; the inverter keeps a free connection slot. | [docs/devices.md](../../devices.md), [evcc.md](../../evcc.md) |
| Wallboxes OpenAmpere knows itself are controlled without evcc (`wallbox/native.py`), so far the portable dé wallbox; evcc stays the way for all others. | No sponsor token, no second program for a single wallbox. | [#318](https://github.com/Gr33ndev/OpenAmpere/issues/318), [#386](https://github.com/Gr33ndev/OpenAmpere/issues/386), [tuya/de-portable.md](../tuya/de-portable.md) |
| `wallbox/native.py` has one model per wallbox with the same reading for all (`base.Reading`); a model can be read only (no write path at all): so far the FoxESS AC EV Charger. Its power is shown and counted in the house load, but the surplus control never acts on it; its modes other than "manual" only simulate the control, logged as not sent and never taking surplus from other devices (#440). | Show a wallbox and how its control would behave before its write registers and watchdog are confirmed, without any risk. | [#439](https://github.com/Gr33ndev/OpenAmpere/issues/439), [#440](https://github.com/Gr33ndev/OpenAmpere/issues/440), [foxess/ac-charger.md](../foxess/ac-charger.md) |
| The sponsor token's state is shown in the app; a missing or expired token gets its own message; a date in the past counts as expired. | An expired token stops the wallbox. | [#317](https://github.com/Gr33ndev/OpenAmpere/issues/317), [#319](https://github.com/Gr33ndev/OpenAmpere/issues/319), [#462](https://github.com/Gr33ndev/OpenAmpere/issues/462) |
| The limit of a heating loadpoint is a temperature capped at 20–90 °C; a car loadpoint uses 20–100 %. | evcc uses the same field for both. | [#245](https://github.com/Gr33ndev/OpenAmpere/issues/245), [#249](https://github.com/Gr33ndev/OpenAmpere/issues/249) |
| evcc's battery priority (`priority_soc`) is sent only with control switched on and outside test mode, and it is logged. | Same rules as every other control. | [#219](https://github.com/Gr33ndev/OpenAmpere/issues/219), [#226](https://github.com/Gr33ndev/OpenAmpere/issues/226) |
| OCPP listener for the diagnostics only: OCPP 1.6J, security profile 0, open for at most 15 minutes, at most 500 messages of 64 KiB per connection. BootNotification is answered with "Pending" (heartbeat 60 s), Heartbeat with the time; StatusNotification and MeterValues are recorded for the diagnostics report; everything else (Authorize, StartTransaction, …) gets CALLERROR NotSupported. OpenAmpere sends only GetConfiguration and GetCompositeSchedule; never start or stop, charging profiles or configuration changes. | A read-only check of the wallbox without taking over its control. | [#328](https://github.com/Gr33ndev/OpenAmpere/issues/328), [#333](https://github.com/Gr33ndev/OpenAmpere/issues/333) |
| The OCPP listener refuses a WebSocket that carries an `Origin` header or a Host that is not allowed (as the rest of the app); it takes its single connection before the first check that waits, and the texts a wallbox sends (vendor, model, firmware, status, …) appear in the report as inline code. | A wallbox sends no `Origin`, a browser always does: otherwise any web page opened in the home network could take the connection and write into the report. Applies to the Home Assistant app as well, where the wallbox connects directly, not through the sidebar. | security fix ([GHSA-q2mp-55hm-346m](https://github.com/Gr33ndev/OpenAmpere/security/advisories/GHSA-q2mp-55hm-346m)) |
| The app warns before the OCPP check that the wallbox may charge without a limit while it points at OpenAmpere. | No charging profile is sent. | [#328](https://github.com/Gr33ndev/OpenAmpere/issues/328), [#333](https://github.com/Gr33ndev/OpenAmpere/issues/333) |

## Open questions

- No wallbox has been confirmed with OpenAmpere on a real system
  ([#111](https://github.com/Gr33ndev/OpenAmpere/issues/111), [#173](https://github.com/Gr33ndev/OpenAmpere/issues/173),
  [#185](https://github.com/Gr33ndev/OpenAmpere/issues/185)).
- The OCPP listener is tested with a fake client only, not with a real wallbox
  ([#333](https://github.com/Gr33ndev/OpenAmpere/issues/333)).
- How long the evcc trial token lasts: "a few days" in the code against about ten days in
  [#317](https://github.com/Gr33ndev/OpenAmpere/issues/317); evcc may change it.

## Sources

- evcc (evcc-io/evcc, MIT for the core, some device modules proprietary): documentation and REST API; no evcc code in
  OpenAmpere, see [NOTICE](../../../NOTICE).
- OCPP 1.6 specification of the Open Charge Alliance.
- Issues and pull requests: [#111](https://github.com/Gr33ndev/OpenAmpere/issues/111),
  [#173](https://github.com/Gr33ndev/OpenAmpere/issues/173), [#185](https://github.com/Gr33ndev/OpenAmpere/issues/185),
  [#219](https://github.com/Gr33ndev/OpenAmpere/issues/219), [#226](https://github.com/Gr33ndev/OpenAmpere/issues/226),
  [#245](https://github.com/Gr33ndev/OpenAmpere/issues/245), [#249](https://github.com/Gr33ndev/OpenAmpere/issues/249),
  [#317](https://github.com/Gr33ndev/OpenAmpere/issues/317), [#318](https://github.com/Gr33ndev/OpenAmpere/issues/318),
  [#319](https://github.com/Gr33ndev/OpenAmpere/issues/319), [#328](https://github.com/Gr33ndev/OpenAmpere/issues/328),
  [#333](https://github.com/Gr33ndev/OpenAmpere/issues/333).
- Code: `src/openampere/evcc.py`, `src/openampere/ocpp.py`; [docs/devices.md](../../devices.md),
  [docs/evcc.md](../../evcc.md).
