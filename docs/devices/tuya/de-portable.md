# Portable dé wallbox (Tuya)

> Not official manufacturer information: compiled by the OpenAmpere contributors, possibly incomplete or wrong.
> Changing device settings can affect warranty and grid connection rules. No affiliation with dé, Tuya or EKD, not
> legal advice. See [Device knowledge](../README.md).

**Applies to:** the portable 11 kW wallbox sold as "dé" (three-phase, CEE plug, Tuya Wi-Fi module, local protocol
3.5) · **Sold as:** "dé" portable EV charger · **OpenAmpere:** `src/openampere/tuya.py` (protocol),
`src/openampere/wallbox/de_tuya.py` (data points), `src/openampere/wallbox/native.py` (several wallboxes, offline
behaviour, control) · **Support:** display and control (solar surplus, fast, off) · **Confirmed on a real device:**
partly (reading yes, control no)

## Summary

- No Modbus. The wallbox speaks the local Tuya protocol 3.5 on TCP port 6668, encrypted with the device's "local
  key" (16 characters). The owner gets the key once through Tuya's developer platform; no cloud is needed afterwards.
- It accepts only one local connection at a time. OpenAmpere opens one per request and closes it right away.
- It is portable and often unplugged for days. OpenAmpere shows it as "not reachable since …" without old values,
  asks again less and less often (up to every 5 minutes) and controls it again as soon as it answers.
- Values come as data points; two of them are JSON text with the measurements and the device information.
- It has no watchdog: a current or a start written by OpenAmpere stays until something else changes it.

## Data points

Numbers in the JSON texts are integers; "0.1 V" means 2300 is 230.0 V.

| Data point | Meaning | Type, scale | R/W | Status | Source |
|---|---|---|---|---|---|
| 102 `L1`…`L3` | Per phase: voltage, current, power | JSON text, list [0.1 V, 0.1 A, 0.1 kW] | R | confirmed | an owner's status reads, idle and charging three-phase, matched the car's app; tuya-local, lachand/EV_charger |
| 102 `p` | Total charging power | JSON text, 0.1 kW | R | confirmed | an owner's status read while charging (about 5 kW, matched the car's app) |
| 102 `t` | Temperature | JSON text, 0.1 °C (assumed) | R | open | an owner's status read; the scale is a guess |
| 102 `d` | Duration of the running session | JSON text, 0.1 s | R | derived | lachand/EV_charger (checked there against a long session); an owner's read fits |
| 102 `e` | Energy of the running session | JSON text, 0.1 kWh | R | derived | lachand/EV_charger |
| 106 `fv` | Firmware version | JSON text, text | R | confirmed | an owner's status read, matched the app |
| 106 `cp` | Control pilot voltage: about 12 V without a car, 9 V plugged in, 6 V charging | JSON text, V as text | R | confirmed | an owner's status reads (12.6 V idle, 6.6 V charging); IEC 61851 levels |
| 106 `e` | Energy of all sessions | JSON text, 0.1 kWh as text | R | confirmed | an owner's status read, matched the app's total |
| 106 `r` | Residual current protection | JSON text, text | R | confirmed | an owner's status read |
| 107 | Current steps the wallbox offers, e.g. 6, 8, 10, 13, 16 | JSON list as text, A | R | confirmed | an owner's status read; tuya-local |
| 109 | State: SLEEP, IDLE, IDLEINS (plugged in), WORKING (charging), WAIT, ERRORPAUSE (fault), PAUSE, STOP (finished) | text | R | partly confirmed | SLEEP and WORKING in an owner's reads; the others from tuya-local |
| 140 | Start (true) or stop (false) charging | bool | W | derived | tuya-local, lachand/EV_charger; not reported back on reads |
| 150 | Charging current | integer, A | R/W | derived (write), confirmed (read) | an owner's read while charging, matched the app; tuya-local |
| 152 | Maximum current set in the app | integer, A | R/W | derived | tuya-local; present in an owner's reads |
| 154 | What happens when a car is plugged in: 0 ask, 1 charge, 2 wait | integer | R/W | derived | tuya-local (not used by OpenAmpere) |
| 101 | Operating state as a number (101 idle, 300 charging seen) | integer | R/W | open | an owner's reads; lachand/EV_charger names 200 ready, 204 paused |
| 151 | Charging schedule (`m`, `ss`, `se`) | JSON text | R/W | derived | tuya-local, lachand/EV_charger (not used) |
| 155–157, 188, 189 | NFC, unearthed mode, product variant, refresh, unknown | various | – | open | tuya-local names most of them |

## Behaviour

| Fact | Status | Source |
|---|---|---|
| Port 502 (Modbus) is closed; TCP 6668 is open. | confirmed | an owner's port check |
| The local key changes when the wallbox is paired again in the app. | derived | Tuya documentation of the developer platform; tinytuya |
| Only one local connection at a time; a second one is closed. | derived | lachand/EV_charger, tinytuya |
| Some firmware keeps the last current and power after a session ends while the state is not WORKING. | derived | lachand/EV_charger |
| Currents between the offered steps (e.g. 11 A) were accepted on one device. | derived | lachand/EV_charger; OpenAmpere uses only the offered steps |
| Devices with protocol 3.3 and newer announce themselves by UDP broadcast on 6667 (3.5 also 7000), with their device ID and IP address. | derived | tinytuya |
| Announcements come in two formats: prefix 6699 (AES-GCM, protocol 3.5) and the older prefix 55AA (return code, then the JSON encrypted with AES-ECB or plain); devices with protocol 3.5 also use the older one. Both with the fixed key of the announcements. | derived | tinytuya (checked against its decoding) |
| Some devices with protocol 3.5 announce themselves only after a request (command 0x25, `{"from": "app", "ip": …}`) to UDP 7000, and may answer it directly to the address that asked. | derived | tinytuya |
| The OpenAmpere search did not find an owner's wallbox in 0.30.0 (it understood only the 6699 format and did not ask). | confirmed | an owner's report (2026-10) |
| Many wallboxes start charging by themselves when a car is plugged in, with the current that is set (data point 154, "charge"). | derived | tuya-local (154: prompt, charge, wait) |

## Decisions in OpenAmpere

| Decision | Why | Issue/PR |
|---|---|---|
| Own implementation of the protocol with `cryptography` instead of the tinytuya library. | No new dependency, asyncio, only the few commands needed; checked against tinytuya during development. | [#386](https://github.com/Gr33ndev/OpenAmpere/issues/386) |
| One connection per request, closed right away; the read-back after a write goes over the same connection. | The device allows one connection; a second one right after the first may be refused while it still closes. | [#386](https://github.com/Gr33ndev/OpenAmpere/issues/386) |
| Read in the background; after a failure again after 15 s, 30 s, … up to every 5 minutes; logged once when it goes and once when it comes back; no old values shown while it is away. | A portable wallbox is unplugged most of the time. | [#386](https://github.com/Gr33ndev/OpenAmpere/issues/386) |
| With a device ID, a wallbox away for more than 2 minutes is searched by its UDP announcement (at most every 15 minutes) and its new address taken over; not in the Home Assistant app. An announcement counts only if it comes from the address it names and that address is in the home network. The search reads only the 6699 format; the older 55AA format is not read on purpose, because it needs AES-ECB (a device announcing itself only that way is found through the request or entered by its address). It asks every 3 seconds on UDP 7000 and listens for 8 seconds on 255.255.255.255, the broadcast address and the own address of each network card in the home network, never on all interfaces (checked in Linux containers with a device that only answers the request). | The router may give it a new address; the app has no host network; a forged announcement must not move a wallbox elsewhere; no ECB in OpenAmpere. | [#386](https://github.com/Gr33ndev/OpenAmpere/issues/386) |
| Several wallboxes (up to 4) share the place "wallbox" in the order of the solar surplus, in the order of their list. | Owners may have more than one wallbox. | [#386](https://github.com/Gr33ndev/OpenAmpere/issues/386) |
| Modes: manual (only shown), off, solar, minimum + solar, fast. Default is manual. Without a car nothing is written; the mode applies once one is plugged in. | Nothing is written until the owner chooses; the mode is chosen before plugging in, as in evcc. | [#386](https://github.com/Gr33ndev/OpenAmpere/issues/386) |
| Only the offered current steps, at most the owner's limit and the maximum of the app; 230 V per phase; phases from the currents while charging, otherwise from the voltages. | Steps are what the device says it supports; a three-phase wallbox taken for single-phase would draw three times the power. | [#386](https://github.com/Gr33ndev/OpenAmpere/issues/386) |
| Solar, minimum + solar and fast each have their own highest current (default: the owner's limit), chosen on the wallbox's card; a value above a lowered limit is cut to it. | Owners want e.g. at most 8 A with minimum + solar and a choice of 6, 8 or 16 A for fast; the lowest step is 6 A, so 4 A is not possible. | [#402](https://github.com/Gr33ndev/OpenAmpere/issues/402) |
| Solar mode: starts after two checks with surplus for the lowest step plus 200 W and 5 minutes after the last stop; lowers at once, raises at most every 30 s. Without enough surplus a charge OpenAmpere started goes on with the lowest step (the rest from the battery or the grid) and stops only when it has stayed so for the owner's waiting time (1–60 minutes, default 10) and after at least 5 minutes of charging. A charge the wallbox started by itself (on plugging in) is stopped at the first check without enough surplus. | Clouds, the car's contactor, fewer writes; on three phases the lowest step is about 4.1 kW, so a short cloud stopped the car (#402); a wallbox starting on its own would otherwise charge from the grid until the next checks. | [#386](https://github.com/Gr33ndev/OpenAmpere/issues/386), [#402](https://github.com/Gr33ndev/OpenAmpere/issues/402) |
| A wallbox reporting STOP ("complete") counts as a full car and is not started, unless OpenAmpere has stopped it since the car last charged (stored with the wallbox, so also after a restart): then it is asked to start again when there is surplus (again after 5 minutes if the start did not reach it). The note is cleared when the car charges or is unplugged. It is kept when OpenAmpere lets go of the wallbox (master switch off, wallbox switched off or changed) until it controls it again, so the master switch off and on again with the car plugged in does not leave the car "full" ([#456](https://github.com/Gr33ndev/OpenAmpere/issues/456)). | Whether the wallbox shows STOP also after a stop by OpenAmpere is open; if it does, a cloud would otherwise end solar charging until the car is unplugged. Same rule as the charge notifications (#451). | [#454](https://github.com/Gr33ndev/OpenAmpere/issues/454) |
| Master switch and test mode as for every control; every write read back and in the control log. Before the first write the owner's current is stored (meta `wallbox_owned`) and put back when OpenAmpere lets go: mode "manual", master switch off, wallbox removed or switched off. A charge OpenAmpere started is then stopped too, except with "manual", where the owner takes over. | No watchdog: OpenAmpere must undo what it did. | [#386](https://github.com/Gr33ndev/OpenAmpere/issues/386) |
| When OpenAmpere stops (update, restart), the wallbox keeps charging as it is. | A restart for an update should not interrupt the car; the stored owner's current is still put back later. | [#386](https://github.com/Gr33ndev/OpenAmpere/issues/386) |
| The local key is stored encrypted, never sent to the app and replaced by a placeholder in reports, like the address and the device ID; the energy counters are left out. | The key opens the device in the home network; counters identify an installation. | [#386](https://github.com/Gr33ndev/OpenAmpere/issues/386) |

## Open questions

- Writing 140 and 150 is not yet confirmed on a device with OpenAmpere; nor whether 150 is stored permanently (flash
  wear) or only for the session.
- Scale of 102 `t`; meaning of 101 and of 155–157, 188, 189.
- Whether other dé models (the wall-mounted one) use the same data points.
- How the wallbox behaves when the car is full while OpenAmpere sends a start (STOP state), and after a power cut.
- Whether 102 `e` counts from plugging in and starts again at 0 with the next car (assumed by the charge notifications, which report it as the energy since plugging in and take a value lower than at the last end message as a new session, [#451](https://github.com/Gr33ndev/OpenAmpere/issues/451)).
- Whether DP 109 shows PAUSE or STOP after a stop by OpenAmpere (DP 140 false). Until this is known, the charge notifications end a charge at STOP at once only if OpenAmpere has sent no stop since the car last charged; after a stop by OpenAmpere, STOP waits 15 minutes like a pause ([#451](https://github.com/Gr33ndev/OpenAmpere/issues/451)), and the control starts such a wallbox again once ([#454](https://github.com/Gr33ndev/OpenAmpere/issues/454)). A diagnostics report or a control log taken after a cloud stopped the car (the wallbox state right after the stop and when the surplus is back) would answer it.

## Sources

- tinytuya (jasonacox/tinytuya, MIT): the protocol 3.5 (framing, session key, commands, UDP announcements); no code
  in OpenAmpere, see [NOTICE](../../../NOTICE).
- tuya-local (make-all/tuya-local, MIT): device definition of the dé EV charger (data points, states, steps).
- EV_charger (lachand/EV_charger, MIT): data points, scales, behaviour of the dé wallboxes.
- An owner's status reads of a dé portable wallbox, idle and charging three-phase, and a port check (2026-10).
- Code: `src/openampere/tuya.py`, `src/openampere/wallbox/de_tuya.py`, `src/openampere/wallbox/native.py`,
  simulator `python -m openampere.simulator --vendor de-tuya --port 6668 --plugged`.
