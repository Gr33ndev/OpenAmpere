# Contributing to OpenAmpere

Thanks for wanting to help! OpenAmpere depends on affected owners sharing their devices, bugs and ideas – no
programming skills needed.

## Languages

The repository is in English: code, comments, commits, issues and documentation. The app UI and all user-facing texts
stay German, because the app is made for German system owners: texts in the app must be German and understandable for
non-technical users. Issues and reports in German are welcome and are handled the same way as English ones; the
issue templates are bilingual.

## In short

1. **Issue first, then code.** For anything except typos, please open an issue first or use an existing one. That way
   we agree beforehand whether and how something gets done, and nobody works for nothing.
2. **Small pull requests.** One PR solves one problem. Two small ones are better than one big one.
3. **UI change = screenshots.** Before/after, light and dark, at phone width.
4. **Tests and build must pass.**
5. **No personal data** (IP addresses, serial numbers, API keys, passwords) in issues, code, screenshots or logs.

## Helping without code

- **Report a bug:** Choose the "Bug report / Fehler melden" template. The version is shown under **Mehr → Über** (More → About).
- **Report a device:** Create a report under **Mehr → Diagnose** (More → Diagnostics) and share it with the "Device
  report / Gerätebericht" template. This is the most important help for supporting more inverters and firmware versions. The
  diagnostics only read and change nothing on the device. How a report becomes a driver or a register fix is described
  in [Writing a driver](docs/writing-a-driver.md#from-a-diagnostics-report-to-a-register-map-and-a-test).
- **Ideas:** "Feature request / Funktionswunsch" template. Above all, describe the problem, not just the solution.
- **Texts and wording:** Unclear wording in the app is a bug – please report it.
- **Translations:** The app can show other languages besides German (**Mehr → Darstellung → Sprache**, More →
  Appearance → Language). English is the first one and still incomplete. See [Translating the app](#translating-the-app).

## Translating the app

German is the source language. Every text in the web app is written in German and wrapped in `t("…")` from
`web/src/i18n.ts`; the German text is also the key. Translations live in `web/src/locales/<language>/<area>.json` (one file per area of the app) as
`"German text": "translation"`. A text without a translation simply stays German, so pages can be translated one at a
time.

- Placeholders are written as `{name}` and must appear in the translation too: `t("Version {version} ist da", { version })`.
- Only plain string literals in `t("…")`, no template strings, so tools can find them. For a choice, call `t()` for each
  text: `enabled ? t("Aktiv") : t("Nur ansehen")`.
- `npm run check:i18n` in `web/` (also run by CI) fails if a translation belongs to a German text that no longer exists
  or if placeholders differ, and prints how much is translated.
- New texts in the app: write them in German and wrap them in `t()`. Adding the English translation in the same PR is
  welcome but not required.
- Numbers, dates and times follow the language: use `LOCALE` from `i18n.ts` (or the helpers in `format.ts`), never a
  fixed `"de-DE"`.
- A new language: add a folder `web/src/locales/<code>/`, register it in `DICTIONARIES` and `LANGUAGES` in `i18n.ts`.

**Security vulnerabilities** must never be reported as a public issue; follow [SECURITY.md](SECURITY.md) instead.

## Workflow for code contributions

1. Open an issue or comment that you would like to take it on. For larger changes, wait for brief feedback on the
   approach.
2. Fork the repository and create a branch (`fix/…`, `feature/…`, `docs/…`).
3. Make the change, add tests, check locally (see below).
4. Open a pull request using the template, with the title in [commit format](#commits-and-pr-titles), and link the
   issue (`Fixes #123`).
5. Wait for the review and address the comments. Please do not force-push while a review is in progress.

## Development environment

Requirements: Python 3.11+ and Node.js 22. You do not need a real system – the simulator emulates FoxESS and SAJ
devices, including proxy errors and delays. The commands are in the
[README](README.md#development-without-a-real-system).

Before the PR:

```bash
.venv/bin/pytest
```

```bash
cd web && npm run build
```

If dependencies change, one command regenerates the lockfiles and the license list (requires
[uv](https://docs.astral.sh/uv/)). The files belong in the same commit; otherwise CI fails until the "Dependency
files" workflow adds them:

```bash
scripts/deps.sh
```

## Code rules

- **Match the surrounding code:** Adapt naming, comment density and style to the code around it. Comments and
  identifiers in English, all texts in the app in German.
- **Understandable for non-technical users:** The app is aimed at system owners without technical knowledge. No jargon
  without explanation; error messages as complete German sentences with a hint on what to do.
- **Everything configurable in the app:** New options belong in the UI, not only in a configuration file.
- **Always keep the demo up to date:** The demo on the project page is the same web app, only the data comes from a
  simulation in the browser (`web/src/demo/`). New screens appear there automatically. If the app reads a new API
  endpoint, it needs a demo response in `web/src/demo/server.ts` (or an entry marking it as write-only or not needed in
  the demo). `npm run check:demo` checks this, and so does CI.
- **Tests:** New logic gets tests; bug fixes get a test that shows the bug beforehand. Tests using the simulator always
  stop the collector in a `finally` block.
- **Accessibility:** Touch targets at least 44 px, sufficient contrast in light and dark, meaningful `aria` attributes.
- **No new dependencies** without agreeing on them in the issue.

### Inverters and control

Adding support for a new inverter or battery? Read [Writing a driver](docs/writing-a-driver.md) first: it explains
detection, the read and write path, the simulator, the tests a driver needs and how to get it reviewed.

Mistakes when writing can affect devices, the warranty or the grid connection requirements. Therefore, additionally:

- **New registers only with a source:** manufacturer documentation, a project with a compatible license (name the
  source in `docs/registers.md` and `NOTICE`) or a diagnostics report from a real device.
- **Writing functions** are only reachable via the control features, respect test mode, check limits before writing,
  read back afterwards and write to the log.
- **Continuous control** (e.g. charging based on price) only via remote control with a watchdog, never via
  permanently stored registers.
- **Legal topics** (feed-in limitation, § 14a EnWG, EEG) must be raised in the issue before any code is written.

### Adding grid operators (meter readings)

Every grid operator has its own customer portal. To add another grid operator:

1. A module in `src/openampere/gridmeter/` with a class that extends `Provider` from `base.py`: `meters()` returns the
   active meters, `daily()` the kWh per day for consumption (`import`) and feed-in (`export`). Errors as
   `ProviderAuthError` (login rejected, not retried automatically) or `ProviderError` (try again later).
2. Add the class to `PROVIDERS` in `gridmeter/__init__.py` and the key to `meter.provider` in `config.py`.
3. A test with a mocked portal as in `tests/test_gridmeter.py`, without real credentials.
4. Name the source for the endpoints and login in the module docs and in `NOTICE`. Prefer official interfaces.

Storage, fetching every six hours, meter selection and billing are the same for all grid operators.

### Third-party code and trademarks

- **No code, graphics or texts from other apps**, in particular not from the Ampere.IQ app or its decompiled code.
  Only facts are taken over (register addresses, data formats, interfaces).
- Code from other open-source projects only with a compatible license and attribution.
- Use company and product names only descriptively, see the [legal notes](README.md#background--legal-notes).

## Commits and PR titles

We use [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/). CI checks this.

```
<type>(<scope>): <short description in imperative mood, English, lowercase>
```

Examples: `fix(report): keep daily totals after midnight`, `feat(saj): read battery temperature`,
`docs: explain the VPN setup`.

| Type | Used for |
|---|---|
| `feat` | new feature for users |
| `fix` | bug fix |
| `docs` | documentation only |
| `refactor` | restructuring without changed behavior |
| `perf` | faster or more efficient |
| `test` | tests only |
| `build` | Docker, dependencies, packaging |
| `ci` | GitHub Actions |
| `chore` | maintenance that fits no other category |
| `revert` | reverts a commit |

- **Scope** (optional): e.g. `modbus`, `foxess`, `saj`, `report`, `dashboard`, `ui`, `api`, `auth`, `control`,
  `charging`, `tariffs`, `import`, `diagnostics`, `docker`, `deps`.
- **Breaking changes** (e.g. a changed database or settings that users have to adjust): `!` after the type,
  e.g. `feat(api)!: …`, and a `BREAKING CHANGE: …` paragraph in the body describing the required adjustment.
- Explain the why in the body, not just the what.
- PRs are squash-merged; the **PR title** becomes the commit message and must therefore follow this format as well.
- No links to private chats, tickets or sessions and no personal data in commits.

## Releases

The maintainer team creates releases from `main`:

```bash
scripts/release.sh 0.2.0
```

```bash
git push origin main v0.2.0
```

The script sets the version in `pyproject.toml` and `web/package.json`, commits it and creates a signed tag. The
"Release" workflow then builds the Docker image and creates the GitHub release. The release notes are generated from
the commit messages since the last tag, so good commit titles pay off: `feat` ends up under "Neu" (New), `fix` under
"Behoben" (Fixed).

## License

By contributing, you agree that your contribution is published under the project's [MIT License](LICENSE).

## How we treat each other

Friendly, factual, patient – many people here are not developers but affected owners who want to keep using their
system. Disparaging comments will be removed.
