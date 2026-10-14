# my-PV AC ELWA heating rods

> **Not official manufacturer information.** Compiled by the OpenAmpere contributors; can be incomplete or wrong. No
> affiliation with my-PV, not legal advice. See [Device knowledge](../README.md).

**Applies to:** my-PV AC ELWA-E, AC ELWA 2, AC THOR · **Sold as:** – · **OpenAmpere:** consumer type "my-PV heating
rod", `src/openampere/drivers/mypv.py` (`MyPvHeatingRod`) · **Support:** display and control (power setpoint only) ·
**Confirmed on a real device:** no (simulator only)

## Summary

- Modbus TCP, port 502, unit id 1 by default. OpenAmpere reads four registers (1000–1003) and writes only one: the
  power setpoint 1000.
- The rod has its own control timeout: without a new setpoint it stops heating. The control type in its web interface
  must be "Modbus TCP".
- The manufacturer asks not to write other registers frequently (non-volatile memory).
- The rod's real power is not read; OpenAmpere estimates it from the setpoint and the status.
- Before the first write OpenAmpere checks that the device really looks like a my-PV rod, so a wrong address can never
  hit the inverter.

## Registers

| Register | Meaning | Type, scale, sign | R/W | Status | Source |
|---|---|---|---|---|---|
| 1000 | Power setpoint | U16, W | R/W (holding, FC03 read, FC06 write) | documented | my-PV Modbus TCP description; [#238](https://github.com/Gr33ndev/OpenAmpere/issues/238), [#241](https://github.com/Gr33ndev/OpenAmpere/issues/241) |
| 1001 | Water temperature | S16 (two's complement), 0.1 °C, e.g. 0xFFF6 = −1.0 °C | R | documented | my-PV Modbus TCP description; [#241](https://github.com/Gr33ndev/OpenAmpere/issues/241) |
| 1002 | Target temperature | S16 (two's complement), 0.1 °C | R | documented | my-PV Modbus TCP description; [#241](https://github.com/Gr33ndev/OpenAmpere/issues/241) |
| 1003 | Status, see below | U16, code | R | documented | my-PV Modbus TCP description; [#238](https://github.com/Gr33ndev/OpenAmpere/issues/238) |

Status codes of register 1003:

| Code | Meaning |
|---|---|
| 1 | not controlled |
| 2 | heating |
| 3 | standby |
| 4 | boost heating |
| 5 | water has reached the target temperature |
| 9 | setup |
| 20 | legionella protection |
| 21 | switched off |
| 22 | locked |
| 200 and up | fault (the value is the fault code) |

## Behaviour

| Fact | Status | Source |
|---|---|---|
| Connection: Modbus TCP, port 502, unit id 1 (configurable). | documented | my-PV Modbus TCP description; [docs/devices.md](../../devices.md) |
| The rod follows Modbus setpoints only while its control type is set to "Modbus TCP" in its web interface. A read that the rod rejects usually means another control type is set. | documented (the hint about rejected reads: derived) | my-PV Modbus TCP description; `mypv.py` |
| The rod has a control timeout set in its web interface: when no new setpoint arrives in time, it stops heating by itself. It has to be a little longer than OpenAmpere's interval. | documented | my-PV Modbus TCP description; [#243](https://github.com/Gr33ndev/OpenAmpere/issues/243) |
| The manufacturer asks not to write any other register frequently, because they are kept in non-volatile memory. | documented | my-PV Modbus TCP description; [NOTICE](../../../NOTICE) |
| The registers 1000–1003 do not contain the power the rod really takes; only the setpoint. | derived | [#238](https://github.com/Gr33ndev/OpenAmpere/issues/238) |

## Decisions in OpenAmpere

| Decision | Why | Issue/PR |
|---|---|---|
| Read 1000–1003 in one FC03 request; write only 1000, clamped to 0–65000 W, continuously by solar surplus (optionally with cheap grid power). | Only the documented registers; the manufacturer asks not to write others. | [#238](https://github.com/Gr33ndev/OpenAmpere/issues/238), [#241](https://github.com/Gr33ndev/OpenAmpere/issues/241), [#262](https://github.com/Gr33ndev/OpenAmpere/issues/262) |
| Estimated power: the setpoint while the status is 2 or 4, or 3 with a setpoint above 0; otherwise 0. | The rod's power is not available in these registers. | [#241](https://github.com/Gr33ndev/OpenAmpere/issues/241) |
| A temperature of 0 counts as not available; status 200 and up is shown as a fault with its code. | 0 is what an unset value reads. | [#241](https://github.com/Gr33ndev/OpenAmpere/issues/241) |
| Before the first write the rod is read once; OpenAmpere writes only if the status is 1–999 and both temperatures are within −30 to 120 °C. | Register 1000 used to be written every few seconds to whatever address was entered; a wrong address (e.g. the inverter's) could be written outside its driver's checks. | [#259](https://github.com/Gr33ndev/OpenAmpere/issues/259), [#262](https://github.com/Gr33ndev/OpenAmpere/issues/262) |
| Only unit ids 1–247 are accepted; never 0. | Unit id 0 is the Modbus broadcast: behind a gateway every device would take the write. | [#259](https://github.com/Gr33ndev/OpenAmpere/issues/259), [#262](https://github.com/Gr33ndev/OpenAmpere/issues/262) |
| A rod cannot be saved at the inverter's host and port. The resolved addresses are compared, so a host name and its IP address, or `localhost` and `127.0.0.1`, count as the same device. | The inverter must never be configured as a heating rod. | [#262](https://github.com/Gr33ndev/OpenAmpere/issues/262), [#351](https://github.com/Gr33ndev/OpenAmpere/issues/351), [#363](https://github.com/Gr33ndev/OpenAmpere/issues/363) |
| 0 W is sent once to a rod OpenAmpere set to power when it is disabled, removed or changed, when control is switched off, when test mode is entered and when OpenAmpere stops; also with the master switch off or in test mode. | Switching off is the safe direction; the rod's own timeout is only a second safeguard. | [#217](https://github.com/Gr33ndev/OpenAmpere/issues/217), [#224](https://github.com/Gr33ndev/OpenAmpere/issues/224), [#243](https://github.com/Gr33ndev/OpenAmpere/issues/243), [#247](https://github.com/Gr33ndev/OpenAmpere/issues/247) |

Retries for an unreachable rod follow the rules for all consumers, see [consumers.md](../topics/consumers.md).

## Open questions

- No owner has run a my-PV rod with OpenAmpere yet; everything is tested against the simulator only. The website and
  README had wrongly shown it as tested
  ([#111](https://github.com/Gr33ndev/OpenAmpere/issues/111), [#173](https://github.com/Gr33ndev/OpenAmpere/issues/173),
  [#185](https://github.com/Gr33ndev/OpenAmpere/issues/185)).
- The power estimate (heating in status 2 and 4, and in 3 only with a setpoint) is OpenAmpere's reading of the status
  list; a real device may take power in other states or less than the setpoint.
- The title and date of the my-PV document behind the registers are not recorded in the repository; a later edit
  should add them.

## Sources

- my-PV's public Modbus TCP description for its heating rods (manufacturer document; facts only, see
  [NOTICE](../../../NOTICE)).
- Issues and pull requests: [#111](https://github.com/Gr33ndev/OpenAmpere/issues/111),
  [#173](https://github.com/Gr33ndev/OpenAmpere/issues/173), [#185](https://github.com/Gr33ndev/OpenAmpere/issues/185),
  [#217](https://github.com/Gr33ndev/OpenAmpere/issues/217), [#224](https://github.com/Gr33ndev/OpenAmpere/issues/224),
  [#238](https://github.com/Gr33ndev/OpenAmpere/issues/238), [#241](https://github.com/Gr33ndev/OpenAmpere/issues/241),
  [#243](https://github.com/Gr33ndev/OpenAmpere/issues/243), [#247](https://github.com/Gr33ndev/OpenAmpere/issues/247),
  [#259](https://github.com/Gr33ndev/OpenAmpere/issues/259), [#262](https://github.com/Gr33ndev/OpenAmpere/issues/262),
  [#351](https://github.com/Gr33ndev/OpenAmpere/issues/351), [#363](https://github.com/Gr33ndev/OpenAmpere/issues/363).
- Code: `src/openampere/drivers/mypv.py`, `src/openampere/consumers.py`; simulator `src/openampere/simulator.py`.
