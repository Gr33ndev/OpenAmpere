#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Release notes for a version tag, from its entry in web/public/changelog.json (#155).

The entry is written by scripts/changelog.py from the Conventional Commits since the previous tag (scripts/release.sh
adds it to the release commit). A tag without an entry gets one built from the commits the same way. The version's
summary (changelog/<version>.json, #176) comes first, German and then English.

Usage: scripts/release_notes.py v0.1.1   (prints Markdown)
"""

from __future__ import annotations

import subprocess
import sys

import changelog

SECTIONS = [("feat", "Neu"), ("fix", "Behoben"), ("perf", "Schneller"), ("other", "Weitere Änderungen")]


def previous_tag(tag: str) -> str | None:
    try:
        return changelog.git("describe", "--tags", "--abbrev=0", "--match", "v*", f"{tag}^")
    except subprocess.CalledProcessError:
        return None


def line(item: dict) -> str:
    return f"**{item['scope']}:** {item['text']}" if item["scope"] else item["text"]


def notes(entry: dict, version: str, tag: str, prev: str | None) -> str:
    """The Markdown of the release."""
    out: list[str] = []
    # the maintainer's summary (#176): German, then English when there is one
    summary = entry.get("summary") or {}
    if summary.get("de"):
        out += [summary["de"], ""]
    if summary.get("en"):
        out += [f"**English:** {summary['en']}", ""]
    if entry["first"]:
        out += ["Erste veröffentlichte Version von OpenAmpere.", ""]
    if entry["breaking"]:
        out += ["### Achtung", "", *(f"- {line(b)}" for b in entry["breaking"]), ""]
    for group, title in SECTIONS:
        if entry["groups"].get(group):
            out += [f"### {title}", "", *(f"- {line(item)}" for item in entry["groups"][group]), ""]

    out += [
        "### Installieren oder aktualisieren",
        "",
        "```bash",
        "curl -fsSL https://gr33ndev.github.io/OpenAmpere/install.sh | bash",
        "```",
        "",
        f"Docker-Image: `ghcr.io/gr33ndev/openampere:{version}` (auch als `latest`)."
        " Wer per git installiert hat: `git pull && docker compose up -d --build`.",
    ]
    if prev:
        out += ["", f"Alle Änderungen: https://github.com/Gr33ndev/OpenAmpere/compare/{prev}...{tag}"]
    return "\n".join(out)


def main() -> None:
    tag = sys.argv[1]
    version = tag.removeprefix("v")
    prev = previous_tag(tag)
    found = next((v for v in changelog.load() if v["version"] == version), None)
    entry = found or changelog.with_summaries([changelog.entry(version, "", prev, tag)], [])[0]
    print(notes(entry, version, tag, prev))


if __name__ == "__main__":
    main()
