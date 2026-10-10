# Governance

Deutsch: OpenAmpere wird derzeit von einer Person betreut. Ideen, Fehlerberichte und Beiträge sind willkommen, auch auf Deutsch; entschieden wird offen in Issues und Pull Requests.

## Maintainer

OpenAmpere currently has one maintainer: [@Gr33ndev](https://github.com/Gr33ndev). The maintainer reviews and merges pull requests, makes releases and has the final say on what goes into the project.

## Roles and responsibilities

| Role | Who | Responsibilities |
|---|---|---|
| Maintainer | [@Gr33ndev](https://github.com/Gr33ndev) | Triages issues and answers questions; reviews and merges pull requests; makes the final decision; makes releases (signed tags with `scripts/release.sh`) and writes the release summary; keeps the repository settings, the website and the release workflow running; keeps the [roadmap](docs/roadmap.md) current. |
| Security contact | the maintainer | Receives private vulnerability reports and handles them as described in [SECURITY.md](SECURITY.md#how-a-report-is-handled). |
| Contributor | anyone | Reports bugs and ideas, sends device reports, translations and pull requests following [CONTRIBUTING.md](CONTRIBUTING.md). Contributors have no merge or release rights. |

## How decisions are made

- Everything starts as an issue, so the reasons are visible. Bigger changes are agreed on in the issue before a pull request is opened (see [CONTRIBUTING.md](CONTRIBUTING.md)).
- Anything that writes to an inverter is held to a higher standard: it must stay behind the control switch and the test mode, values are checked before writing and read back afterwards, and the registers must be documented or confirmed by a diagnostic report from a real device. When in doubt, a feature stays read-only.
- Users come first: OpenAmpere was started for owners of systems from the insolvent company EKD. The app and the user guides stay German and understandable for non-technical users.

## Becoming a maintainer

People who contribute regularly and carefully, for example drivers, reviews or helping others in issues, can be invited as maintainers. With a second maintainer, changes to drivers and control functions will need a review by someone other than the author.

## Code of Conduct

Everyone in the project follows the [Code of Conduct](CODE_OF_CONDUCT.md).
