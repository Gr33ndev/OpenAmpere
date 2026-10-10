#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""The changelog of all versions, built from the Conventional Commits between the version tags (#155).

One file, web/public/changelog.json, is the source for the changelog page of the app (it ships with the web app),
the changelog page of the website and the release notes on GitHub (scripts/release_notes.py):

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

Usage:
  scripts/changelog.py add 0.14.0   add the version with the commits since the last tag (scripts/release.sh does this)
  scripts/changelog.py rebuild      build the whole file again from the version tags
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
GROUPS = ("feat", "fix", "perf", "other")
COMMIT = re.compile(r"^(?P<type>\w+)(?:\((?P<scope>[^)]+)\))?(?P<breaking>!)?: (?P<subject>.+)$")
TAG = re.compile(r"^v\d+\.\d+\.\d+$")  # stable versions only


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True, cwd=ROOT).stdout.strip()


def parse(log: str) -> tuple[dict[str, list[dict]], list[dict]]:
    """Groups and breaking changes of `git log --format=%s%x1f%b%x1e` output."""
    groups: dict[str, list[dict]] = {g: [] for g in GROUPS}
    breaking: list[dict] = []
    for entry in filter(None, (e.strip() for e in log.split("\x1e"))):
        subject, _, body = entry.partition("\x1f")
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


def entry(version: str, date: str, prev: str | None, ref: str) -> dict:
    """One version: the commits after the previous tag up to ref."""
    groups, breaking = parse(git("log", "--no-merges", "--format=%s%x1f%b%x1e", f"{prev}..{ref}" if prev else ref))
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


def save(versions: list[dict]) -> None:
    FILE.write_text(json.dumps({"versions": versions}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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
            case ["add", version]:
                save(add(version.removeprefix("v")))
            case _:
                sys.exit(__doc__)
    except ValueError as err:  # a broken summary file: nothing is written
        sys.exit(str(err))


if __name__ == "__main__":
    main()
