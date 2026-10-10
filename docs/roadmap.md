# Roadmap

What OpenAmpere intends to do, and not to do, over the next year (as of October 2026). It shows the direction, not
promises or dates. Concrete work is planned in [issues](https://github.com/Gr33ndev/OpenAmpere/issues) and on the
[project board](https://github.com/users/Gr33ndev/projects/1); the maintainer updates this page when the direction
changes (see [GOVERNANCE.md](../GOVERNANCE.md)).

## Planned

- **Confirm the supported devices:** move the FoxESS variants, the SAJ H2/HS2, my-PV heaters and Shelly relays from
  "not yet confirmed" to "confirmed" with device reports from real systems ([devices.md](devices.md)).
- **Control for SAJ H2/HS2:** enable it once the driver has been checked on real devices.
- **More devices:** new drivers when owners or the community provide register documentation or diagnostics reports
  ([Writing a driver](writing-a-driver.md)).
- **Robustness:** reliable operation with Modbus proxies, connection drops, restarts and automatic updates, also on
  small hardware such as a Raspberry Pi.
- **Security and supply chain:** keep releases signed and dependencies pinned, follow the OpenSSF Best Practices and
  Scorecard recommendations.
- **Languages:** German stays the reference and English stays complete; more languages if someone maintains them.

## Not planned

- **No cloud:** no online service, no accounts, no telemetry. OpenAmpere works without internet.
- **No access from the internet without a VPN:** no port forwarding or public hosting of the app.
- **No unsafe writes:** no registers without a documented source or a report from a real device, no continuous
  control through permanently stored registers.
- **No legal advice:** OpenAmpere applies the export rule the owner selects; it does not decide which rules apply to a
  system.
- **No code, graphics or texts from the previous manufacturer app.**
- **No general home automation platform:** wallboxes, smart homes and dashboards are connected through evcc and Home
  Assistant instead.
