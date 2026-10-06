<!-- PR title in Conventional Commits format, e.g. "fix(report): keep daily totals after midnight" (see CONTRIBUTING.md). -->

## What is this about?

<!-- Briefly: what does this PR change and why? -->

Fixes #<!-- Issue number. Without an agreed issue, please open one first (except for typos). -->

## Type of change

- [ ] Bug fix
- [ ] New feature
- [ ] New device / new registers
- [ ] UI / texts
- [ ] Documentation
- [ ] Build, CI, dependencies

## Screenshots

<!-- Required for UI changes: before/after, light and dark, at phone width (approx. 390 px). -->

| Before | After |
|---|---|
|  |  |

## How was this tested?

<!-- Simulator, real system (which device/firmware), browser/phone … -->

## Checklist

- [ ] `.venv/bin/pytest` passes, new logic has tests.
- [ ] `npm run build` in `web/` runs without errors.
- [ ] Texts in the app are German and understandable for non-technical users.
- [ ] New settings can be changed in the app, not only via a file.
- [ ] New features also work in the demo: new API endpoints have a response in `web/src/demo/server.ts` (`npm run check:demo`).
- [ ] No personal data (IP addresses, serial numbers, keys) in code, tests or screenshots.
- [ ] No code, graphics or texts from other apps; sources for registers/code are given.
- [ ] If dependencies changed: `scripts/deps.sh` was run, lockfiles and license list are in the same commit.

### Only if something is written to the inverter

- [ ] Only reachable via the control features, test mode is respected.
- [ ] Limits are checked before writing; afterwards the value is read back and logged.
- [ ] Registers are backed by a source (documentation, reference or diagnostics report from a real device).
- [ ] Legal questions (feed-in limitation, § 14a EnWG, EEG) are settled in the issue.
