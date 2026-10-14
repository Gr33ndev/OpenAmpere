# Sales names and manufacturer models

> **Not official manufacturer information.** Compiled by the OpenAmpere contributors; can be incomplete or wrong. No
> affiliation with the manufacturers named here or with EKD, not legal advice. See [Device knowledge](../README.md).

**Applies to:** the names under which EKD sold storage systems, the manufacturer models behind them and the
OpenAmpere driver for each · **Sold as:** see the table · **OpenAmpere:** drivers `foxess` (register maps
`foxess_h3_new`, `foxess_h3_legacy`) and `saj` (register map `saj_h2`) · **Support:** display and control, see the
device pages · **Confirmed on a real device:** partly

All product and company names are trademarks of their owners and are used here only to describe which devices
OpenAmpere works with.

## Summary

- The systems EKD sold are standard hybrid inverters of FoxESS and SAJ; OpenAmpere talks to them directly with the
  same drivers as to devices from other sources.
- "Ampere.StoragePro E3" is a FoxESS H3; which of its two register maps the E3 uses is still open.
- The older "Ampere.StoragePro", including the "E2", is an SAJ of the H2/HS2 family; the E2 reports type code 0x005E.
- "Ampere.IQ" is EKD's app, which works only through EKD's servers; the "IQ box" or "smartbox" is the energy manager
  installed with the systems ([iq-box.md](iq-box.md)).
- On the systems sold by EKD, Modbus TCP is already turned on.

## Sales names

| Sales name | Manufacturer model | OpenAmpere | Unit id | Status | Source |
|---|---|---|---|---|---|
| "Ampere.StoragePro E3" | FoxESS H3 family (whether H3, H3 Smart or H3 Pro is not stated) | driver `foxess`, map detected (`foxess_h3_new` or `foxess_h3_legacy`) | 247 | confirmed | [#11](https://github.com/Gr33ndev/OpenAmpere/issues/11), [#311](https://github.com/Gr33ndev/OpenAmpere/issues/311); [foxess/h3.md](../foxess/h3.md) |
| "Ampere.StoragePro" (older systems) | SAJ H2/HS2 family hybrid inverter | driver `saj`, map `saj_h2` | 1 or 2 (depends on the communication module) | confirmed | [#330](https://github.com/Gr33ndev/OpenAmpere/issues/330), [#334](https://github.com/Gr33ndev/OpenAmpere/issues/334), [#336](https://github.com/Gr33ndev/OpenAmpere/issues/336); [saj/h2-family.md](../saj/h2-family.md) |
| "Ampere Storage Pro E2" | SAJ with type code 0x005E ("3P-X E2", three-phase) | driver `saj`, map `saj_h2` | 1 or 2 | confirmed | [#339](https://github.com/Gr33ndev/OpenAmpere/issues/339), [#378](https://github.com/Gr33ndev/OpenAmpere/issues/378), [#379](https://github.com/Gr33ndev/OpenAmpere/issues/379); [saj/0x005e-3p-x-e2.md](../saj/0x005e-3p-x-e2.md) |
| "Ampere.IQ" | EKD's app; works only through EKD's servers | no driver; history import through EKD's customer API | – | derived | [README](../../../README.md) |
| "IQ box", "smartbox" | the energy manager installed with EKD systems | no driver; OpenAmpere detects its effects | – | derived | [iq-box.md](iq-box.md) |

## Manufacturer model names

FoxESS H3 family, as the driver tells them apart by the model string:

| Model | Model string | Register map | Status | Source |
|---|---|---|---|---|
| H3 (classic) | other H3 strings | `foxess_h3_new` or `foxess_h3_legacy`, detected | derived | `drivers/foxess/driver.py`; [docs/devices.md](../../devices.md) |
| H3 Smart | `H3-…-Smart` or `H3-…-M` | `foxess_h3_new` | derived | as above |
| H3 Pro | `H3-Pro-…` or `P3-Pro-…` | `foxess_h3_new` | derived | as above |

SAJ type codes (register 0x8F00 block), as the driver names them:

| Type code | Name | Control in OpenAmpere | Status | Source |
|---|---|---|---|---|
| 0x0055 | AS2 3–6K | read only | derived | `drivers/saj/registers.py` |
| 0x0056 | AS2 5–10K | read only | derived | as above |
| 0x005A | H2 3–6K S2 | yes | derived | as above |
| 0x005B | H2 5–10K S3 | yes | derived | as above |
| 0x005C | HS2 3–6K S2 | yes | derived | as above |
| 0x005D | HS2 5–10K S3 | yes | derived | as above |
| 0x005E | "3P-X E2" (not in SAJ's protocol) | yes | confirmed (reported by owners of an E2) | [#339](https://github.com/Gr33ndev/OpenAmpere/issues/339), [#379](https://github.com/Gr33ndev/OpenAmpere/issues/379) |

Other codes in the SAJ family range 0x0050–0x006F are read with the same map and a warning in the log
([docs/devices.md](../../devices.md)).

## Behaviour

| Fact | Status | Source |
|---|---|---|
| On the storage systems sold by EKD, Modbus TCP is already turned on; owners do not need to switch it on. | derived | [#84](https://github.com/Gr33ndev/OpenAmpere/issues/84), [#85](https://github.com/Gr33ndev/OpenAmpere/issues/85); [README](../../../README.md) |
| EKD stands for Energiekonzepte Deutschland GmbH. It and other companies of its group filed for insolvency in early October 2026; the proceedings of seven companies run at the local court of Leipzig. | documented (public insolvency announcements) | [#85](https://github.com/Gr33ndev/OpenAmpere/issues/85), [#91](https://github.com/Gr33ndev/OpenAmpere/issues/91), [#92](https://github.com/Gr33ndev/OpenAmpere/issues/92); [README](../../../README.md) |

## Decisions in OpenAmpere

| Decision | Why | Issue/PR |
|---|---|---|
| The model string is only a hint; the driver checks which register map really answers and prefers the newer one. Owners can choose the map by hand. | Sales names and model strings do not tell the firmware's register map. | [docs/devices.md](../../devices.md) |
| Detection does not depend on a sales name: FoxESS at unit 247 first, then SAJ at 1 and 2 ([modbus.md](modbus.md)). | The same devices are sold under several names. | [docs/devices.md](../../devices.md) |
| App texts call the box the "former smartbox", without a manufacturer name; the README names companies only in the background and no-affiliation notes. | Names only descriptively. | [#53](https://github.com/Gr33ndev/OpenAmpere/issues/53) |

## Open questions

- Which register map (newer or older firmware) the "Ampere.StoragePro E3" uses, and which H3 variant it is. The E3
  is a FoxESS H3 ([#311](https://github.com/Gr33ndev/OpenAmpere/issues/311)), but its map is still open in
  [registers.md](../../registers.md) ([#185](https://github.com/Gr33ndev/OpenAmpere/issues/185)).
- Who made the IQ box is not stated in OpenAmpere's sources.
- Whether every system sold by EKD has Modbus TCP turned on is known only from owners' reports, not from a document.

## Sources

- Issues and pull requests: [#11](https://github.com/Gr33ndev/OpenAmpere/issues/11),
  [#53](https://github.com/Gr33ndev/OpenAmpere/issues/53), [#84](https://github.com/Gr33ndev/OpenAmpere/issues/84),
  [#85](https://github.com/Gr33ndev/OpenAmpere/issues/85), [#91](https://github.com/Gr33ndev/OpenAmpere/issues/91),
  [#92](https://github.com/Gr33ndev/OpenAmpere/issues/92), [#185](https://github.com/Gr33ndev/OpenAmpere/issues/185),
  [#311](https://github.com/Gr33ndev/OpenAmpere/issues/311), [#330](https://github.com/Gr33ndev/OpenAmpere/issues/330),
  [#334](https://github.com/Gr33ndev/OpenAmpere/issues/334), [#336](https://github.com/Gr33ndev/OpenAmpere/issues/336),
  [#339](https://github.com/Gr33ndev/OpenAmpere/issues/339), [#378](https://github.com/Gr33ndev/OpenAmpere/issues/378),
  [#379](https://github.com/Gr33ndev/OpenAmpere/issues/379).
- Public insolvency announcements (court of Leipzig), linked from the [README](../../../README.md).
- Code: `src/openampere/drivers/registry.py`, `src/openampere/drivers/foxess/driver.py`,
  `src/openampere/drivers/saj/registers.py`.
