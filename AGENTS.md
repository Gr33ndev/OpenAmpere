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
| `scripts/` | `install.sh` (end-user installer), `updater.sh` / `tailscale.sh` (helper containers), `release.sh`, `deps.sh`, `i18n_messages.py`, … |
| `tests/` | pytest suite for the backend |
| `docs/` | `architecture.md`, `registers.md`, `devices.md`, `writing-a-driver.md`; German user guides are `*.de.md` |

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
cd web && npm run test:ui                                    # UI tests (Playwright, after npm run build; once: npx playwright install chromium webkit)
scripts/build-site.sh                                        # website + demo into _site/ (needs web/node_modules)
scripts/i18n_messages.py --missing                           # server messages without a key in locales/de.json
scripts/deps.sh                                              # after dependency changes: lockfiles + license list (uv)
```

CI runs: `backend` (ruff, pytest + license list), `web` (lint, build, check:demo, check:i18n), `ui` (Playwright), `docker`, `homeassistant`,
`hassfest`, `hacs`, and `check` (Conventional Commits for the PR title and every commit).

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
- **Tests:** new logic gets tests, bug fixes a test that fails without the fix. Tests with the simulator must stop the
  collector in a `finally` block, otherwise the test run hangs. A UI bug gets a test in `web/ui-tests/`; a new page goes
  into `PAGES` in `web/ui-tests/fixtures.ts`.
- **No new dependencies** without agreeing in the issue (they also change the license list).
- **No personal data** in code, tests, docs, issues, PRs, commits or screenshots: names, e-mail and postal addresses,
  locations, IP and MAC addresses, host and tailnet names, serial numbers, meter, customer and Marktstammdatenregister
  numbers, keys, passwords, tokens. **No links to private chats, tickets or sessions.**
- **Nothing that identifies an installation** either: exact system size (kWp, battery kWh), commissioning date,
  roof orientation, exact model and firmware of a user's device, real energy figures, dates and times of a user's
  actions or outages. Many plants can be found in the public Marktstammdatenregister by size and date. Use round
  example values ("about 10 kWp", "a FoxESS H3", "at night").
- **Reports, logs and screenshots from users** (diagnostics, control log, app screenshots) contain such data, e.g.
  the IP address in connection errors, the shortened serial number, model, firmware and timestamps. Quote only what
  the issue needs (usually register values) and check the text before posting.
- **Legal/trademarks:** no code, graphics or texts from the Ampere.IQ app. Name companies and products only
  descriptively. Keep the "no affiliation" and "not legal advice" notes intact when editing README or website.

## Writing to the inverter (safety)

- Writes only through the control features: master switch (`control.enabled`) and test mode (`control.dry_run`)
  respected, `supports_control` of the device checked, limits checked before writing, value read back afterwards,
  every change in the control log.
- Continuous control (e.g. grid charging) only via the inverter's remote control with a watchdog timeout, never via
  permanently stored registers. Release only what OpenAmpere started (`_remote_owned`; after a restart the
  `remote_command` meta plus its own register values, #141).
- New registers need a source: manufacturer docs, a project with a compatible license (credit in `NOTICE` and
  `docs/registers.md`) or a diagnostics report from a real device.
- Legal topics (export limit, § 14a EnWG, EEG) are settled in the issue before code. Label such issues `safety`.

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
  workflow or the repository breaks updates.
- **The Website workflow cancels older runs** (`concurrency: pages`); a "cancelled" run on `main` is normal.
- **Release:** maintainers only, from a clean `main`: `scripts/release.sh X.Y.Z` then
  `git push origin main vX.Y.Z`. The commit titles since the last tag become the version's entry in
  `web/public/changelog.json` (`feat` → "Neu", `fix` → "Behoben"): the changelog in the app, on the website and the
  GitHub release notes. Good commit titles are what owners read. An optional summary for owners goes into
  `changelog/X.Y.Z.json` (`{"de": "…", "en": "…"}`, German required) before running `release.sh`; it is shown
  above the list. It is the maintainer's text: don't write one unless asked.
- **Agent worktrees** (e.g. `.claude/worktrees/`) must never be committed; `release.sh` refuses an unclean tree.
- **The demo is the website:** UI changes show up in the public demo with the next website build.

## Where to look

- Contributing, i18n details, adding a language: [CONTRIBUTING.md](CONTRIBUTING.md)
- Architecture: [docs/architecture.md](docs/architecture.md), registers: [docs/registers.md](docs/registers.md)
- New devices: [docs/writing-a-driver.md](docs/writing-a-driver.md), supported devices: [docs/devices.md](docs/devices.md)
- Governance and decisions: [GOVERNANCE.md](GOVERNANCE.md), security reports: [SECURITY.md](SECURITY.md)
