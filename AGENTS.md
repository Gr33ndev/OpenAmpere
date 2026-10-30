# AGENTS.md

Guidance for AI coding agents (and humans in a hurry) working on OpenAmpere. Read this before changing code.
`CLAUDE.md` only points here. Details live in [CONTRIBUTING.md](CONTRIBUTING.md); this file is the short version plus
the things that are easy to get wrong.

## What this is

OpenAmpere is a local, cloud-free web app for solar systems with battery storage (FoxESS H3, SAJ H2/HS2). It talks
Modbus TCP to the inverter in the home network, stores data in SQLite and serves a React web app. It started as a
rescue tool for owners of systems sold by the insolvent German company EKD (app "Ampere.IQ"). Users are mostly
**non-technical German owners**; the app and the user guides are German, the repository is English.

**It can write to inverters.** Mistakes can affect devices, warranties or grid connection rules. Treat anything that
writes as safety-relevant (see below).

## Layout

| Path | What |
|---|---|
| `src/openampere/` | Python backend (FastAPI): `api.py` (HTTP API, auth, error translation), `collector.py` (polling loop), `runtime.py`, `config.py`, `storage.py` (SQLite), `control.py` (battery settings, export limit), `charging.py` (grid charging via remote control), `diagnostics.py`, `outages.py`, `notify.py` (ntfy), `remote.py` (Tailscale), `updates.py`, `i18n.py` (server messages) |
| `src/openampere/drivers/` | Inverter drivers: `base.py` (`Snapshot`, protocol), `registry.py` (detection), `foxess/`, `saj/`, `mypv.py`. See [docs/writing-a-driver.md](docs/writing-a-driver.md) |
| `src/openampere/simulator.py` | Modbus simulator (FoxESS new/legacy map, SAJ, my-PV) for development and tests |
| `src/openampere/locales/` | Server messages: `de.json` (reference) and translations, English keys per module |
| `web/` | React + Vite + TypeScript web app; `web/src/demo/` is an in-browser fake server for the public demo |
| `web/src/locales/<lang>/` | App texts: `<area>.json` per area plus `meta.json`; `web/src/i18n.tsx` has `t()`/`tx()` |
| `site/` | Project website: `pages/*.html` templates, `locales/<lang>/`, built by `scripts/build-site.sh` |
| `custom_components/openampere/` | Home Assistant integration (HACS); tests in `tests_ha/` |
| `ha-app/`, `repository.yaml` | Home Assistant app (#300): config for the Supervisor, start script of the image stage `ha-app`, `DOCS.md` (the English guide shown in Home Assistant; German: `docs/homeassistant-app.de.md`) |
| `scripts/` | `install.sh` (end-user installer), `updater.sh` / `tailscale.sh` (helper containers), `release.sh`, `deps.sh`, `i18n_messages.py`, … |
| `tests/` | pytest suite for the backend |
| `docs/` | `architecture.md`, `devices/` (what we know per device, with status and source), `devices.md`, `writing-a-driver.md`; German user guides are `*.de.md` |

## Commands

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"   # backend dev setup (Python 3.11+)
.venv/bin/pytest                                             # backend tests (about 2 minutes)
.venv/bin/ruff check .                                       # Python code style (PEP 8 via ruff)
.venv/bin/python -m openampere.simulator --port 5020 --speed 20
OPENAMPERE_INVERTER_HOST=127.0.0.1 OPENAMPERE_INVERTER_PORT=5020 OPENAMPERE_SERVER_PORT=8089 .venv/bin/python -m openampere
cd web && npm ci && npm run dev                              # web app with live reload, proxies the API on 8089
cd web && npm run lint                                       # TypeScript code style (Biome)
cd web && npm run build                                      # tsc + build into src/openampere/web/
cd web && npm run check:demo                                 # every API endpoint the app uses has a demo answer
cd web && npm run check:i18n                                 # translation keys, placeholders, plurals, coverage
node scripts/device-support.mjs                              # after editing docs/devices/support.json: README and names.md tables
cd web && npm run test:ui                                    # UI tests (Playwright, after npm run build; once: npx playwright install chromium webkit)
scripts/build-site.sh                                        # website + demo into _site/ (needs web/node_modules)
scripts/i18n_messages.py --missing                           # server messages without a key in locales/de.json
scripts/deps.sh                                              # after dependency changes: lockfiles + license list (uv)
```

CI runs: `backend` (ruff, pytest + license list), `web` (lint, build, check:demo, check:i18n, check:devices), `ui` (Playwright), `docker`, `homeassistant`,
`hassfest`, `hacs`, and `check` (Conventional Commits for the PR title and every commit).
`AI review` (not required) comments on pull requests from this repository following `.github/claude-review.md`;
when a rule here changes, check whether that file needs the change too.

**A pull request is done when the AI review finds nothing more, not when CI is green.** After opening a pull request
(or pushing to it), watch it in a loop: wait for the `AI review` run on the latest commit, read its comment, fix every
finding that holds, push, and wait for the next review. A finding that does not hold gets an answer with the reason in
the pull request; it then counts as handled. Stop only when every finding of the review of the latest commit is fixed
or answered and CI is green. If no review runs (draft, fork or dependency-workflow pull request, or a failed `review`
check: remove the label `ai-review` and add it again, once), say so instead of counting the pull request as done.

## Rules

- **Issue first, then PR.** Small PRs, linked with `Fixes #123`. PRs are squash-merged.
- **Conventional Commits** for every commit and the PR title (`feat(scope): …`, `fix: …`, `docs: …`), English,
  imperative, lowercase. CI rejects anything else, also intermediate commits like `wip:` – squash before pushing.
  The body explains why.
- **Languages:** code, comments, commits, issues, docs in English. Every text in the app goes through `t("area.component.name")`
  with German in `web/src/locales/de/` (reference) and English in `web/src/locales/en/`. Never hard-code UI text,
  never split a sentence into several keys (use `tx()` with placeholders for links/bold), never use `"de-DE"` directly
  (use `LOCALE`). Same German word with a different meaning gets separate keys. Server error messages stay German in
  the code and need a key in `src/openampere/locales/de.json`. Issues in German are welcome.
- **Texts for non-technical users:** complete German sentences, say what to do, no jargon without explanation.
- **Everything configurable in the app**, not only in config files.
- **Demo:** a new API endpoint the app reads needs an answer in `web/src/demo/server.ts` (or a write-only/not-needed
  entry).
- **Device knowledge** lives in `docs/devices/` (one page per device family or special type, `topics/` across
  devices). A new register, a behaviour seen on a device, a confirmation or a correction goes onto that page in the
  same PR, with status (documented, derived, confirmed, open) and source; decisions link their issue or PR. Read its
  README first: not official information, own words, no NDA material, private sources described but never linked.
  The website shows these pages (`scripts/markdown.mjs`, #512) and the app links to them.
- **Support status in one place:** which devices are supported and confirmed, and what EKD sold, lives only in
  `docs/devices/support.json` (#526). After changing it run `node scripts/device-support.mjs`; it writes the tables in
  README.md, README.de.md and `docs/devices/topics/names.md` (between `generated:` markers, never edit those by hand),
  and the website renders the EKD device page and the home page list from it.
- **Tests:** new logic gets tests, bug fixes a test that fails without the fix. Tests with the simulator must stop the
  collector in a `finally` block, otherwise the test run hangs. A UI bug gets a test in `web/ui-tests/`; a new page goes
  into `PAGES` in `web/ui-tests/fixtures.ts`.
- **Every source file starts with two SPDX lines** (copyright "the OpenAmpere contributors", license MIT), see
  [CONTRIBUTING.md](CONTRIBUTING.md#code-rules); `tests/test_source_headers.py` fails without them.
- **No new dependencies** without agreeing in the issue (they also change the license list).
- **No personal data** in code, tests, docs, issues, PRs, commits or screenshots: names, e-mail and postal addresses,
  locations, IP and MAC addresses, host and tailnet names, serial numbers, meter, customer and Marktstammdatenregister
  numbers, keys, passwords, tokens. **No links to private chats, tickets or sessions.**
- **Nothing that identifies an installation** either: exact system size (kWp, battery kWh), commissioning date,
  roof orientation, exact model and firmware of a user's device, real energy figures, dates and times of a user's
  actions or outages. Many plants can be found in the public Marktstammdatenregister by size and date. Use round
  example values ("about 10 kWp", "a FoxESS H3", "at night").
- **Everything OpenAmpere exports for a report** (diagnostics report, device query, wallbox check, …) is masked
  through `src/openampere/privacy.py`: serial numbers shortened, addresses and host names pseudonymised, times as
  durations, private register values removed. Use it for a new export; never write a second masking.
- **Reports, logs and screenshots from users** (diagnostics, control log, app screenshots) contain such data, e.g.
  the IP address in connection errors, the shortened serial number, model, firmware and timestamps. Quote only what
  the issue needs (usually register values) and check the text before posting.
- **Legal/trademarks:** no code, graphics or texts from the Ampere.IQ app. Name companies and products only
  descriptively. Keep the "no affiliation" and "not legal advice" notes intact when editing README or website.

## Three ways to run OpenAmpere

Every change reaches owners in three ways, all built from the same release (#300). Think of all three:

1. **Standalone with Docker** (`install.sh`): the default image, update and Tailscale helper containers.
2. **Home Assistant app** (`ha-app/`, image stage `ha-app`): the same code in "app mode" (`ha_app.active()`,
   `SUPERVISOR_TOKEN`). Opened in the Home Assistant sidebar (ingress) and by IP on another port; no helper containers,
   updates come from Home Assistant.
3. **Home Assistant integration** (`custom_components/openampere/`): installed through HACS for way 1, brought along
   and installed by the app for way 2 (same version). It is only a client: it works only while an OpenAmpere server
   runs (way 1 or 2) and talks to its external API, never to the inverter. Nothing the integration offers may bypass
   the checks of the server.

- **Web app:** only paths relative to the page (`appUrl()`, no `"/…"` in `fetch`, `href` or `src`); the app runs under
  the ingress address `/api/hassio_ingress/<token>/`. `window.location` is the address of Home Assistant there; use
  `status.home_assistant` for the direct address. `web/ui-tests/ingress.spec.ts` covers this.
- **Backend:** a feature that needs a helper container, the host or Docker does not exist in the app; say so in the
  app (as updates and remote access do). Through the sidebar only **admins of Home Assistant** count as logged in
  (`ha_admin`): `panel_admin` only hides the sidebar entry, the Supervisor forwards every user. Never trust a
  forwarded header for the sidebar itself. New files in the data folder end up in Home Assistant backups.
- **Root in the app:** only `ha-app/start.sh` (and `openampere ha-integration` from it) runs as root; it must not read
  or write anything in `/data` or follow paths from the config, which belong to the app user. Everything else runs
  through `setpriv` as the app user, in the AppArmor profile "server" without the config folder of Home Assistant.
- **Integration and discovery:** a discovery must never change the certificate of an existing entry (that would send
  its token elsewhere); a new certificate means pairing again.
- **Home Assistant:** new values or controls the integration should offer go into `custom_components/` with tests in
  `tests_ha/`; the integration talks to the external API only. Both ways get the change with the next release.
- **App config:** new rights, ports, capabilities or file access go into `ha-app/config.yaml` and, if needed,
  `ha-app/apparmor.txt` (the profile limits capabilities and network types). `tests/test_ha_app_config.py` checks the
  config against the code.
- **Docs:** the installation is described per way (README, README.de, website): way 1 with "Connect Home Assistant"
  (HACS), way 2 with "Sensors and control in Home Assistant" (no HACS). The app guide is `ha-app/DOCS.md` (English,
  shown in Home Assistant) and `docs/homeassistant-app.de.md`.

## Writing to the inverter (safety)

- Writes only through the control features: master switch (`control.enabled`) and test mode (`control.dry_run`)
  respected, `supports_control` of the device checked, limits checked before writing, value read back afterwards,
  every change in the control log.
- Continuous control (e.g. grid charging) only via the inverter's remote control with a watchdog timeout, never via
  permanently stored registers. Release only what OpenAmpere started (`_remote_owned`; after a restart the
  `remote_command` meta plus its own register values, #141). The one exception is a device without such a remote
  control (SAJ, #377): a short time slot of the inverter that ends by itself and that OpenAmpere extends, with the
  owner's values kept in the `remote_command` meta and put back, and a note in the app how long charging goes on.
- The FoxESS AC EV Charger (#465) is the exception to "watchdog fails safe": its watchdog charges on with a fallback
  current when the controller is gone (behaviour and registers in `docs/devices/foxess/ac-charger.md`). Real control
  only per wallbox after the owner agreed in the app (the question says so), in its work mode "controlled"; the
  fallback current set once to 6 A; the current written again every `heartbeat_s` only while the car charges, never
  after a stop (see the device page); the current written before start or stop; a charge OpenAmpere started stopped on
  a planned shutdown and when it lets go, also while the charger still reports "starting".
- Switching off is the safe direction and is written even with the master switch off or in test mode: releasing the
  remote control, 0 W to a heating rod and "off" to a relay OpenAmpere switched on (#217, #243), and setting a backup
  reserve raised by 0.23.1 back to the owner's value (#320; only to a device with `supports_control` and within the
  current limits, #350). Within 10 minutes after a new connection, a remote command with enable 1 or 5 (#509) and
  OpenAmpere's timeout (180 s) counts as OpenAmpere's own (the inverter brings back its values after a restart, #143),
  so another controller using exactly these values would be switched off as well. Keep both when changing the release
  logic.
- New registers need a source: manufacturer docs, a project with a compatible license (credit in `NOTICE` and on the
  device page in `docs/devices/`) or a diagnostics report from a real device.
- Legal topics (export limit, § 14a EnWG, EEG) are settled in the issue before code. Label such issues `safety`.

## Security advisories

A vulnerability is fixed only in the private fork of its advisory and published after the release:
[docs/security-advisories.md](docs/security-advisories.md). Easy to miss: one advisory and one fork per
vulnerability, tests in a new file per advisory, and the fix commit's message ends with the advisory id on a line of
its own (otherwise the changelog lists it as "Merge commit from fork").

## Things that are easy to get wrong

- **Merging as admin skips the required checks.** The `main` ruleset has an admin bypass (needed by `release.sh`).
  `gh pr merge --auto` run with admin rights merges immediately, before CI has finished. Enable auto-merge through the
  GraphQL `enablePullRequestAutoMerge` mutation or the web UI and check that the PR is still open and waiting.
- **Data files of the Python package** (e.g. `src/openampere/locales/*.json`) must be listed under
  `[tool.setuptools.package-data]` in `pyproject.toml`, otherwise they are missing in the Docker image.
- **Database changes** go into `storage.py` as a new step in `MIGRATIONS` plus `SCHEMA_VERSION + 1`; never edit
  `SCHEMA` (it is version 1). Before the upgrade the database is copied, so a rolled-back version can continue
  with the copy. Test the step with an existing database, not only with a new one.
- **Helper scripts reach existing installations only through `install.sh`.** `updater.sh` and `tailscale.sh` are
  copied by the installer; users must run it again to get changes. The website serves the scripts of the latest
  release tag, not of `main`.
- **The update helper verifies image provenance** with cosign against `.github/workflows/release.yml`; renaming that
  workflow or the repository breaks updates. The check (and `gh attestation verify --owner Gr33ndev`) trusts the
  names `Gr33ndev` and `OpenAmpere`, not a fixed account or repository ID: never rename, transfer or delete the account
  or the repository, or someone who registers a freed name can sign images that pass (see GOVERNANCE.md).
- **The Website workflow runs one deployment at a time** (`concurrency: pages`, not cancelling a running one, #398);
  a queued run replaced by a newer one shows as "cancelled" on `main`, which is normal.
- **Release:** maintainers only, from a clean `main`: `scripts/release.sh X.Y.Z` then
  `git push origin vX.Y.Z`, wait for the "Release" workflow, then `git push origin main` (Home Assistant offers the
  app version in `ha-app/config.yaml` as soon as `main` has it, before that the image must exist). The commit titles since the last tag become the version's entry in
  `web/public/changelog.json` (`feat` → "Neu", `fix` → "Behoben"): the changelog in the app, on the website, the
  GitHub release notes and `ha-app/CHANGELOG.md` (shown by Home Assistant with an update of the app; written by
  `scripts/changelog.py` together with the JSON file, never by hand). Good commit titles are what owners read. An optional summary for owners goes into
  `changelog/X.Y.Z.json` (`{"de": "…", "en": "…"}`, German required) before running `release.sh`; it is shown
  above the list. It is the maintainer's text: don't write one unless asked.
- **Agent worktrees** (e.g. `.claude/worktrees/`) must never be committed; `release.sh` refuses an unclean tree.
- **The demo is the website:** UI changes show up in the public demo with the next website build.

## Where to look

- Contributing, i18n details, adding a language: [CONTRIBUTING.md](CONTRIBUTING.md)
- Architecture: [docs/architecture.md](docs/architecture.md), devices and registers: [docs/devices/](docs/devices/README.md)
- New devices: [docs/writing-a-driver.md](docs/writing-a-driver.md), supported devices: [docs/devices.md](docs/devices.md)
- Governance and decisions: [GOVERNANCE.md](GOVERNANCE.md), security reports: [SECURITY.md](SECURITY.md)
