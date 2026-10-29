# Handling a security advisory (maintainers)

How a vulnerability goes from a report or an internal review to a published advisory. [SECURITY.md](../SECURITY.md)
describes the process for reporters; this page has the steps and the things that are easy to get wrong.

## 1. Create the advisory

- **One advisory per vulnerability that can be fixed on its own**, also for low ones. Two findings that share one root
  cause and one fix are one advisory.
- Draft it under **Security → Advisories** (or `gh api -X POST repos/Gr33ndev/OpenAmpere/security-advisories`):
  - title and text in English, for owners: Impact (who can do what, under which conditions), Patches, Workarounds,
    Credits, References. A reference to an older advisory of the same kind goes under References.
  - ecosystem `other`, package `openampere`, affected versions as a range (`>= first affected, <= current`); patched
    versions stay empty until the release. Documented helper tools under `tools/` count as affected products too.
  - CVSS 3.1. A web endpoint reachable in the home network is `AV:N` (the specification counts the same intranet as
    network); `AV:A` only for something limited to the broadcast domain, e.g. UDP discovery.
  - no personal data and nothing that identifies an installation (see AGENTS.md), no links to private chats.
- **Request the CVE while the advisory is still a draft** (`POST …/security-advisories/<GHSA>/cve`). It is published
  with the advisory. An advisory can be published before GitHub has assigned the number. A vulnerability only in a
  CI workflow, which no installation runs, gets no CVE.

## 2. Fix it in the advisory's private fork

- **One temporary private fork per advisory** (`POST …/security-advisories/<GHSA>/forks`). Never push a security fix
  to this repository or open a public pull request for it before the release: the fix would show the vulnerability.
- The branch in the fork is the current `main` plus only this fix, so the forks can be merged in any order. Tests go
  into a **new file per advisory** (`tests/test_ghsa_xxxx_xxxx_xxxx.py`); appending to a shared test file conflicts
  with every other advisory.
- **The commit message ends with the advisory id on a line of its own** (`GHSA-xxxx-xxxx-xxxx`). The merge arrives on
  `main` as one commit titled "Merge commit from fork", and `scripts/changelog.py` only lists it as a security fix
  through that line. A fix merged without it needs an entry in `changelog/advisory-commits.json` (full commit
  hash → advisory, read by `scripts/changelog.py` since #487), because a merged message cannot be changed.
- GitHub Actions do not run in private forks. Run everything CI runs locally before asking for the merge: `ruff`,
  `pytest`, and for changes in `web/` lint, build, `check:demo`, `check:i18n` and the Playwright tests.
- The fork's `main` does not follow this repository. After other merges, rebase the branch on the current `main` here
  and push that `main` to the fork's `main`, so the pull request shows only the fix. Check that the fork's pull
  request is still open before pushing to it again.

## 3. Merge

- Merge through the advisory page (**Merge pull request** in the advisory). It is squash-merged and bypasses the branch
  rules, which is unavoidable without Actions in the fork.
- A second pull request in the same fork (a follow-up) can be merged the same way.

## 4. Release

- Release the fixes of a batch together in one version (`scripts/release.sh`, see AGENTS.md), medium before low.
- Check the changelog entry before pushing the tag: every advisory fix must appear under "Behoben" with its title
  and id, not as "Merge commit from fork".

## 5. Publish

Only once the fixed release is out:

- set the patched version and the range (`< fixed version`), and change Patches to "Fixed in X.Y.Z" with what the fix
  does;
- add the references: the release and the fix commit on `main`;
- publish. Publishing deletes the temporary private fork.
