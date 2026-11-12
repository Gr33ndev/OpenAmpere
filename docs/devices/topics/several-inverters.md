# Several inverters in one installation

> **Not official information.** Compiled by the OpenAmpere contributors; can be incomplete or wrong. No affiliation
> with EKD or the manufacturers named here, not legal advice. See [Device knowledge](../README.md).

**Applies to:** installations with more than one inverter or battery inverter, each reachable on its own ·
**OpenAmpere:** `inverters.py`, `collector.py`, settings `inverters` · **Confirmed on a real device:** open

## Summary

- OpenAmpere reads the first inverter as before and up to five further ones, each over its own connection and
  checked by its own serial number. The stored history shows the combined values; the energy flow draws a sun and
  a battery for each inverter and gives the sums in a line below it.
- Inverters at one house connection usually all measure the same grid point. Their grid readings are not added up.
- An inverter's own house value counts the other batteries as consumption, so the combined house power is the
  balance of grid, PV and battery.

## Behaviour

| Fact | Status | Source |
|---|---|---|
| Installations sold by EKD exist with two "Ampere.StoragePro" units, both detected as SAJ type 0x005E ("3P-X E2"), each with its own network connection and each detected on its own. | confirmed | owner report in [#619](https://github.com/Gr33ndev/OpenAmpere/issues/619) |
| In such an installation an AC-coupled string inverter, read by a data logger over RS485, shows up on Meter B of an SAJ unit as the second PV inverter. | confirmed | owner report in [#619](https://github.com/Gr33ndev/OpenAmpere/issues/619) |
| Two batteries at one house connection both regulating towards zero grid power can work against each other: while one is charged from the grid, the other sees that as consumption and discharges. | derived | [#619](https://github.com/Gr33ndev/OpenAmpere/issues/619), review of [#620](https://github.com/Gr33ndev/OpenAmpere/pull/620) |
| Whether two such units measure the same grid point or each its own, and whether they are set up as a parallel system, is not known yet. | open | [#619](https://github.com/Gr33ndev/OpenAmpere/issues/619) |

## Decisions in OpenAmpere

| Decision | Why | Issue/PR |
|---|---|---|
| The grid comes from the first inverter; a further one marked "Misst einen eigenen Netzanschluss" adds its grid power and counters. | All inverters at one house connection measure the same grid point; adding them would count the grid twice. | [#619](https://github.com/Gr33ndev/OpenAmpere/issues/619), [#620](https://github.com/Gr33ndev/OpenAmpere/pull/620) |
| The combined house power is the balance grid + PV + battery. | Each inverter's own house value counts the other batteries as consumption. | [#619](https://github.com/Gr33ndev/OpenAmpere/issues/619), [#620](https://github.com/Gr33ndev/OpenAmpere/pull/620) |
| The energy of a further inverter is counted by the differences of its counters, from the moment it is set up. | Its lifetime counters would otherwise land in one quarter hour; a restart (counter 0) or a reset would count as energy. | [#620](https://github.com/Gr33ndev/OpenAmpere/pull/620) |
| A further inverter without readings for five minutes is left out of the combined values until it answers again; the app and a notification say so. Before that the combined values are missing. | A short gap is spread by the storage; a long one would otherwise stop recording the first inverter's energy too. | [#620](https://github.com/Gr33ndev/OpenAmpere/pull/620) |
| A further inverter can be marked "Werte stecken schon im ersten Wechselrichter": nothing of it is added, the energy flow does not draw it, and its values are shown in a line below the flow. | In a parallel system the first inverter may already report the values of all; adding the others would count them twice. Which systems do this is not known, so the owner decides. | [#619](https://github.com/Gr33ndev/OpenAmpere/issues/619) |
| Battery settings can be changed per inverter, in the app, through the interface for other apps and in Home Assistant. Grid charging charges every battery at the same time, each through its own remote control with the same safety rules; the charging power of the settings is shared by the size of each battery, and each battery plans with the size entered for its inverter (the size on the charging page, shared, only while one is missing). | Charged alone, one battery would discharge into the other one. | [#619](https://github.com/Gr33ndev/OpenAmpere/issues/619) |
| The export limit acts on the first inverter only; the app says so. | The legal limit is the sum at the connection point (§ 9 EEG); how to share it between inverters is still to be settled. | [#619](https://github.com/Gr33ndev/OpenAmpere/issues/619) |
