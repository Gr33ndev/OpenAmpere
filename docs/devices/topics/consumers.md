# Relays, smart plugs and SG Ready

> **Not official manufacturer information.** Compiled by the OpenAmpere contributors; can be incomplete or wrong. No
> affiliation with the manufacturers named here, not legal advice. See [Device knowledge](../README.md).

**Applies to:** Shelly relays (Gen1, Gen2 and newer), any device that can be switched by calling a web address,
heat pumps with an SG Ready input switched through such a relay · **OpenAmpere:** consumers,
`src/openampere/consumers.py` (my-PV heating rods: [mypv/elwa.md](../mypv/elwa.md)) · **Support:** switching on and
off; their power is not read · **Confirmed on a real device:** no

## Summary

- These devices are only switched on or off, with a minimum run time and a minimum pause time. OpenAmpere uses the
  power the owner enters; it does not read their power.
- Shelly Gen1 and Gen2 relays are switched through their local HTTP interface; any other device through one web
  address for on and one for off.
- A relay has no time limit: one that OpenAmpere switched on stays on while OpenAmpere is stopped or uninstalled.
- For an SG Ready heat pump OpenAmpere only switches the relay on the SG Ready contact; what the heat pump does with
  the signal is set on the heat pump.
- None of these devices has been confirmed with OpenAmpere on a real system yet.

## Behaviour

| Fact | Status | Source |
|---|---|---|
| Shelly Gen1 relays switch with `http://<address>/relay/<channel>?turn=on` or `turn=off`. | derived | Shelly's Gen1 HTTP API as used by the code; [docs/devices.md](../../devices.md) |
| Shelly Gen2 and newer switch with `http://<address>/rpc/Switch.Set?id=<channel>&on=true` or `on=false`. | derived | Shelly's Gen2 RPC API as used by the code; [docs/devices.md](../../devices.md) |
| An SG Ready input of a heat pump is a contact that a relay can switch; the reaction to the signal is configured on the heat pump. | derived | [docs/devices.md](../../devices.md) |
| A relay can come back switched off after a power cut, so after a restart of OpenAmpere its state is not known. | derived | [#243](https://github.com/Gr33ndev/OpenAmpere/issues/243), [#247](https://github.com/Gr33ndev/OpenAmpere/issues/247) |
| A relay keeps its state when nobody switches it: one that OpenAmpere switched on stays on while OpenAmpere is stopped or uninstalled. | derived | [#359](https://github.com/Gr33ndev/OpenAmpere/issues/359), [#365](https://github.com/Gr33ndev/OpenAmpere/issues/365); [docs/devices.md](../../devices.md) |

## Decisions in OpenAmpere

| Decision | Why | Issue/PR |
|---|---|---|
| Relays and web-address devices are only switched on and off, with minimum run and pause times, and with the power the owner enters; the channel of a Shelly relay is selectable. A web-address device takes one address for on and one for off (http or https). | Works with any switchable device without reading it. | [docs/devices.md](../../devices.md) |
| A relay that OpenAmpere really switched on is remembered (meta `consumer_switched_on` with its configuration, kept across restarts and a backup restore). It is switched off when it is disabled or removed, when its address, channel or type changes, or when test mode is entered. | Release only what OpenAmpere switched; switching off is the safe direction. | [#217](https://github.com/Gr33ndev/OpenAmpere/issues/217), [#224](https://github.com/Gr33ndev/OpenAmpere/issues/224), [#243](https://github.com/Gr33ndev/OpenAmpere/issues/243), [#247](https://github.com/Gr33ndev/OpenAmpere/issues/247) |
| Switching off is written even with the master switch off or in test mode. | Leaving a device on is the risk. | [#217](https://github.com/Gr33ndev/OpenAmpere/issues/217), [#243](https://github.com/Gr33ndev/OpenAmpere/issues/243) |
| After a restart OpenAmpere sends the wanted state once; in manual mode it always sends. | The relay's state is unknown after a power cut. | [#243](https://github.com/Gr33ndev/OpenAmpere/issues/243), [#247](https://github.com/Gr33ndev/OpenAmpere/issues/247) |
| An unreachable device is tried again after 15 s, 30 s and so on up to every 10 minutes, not on every tick; only the first error is logged. A disabled or removed device that stays unreachable is given up after a day. | Avoids flooding the network and the log. | [#243](https://github.com/Gr33ndev/OpenAmpere/issues/243), [#247](https://github.com/Gr33ndev/OpenAmpere/issues/247) |
| When OpenAmpere stops, only adjustable devices (such as a heating rod) are set to 0 W; relays are left as they are. The app and the guides tell owners to set relays to "Aus" (off) on the "Geräte" page before stopping or uninstalling. | Relays have no time limit and OpenAmpere cannot switch them after it has stopped. | [#359](https://github.com/Gr33ndev/OpenAmpere/issues/359), [#365](https://github.com/Gr33ndev/OpenAmpere/issues/365) |
| Modbus consumers accept only device addresses 1–247; never the broadcast 0. | Behind a gateway every device would take a write to 0. | [#259](https://github.com/Gr33ndev/OpenAmpere/issues/259), [#262](https://github.com/Gr33ndev/OpenAmpere/issues/262) |

## Open questions

- No relay, web-address device or SG Ready heat pump has been confirmed with OpenAmpere on a real system
  ([#111](https://github.com/Gr33ndev/OpenAmpere/issues/111), [#173](https://github.com/Gr33ndev/OpenAmpere/issues/173),
  [#185](https://github.com/Gr33ndev/OpenAmpere/issues/185)).
- [#359](https://github.com/Gr33ndev/OpenAmpere/issues/359) brought up that relays stay on after a stop, while relays
  are still listed as not confirmed on a real system. The fact is shown as derived here until a report from a real
  relay confirms it.
- Which SG Ready states a heat pump uses with a single contact depends on the heat pump; OpenAmpere switches one
  contact only.

## Sources

- Shelly's public HTTP API documentation (Gen1 HTTP API, Gen2 RPC API).
- Issues and pull requests: [#111](https://github.com/Gr33ndev/OpenAmpere/issues/111),
  [#173](https://github.com/Gr33ndev/OpenAmpere/issues/173), [#185](https://github.com/Gr33ndev/OpenAmpere/issues/185),
  [#217](https://github.com/Gr33ndev/OpenAmpere/issues/217), [#224](https://github.com/Gr33ndev/OpenAmpere/issues/224),
  [#243](https://github.com/Gr33ndev/OpenAmpere/issues/243), [#247](https://github.com/Gr33ndev/OpenAmpere/issues/247),
  [#259](https://github.com/Gr33ndev/OpenAmpere/issues/259), [#262](https://github.com/Gr33ndev/OpenAmpere/issues/262),
  [#359](https://github.com/Gr33ndev/OpenAmpere/issues/359), [#365](https://github.com/Gr33ndev/OpenAmpere/issues/365).
- Code: `src/openampere/consumers.py`; [docs/devices.md](../../devices.md).
