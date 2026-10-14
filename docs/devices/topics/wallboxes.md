# Wallboxes through evcc and OCPP

> **Not official manufacturer information.** Compiled by the OpenAmpere contributors; can be incomplete or wrong. No
> affiliation with evcc or the manufacturers named here, not legal advice. See [Device knowledge](../README.md).

**Applies to:** wallboxes and vehicles supported by evcc; wallboxes that speak OCPP 1.6J (read-only check) · **Sold
as:** – · **OpenAmpere:** `src/openampere/evcc.py` (evcc's REST API), `src/openampere/ocpp.py` (OCPP listener for the
diagnostics) · **Support:** control through evcc; OCPP read-only · **Confirmed on a real device:** no

## Summary

- OpenAmpere does not control wallboxes itself; evcc does. OpenAmpere gives evcc the site meters (grid, PV, battery),
  so evcc needs no Modbus connection of its own to the inverter.
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
| An evcc loadpoint with the charger feature "heating" is a heat pump or heating rod: its "SoC" and limit values are temperatures. | derived | [#245](https://github.com/Gr33ndev/OpenAmpere/issues/245), [#249](https://github.com/Gr33ndev/OpenAmpere/issues/249) |
| A wallbox can be given an OCPP 1.6J central system (WebSocket, subprotocol `ocpp1.6`) in the manufacturer's app. In its BootNotification it reports vendor, model and firmware version; it sends StatusNotification and MeterValues. | documented | OCPP 1.6 specification (Open Charge Alliance); [#328](https://github.com/Gr33ndev/OpenAmpere/issues/328) |
| While a BootNotification is answered with "Pending", the central system may only retrieve information. | documented | OCPP 1.6 specification; `ocpp.py` |
| Without a charging profile, a wallbox in OCPP mode charges without a limit. | open (not checked on a real wallbox) | [#328](https://github.com/Gr33ndev/OpenAmpere/issues/328), [#333](https://github.com/Gr33ndev/OpenAmpere/issues/333) |

## Decisions in OpenAmpere

| Decision | Why | Issue/PR |
|---|---|---|
| Wallboxes are controlled only through evcc's REST API; OpenAmpere serves evcc the site meters at `/api/evcc/site`. | evcc supports many wallboxes; the inverter keeps a free connection slot. | [docs/devices.md](../../devices.md), [evcc.md](../../evcc.md) |
| The sponsor token's state is shown in the app; a missing or expired token gets its own message. | An expired token stops the wallbox. | [#317](https://github.com/Gr33ndev/OpenAmpere/issues/317), [#319](https://github.com/Gr33ndev/OpenAmpere/issues/319) |
| The limit of a heating loadpoint is a temperature capped at 20–90 °C; a car loadpoint uses 20–100 %. | evcc uses the same field for both. | [#245](https://github.com/Gr33ndev/OpenAmpere/issues/245), [#249](https://github.com/Gr33ndev/OpenAmpere/issues/249) |
| evcc's battery priority (`priority_soc`) is sent only with control switched on and outside test mode, and it is logged. | Same rules as every other control. | [#219](https://github.com/Gr33ndev/OpenAmpere/issues/219), [#226](https://github.com/Gr33ndev/OpenAmpere/issues/226) |
| OCPP listener for the diagnostics only: OCPP 1.6J, security profile 0, open for at most 15 minutes, at most 500 messages of 64 KiB per connection. BootNotification is answered with "Pending" (heartbeat 60 s), Heartbeat with the time; StatusNotification and MeterValues are recorded for the diagnostics report; everything else (Authorize, StartTransaction, …) gets CALLERROR NotSupported. OpenAmpere sends only GetConfiguration and GetCompositeSchedule; never start or stop, charging profiles or configuration changes. | A read-only check of the wallbox without taking over its control. | [#328](https://github.com/Gr33ndev/OpenAmpere/issues/328), [#333](https://github.com/Gr33ndev/OpenAmpere/issues/333) |
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
