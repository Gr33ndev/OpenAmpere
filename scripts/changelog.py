#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""The changelog of all versions, built from the Conventional Commits between the version tags (#155).

One file, web/public/changelog.json, is the source for the changelog page of the app (it ships with the web app),
the changelog page of the website, the release notes on GitHub (scripts/release_notes.py) and ha-app/CHANGELOG.md,
which Home Assistant shows when it offers an update of the app (#300; written together with the JSON file):

    {"versions": [{"version": "0.13.3", "date": "2026-10-07", "first": false,
                   "groups": {"feat": [{"scope": "report", "text": "Show complete bars …"}], "fix": [], "perf": [],
                              "other": []},
                   "breaking": []}, …]}

Newest version first. Breaking changes are listed in "breaking" and in their group.

A version can have a short summary for owners (#176), written by the maintainer before the release in
changelog/<version>.json, German required, other languages optional:

    {"de": "Kurz gesagt: …", "en": "In short: …"}

It goes into the version's entry as "summary": {"de": …, "en": …}, shown above the list. A blank line starts a new
paragraph. A summary already in the file is kept when the version is built again and its summary file is gone.

A fix merged from a security advisory is listed with the advisory's title through the line with its id at the end of
the commit message (docs/security-advisories.md). A fix merged without that line is named in
changelog/advisory-commits.json, {"<full commit hash>": "GHSA-…"}, since a merged message cannot be changed.

Usage:
  scripts/changelog.py add 0.14.0   add the version with the commits since the last tag (scripts/release.sh does this)
  scripts/changelog.py rebuild      build the whole file again from the version tags
  scripts/changelog.py app          write ha-app/CHANGELOG.md again from web/public/changelog.json
"""

from __future__ import annotations

import datetime
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILE = ROOT / "web" / "public" / "changelog.json"
SUMMARIES = ROOT / "changelog"
APP_FILE = ROOT / "ha-app" / "CHANGELOG.md"
APP_VERSIONS = 10  # Home Assistant shows the whole file in the update dialog
SECTIONS = [("feat", "Neu"), ("fix", "Behoben"), ("perf", "Schneller"), ("other", "Weitere Änderungen")]
GROUPS = ("feat", "fix", "perf", "other")
COMMIT = re.compile(r"^(?P<type>\w+)(?:\((?P<scope>[^)]+)\))?(?P<breaking>!)?: (?P<subject>.+)$")
TAG = re.compile(r"^v\d+\.\d+\.\d+$")  # stable versions only
# A fix merged from a security advisory's private fork arrives as one commit with this title; its message names the
# advisory. It is listed as a fix with the advisory's title (looked up with the GitHub CLI if it is there).
ADVISORY_MERGE = "Merge commit from fork"
# only an id on a line of its own, as every advisory fix ends: a fix that mentions an earlier advisory in its text
# ("the rest of GHSA-…") must not list that one again (#370)
GHSA = re.compile(r"^\s*(GHSA(?:-[23456789cfghjmpqrvwx]{4}){3})\s*$", re.MULTILINE)
# fix commits merged from an advisory without its id on a line of their own (the message cannot be changed after the
# merge): commit -> advisory, read as if the message ended with the id
ADVISORY_COMMITS = SUMMARIES / "advisory-commits.json"


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True, cwd=ROOT).stdout.strip()


def advisory_title(ghsa: str) -> str | None:
    """The title of a repository security advisory, None without the GitHub CLI or access."""
    try:
        return subprocess.run(["gh", "api", f"repos/{{owner}}/{{repo}}/security-advisories/{ghsa}", "--jq", ".summary"],
                              check=True, capture_output=True, text=True, cwd=ROOT, timeout=30).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def parse(log: str, title=advisory_title) -> tuple[dict[str, list[dict]], list[dict]]:
    """Groups and breaking changes of `git log --format=%s%x1f%b%x1e` output."""
    groups: dict[str, list[dict]] = {g: [] for g in GROUPS}
    breaking: list[dict] = []
    advisories: set[str] = set()
    for entry in filter(None, (e.strip() for e in log.split("\x1e"))):
        subject, _, body = entry.partition("\x1f")
        if subject.strip() == ADVISORY_MERGE and GHSA.search(body):
            for ghsa in dict.fromkeys(GHSA.findall(body)):  # once per advisory, also when merged with another
                if ghsa not in advisories:
                    advisories.add(ghsa)
                    text = title(ghsa)
                    groups["fix"].append({"scope": "security",
                                          "text": f"{text} ({ghsa})" if text else f"Close security advisory {ghsa}"})
            continue
        m = COMMIT.match(subject.strip())
        if not m:
            groups["other"].append({"scope": None, "text": subject.strip()})
            continue
        if m["type"] == "chore" and (m["scope"] or "").startswith("release"):
            continue
        item = {"scope": m["scope"], "text": m["subject"][0].upper() + m["subject"][1:]}
        if m["breaking"] or "BREAKING CHANGE" in body:
            breaking.append(item)
        groups[m["type"] if m["type"] in GROUPS else "other"].append(item)
    return groups, breaking


def with_advisories(log: str, advisories: dict[str, str]) -> str:
    """`git log --format=%H%x1f%s%x1f%b%x1e` output as parse() reads it, with the id of a listed advisory added to the
    message of its fix commit."""
    entries = []
    for e in filter(None, (e.strip() for e in log.split("\x1e"))):
        commit, _, rest = e.partition("\x1f")
        entries.append(f"{rest}\n\n{advisories[commit]}" if commit in advisories else rest)
    return "".join(f"{e}\x1e" for e in entries)


def advisory_commits(path: Path = ADVISORY_COMMITS) -> dict[str, str]:
    """changelog/advisory-commits.json; stops the release when a listed commit is not in the history (a typo would
    leave the advisory out of the changelog without a word)."""
    advisories = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    for commit in advisories:
        if subprocess.run(["git", "cat-file", "-e", f"{commit}^{{commit}}"], cwd=ROOT, capture_output=True).returncode:
            raise SystemExit(f"{path.name}: commit {commit} is not in the history")
    return advisories


def entry(version: str, date: str, prev: str | None, ref: str) -> dict:
    """One version: the commits after the previous tag up to ref."""
    advisories = advisory_commits()
    log = git("log", "--no-merges", "--format=%H%x1f%s%x1f%b%x1e", f"{prev}..{ref}" if prev else ref)
    groups, breaking = parse(with_advisories(log, advisories))
    if not prev:  # the full history is too long for a first release
        groups = {g: items if g == "feat" else [] for g, items in groups.items()}
    return {"version": version, "date": date, "first": prev is None, "groups": groups, "breaking": breaking}


def read_summary(version: str, folder: Path = SUMMARIES) -> dict[str, str] | None:
    """The summary of changelog/<version>.json, or None without such a file."""
    path = folder / f"{version}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as err:
        raise ValueError(f"{path}: kein gültiges JSON ({err})") from err
    if not isinstance(data, dict) or not all(isinstance(lang, str) and isinstance(text, str) and text.strip()
                                             for lang, text in data.items()):
        raise ValueError(f'{path}: erwartet {{"de": "Text", "en": "Text"}}, nur Texte, keiner leer')
    if "de" not in data:
        raise ValueError(f'{path}: die deutsche Zusammenfassung ("de") fehlt')
    return {lang: text.strip() for lang, text in data.items()}


def with_summaries(versions: list[dict], previous: list[dict], folder: Path = SUMMARIES) -> list[dict]:
    """Each version with its summary: from its file, else the one it had in the changelog before, else none."""
    before = {v["version"]: v.get("summary") for v in previous}
    for v in versions:
        summary = read_summary(v["version"], folder) or before.get(v["version"])
        if summary:
            v["summary"] = summary
        else:
            v.pop("summary", None)
    return versions


def tags() -> list[str]:
    """Stable version tags, oldest first."""
    return [t for t in git("tag", "--list", "v*", "--sort=v:refname").splitlines() if TAG.match(t)]


def load() -> list[dict]:
    return json.loads(FILE.read_text(encoding="utf-8"))["versions"] if FILE.exists() else []


def item(entry: dict) -> str:
    return f"**{entry['scope']}:** {entry['text']}" if entry["scope"] else entry["text"]


def app_changelog(versions: list[dict]) -> str:
    """ha-app/CHANGELOG.md: the latest versions as in the release notes, newest first."""
    out = ["# Changelog", "",
           "<!-- written by scripts/changelog.py from web/public/changelog.json, do not edit -->", "",
           "All versions: https://github.com/Gr33ndev/OpenAmpere/releases", ""]
    for version in versions[:APP_VERSIONS]:
        out += [f"## {version['version']} ({version['date']})" if version["date"] else f"## {version['version']}", ""]
        summary = version.get("summary") or {}
        if summary.get("de"):
            out += [summary["de"], ""]
        if summary.get("en"):
            out += [f"**English:** {summary['en']}", ""]
        if version.get("breaking"):
            out += ["### Achtung", "", *(f"- {item(b)}" for b in version["breaking"]), ""]
        for group, title in SECTIONS:
            if version["groups"].get(group):
                out += [f"### {title}", "", *(f"- {item(e)}" for e in version["groups"][group]), ""]
    return "\n".join(out).rstrip() + "\n"


def save(versions: list[dict]) -> None:
    FILE.write_text(json.dumps({"versions": versions}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    APP_FILE.write_text(app_changelog(versions), encoding="utf-8")


def rebuild() -> list[dict]:
    versions, prev = [], None
    for tag in tags():
        date = git("for-each-ref", "--format=%(creatordate:short)", f"refs/tags/{tag}")
        versions.insert(0, entry(tag.removeprefix("v"), date, prev, tag))
        prev = tag
    return with_summaries(versions, load())


def add(version: str) -> list[dict]:
    known = tags()
    prev = known[-1] if known else None
    new = entry(version, datetime.date.today().isoformat(), prev, "HEAD")
    before = load()
    return with_summaries([new, *(v for v in before if v["version"] != version)], before)


def main() -> None:
    try:
        match sys.argv[1:]:
            case ["rebuild"]:
                save(rebuild())
            case ["app"]:
                save(load())
            case ["add", version]:
                save(add(version.removeprefix("v")))
            case _:
                sys.exit(__doc__)
    except ValueError as err:  # a broken summary file: nothing is written
        sys.exit(str(err))


if __name__ == "__main__":
    main()
