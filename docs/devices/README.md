# Device knowledge

What OpenAmpere knows about the devices it talks to: registers, behaviour, differences between types and firmware,
and how OpenAmpere treats them. Every fact says how sure it is and where it comes from, so nobody has to search
through hundreds of issues again. The website shows these pages too, linked from the app
([Device knowledge](https://gr33ndev.github.io/OpenAmpere/en/docs/devices/),
[#512](https://github.com/Gr33ndev/OpenAmpere/issues/512)), built from `main`, so they can describe behaviour that is not
released yet. Keep links relative; the build reports a link to a file or heading that does not exist.

> **Not official manufacturer information.** These pages are compiled by the OpenAmpere contributors from public
> manufacturer documents, community projects and reports of owners. They can be incomplete or wrong. Changing
> settings of an inverter can affect its warranty and the rules of the grid connection. OpenAmpere has no
> affiliation with the manufacturers named here or with EKD; names are used only to describe compatibility.
> Nothing here is legal advice.

## Pages

| Device | Page |
|---|---|
| FoxESS H3, H3 Smart, H3 Pro (sold as "Ampere.StoragePro E3") | [foxess/h3.md](foxess/h3.md) |
| FoxESS H3, register map of newer firmware | [foxess/h3-new-map.md](foxess/h3-new-map.md) |
| FoxESS H3, register map of older firmware | [foxess/h3-legacy-map.md](foxess/h3-legacy-map.md) |
| FoxESS AC EV Charger (read-only wallbox check) | [foxess/ac-charger.md](foxess/ac-charger.md) |
| Portable dé wallbox over Tuya LAN (display and control) | [tuya/de-portable.md](tuya/de-portable.md) |
| Weidmüller AC SMART (sold as "AMPERE.Charge", display only) | [weidmueller/ac-smart.md](weidmueller/ac-smart.md) |
| SAJ H2, HS2, AS2 (sold as "Ampere.StoragePro") | [saj/h2-family.md](saj/h2-family.md) |
| SAJ type code 0x005E, "3P-X E2" | [saj/0x005e-3p-x-e2.md](saj/0x005e-3p-x-e2.md) |
| Solarmax MAX.STORAGE (sold as "AMPERE.Storage", display only) | [solarmax/max-storage.md](solarmax/max-storage.md) |
| M-TEC Energy-Butler GEN3 (display only) | [mtec/energy-butler.md](mtec/energy-butler.md) |
| my-PV AC ELWA heating rod | [mypv/elwa.md](mypv/elwa.md) |
| Netze BW meter portal | [gridmeter/netze-bw.md](gridmeter/netze-bw.md) |
| Cars of the Volkswagen group through the EU Data Act portal | [cars/vw-eu-data-act.md](cars/vw-eu-data-act.md) |
| Previous EKD smartbox ("IQ box") and the EKD cloud | [topics/iq-box.md](topics/iq-box.md) |
| Sales names and manufacturer models, hardware sold by EKD since 2018 | [topics/names.md](topics/names.md) |
| Modbus connections, proxies and what all inverters share | [topics/modbus.md](topics/modbus.md) |
| Relays, smart plugs, SG Ready | [topics/consumers.md](topics/consumers.md) |
| Wallboxes through evcc, OCPP and directly | [topics/wallboxes.md](topics/wallboxes.md) |

[devices.md](../devices.md) is the short support overview, [registers.md](../registers.md) an index of the register
tables on these pages.

## Status of a fact

| Status | Meaning |
|---|---|
| documented | In a document of the manufacturer (protocol, manual, datasheet). |
| derived | From another project or the community, not checked on a device by us. |
| confirmed | Seen on a real device: a diagnostics report, an owner's test or observation. |
| open | Unverified, an assumption, contradicted or still to check. Says what is missing. |

A fact can move up when new evidence arrives; then its source gets the new evidence added, the old one stays.
Decisions of OpenAmpere (how it treats a device, and why) are not facts: they are listed with a link to the issue or
pull request that made them.

## Sources

- **Allowed:** public manufacturer documents (named, not copied into the repository), projects with a compatible
  license (credited in [NOTICE](../../NOTICE)), diagnostics reports and tests in our issues, and owners' reports.
- **Not public, but handed to owners** (e.g. a protocol the manufacturer's support sends on request, marked "all
  rights reserved"): only the facts (register addresses, meanings, units), in own words. The document is named, but
  never quoted, never added to the repository and never passed on.
- **Not allowed:** documents under NDA or meant only for partners, anything from the Ampere.IQ app or its decompiled
  code, projects whose license does not allow reuse.
- **In own words:** register addresses and their meaning are facts; text, tables and the structure of a manufacturer
  document are not copied. Only the registers OpenAmpere needs, in our own layout.
- **How to cite:** an issue or pull request of OpenAmpere as a link, e.g. [#334](https://github.com/Gr33ndev/OpenAmpere/issues/334);
  another project as `owner/repo` (with `owner/repo#N` for its issues) and, where it matters, the commit; a document
  by its title and date.
- **Private sources** (e.g. a message or screenshot an owner sent the maintainer) are described, never linked:
  "an owner's report, 2026-10". Ask the owner whether the information may be used, without their name.

## Privacy

Nothing that identifies a person or an installation: no names, addresses, serial numbers (also shortened ones), host
names, exact system size (kWp, kWh) or rated power of an owner's system, the exact model and firmware of an owner's
device, energy figures, dates and times of an owner's actions or outages. Settings of an owner's device are not
quoted either; "matched the manufacturer's app" is enough. Round example values ("about 10 kW", "a FoxESS H3") are
fine.

## Keeping it current

New knowledge about a device goes onto its page in the same pull request as the code or the issue that brought it:
a new register, a behaviour seen on a device, a confirmation, a correction. Add the status and the source; mark
facts that turned out wrong as such instead of deleting them silently.

The short status (supported, confirmed, planned) of every device, and the list of what EKD sold, is kept in
[support.json](support.json) ([#526](https://github.com/Gr33ndev/OpenAmpere/issues/526)): the READMEs,
[topics/names.md](topics/names.md) and the website are generated from it. When a confirmation on a device page changes
that status, change it there too and run `node scripts/device-support.mjs`.

## Layout of a page

```markdown
# <Device>

> Not official manufacturer information … (the note from this README, shortened, with a link to it)

**Applies to:** … · **Sold as:** … · **OpenAmpere:** driver `…`, register map `…` · **Support:** display/control ·
**Confirmed on a real device:** yes/no/partly

## Summary
3–6 bullets: what is special about this device.

## Registers
| Register | Meaning | Type, scale, sign | R/W | Status | Source |

## Behaviour
| Fact | Status | Source |

## Decisions in OpenAmpere
| Decision | Why | Issue/PR |

## Open questions
- … (source)

## Sources
Documents, projects (license) and issues used on this page.
```
