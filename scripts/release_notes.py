#!/usr/bin/env python3
"""Release notes for a version tag, built from the Conventional Commits since the previous tag.

Usage: scripts/release_notes.py v0.1.1   (prints Markdown)
"""

from __future__ import annotations

import re
import subprocess
import sys

SECTIONS = [("feat", "Neu"), ("fix", "Behoben"), ("perf", "Schneller")]
OTHER = "Weitere Änderungen"
COMMIT = re.compile(r"^(?P<type>\w+)(?:\((?P<scope>[^)]+)\))?(?P<breaking>!)?: (?P<subject>.+)$")


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()


def previous_tag(tag: str) -> str | None:
    try:
        return git("describe", "--tags", "--abbrev=0", "--match", "v*", f"{tag}^")
    except subprocess.CalledProcessError:
        return None


def main() -> None:
    tag = sys.argv[1]
    version = tag.removeprefix("v")
    prev = previous_tag(tag)
    log = git("log", "--no-merges", "--format=%s%x1f%b%x1e", f"{prev}..{tag}" if prev else tag)

    groups: dict[str, list[str]] = {}
    breaking: list[str] = []
    for entry in filter(None, (e.strip() for e in log.split("\x1e"))):
        subject, _, body = entry.partition("\x1f")
        m = COMMIT.match(subject)
        if not m:
            groups.setdefault(OTHER, []).append(subject)
            continue
        if m["type"] == "chore" and (m["scope"] or "").startswith("release"):
            continue
        text = m["subject"][0].upper() + m["subject"][1:]
        line = f"**{m['scope']}:** {text}" if m["scope"] else text
        if m["breaking"] or "BREAKING CHANGE" in body:
            breaking.append(line)
        groups.setdefault(dict(SECTIONS).get(m["type"], OTHER), []).append(line)

    out: list[str] = []
    if not prev:
        out += ["Erste veröffentlichte Version von OpenAmpere.", ""]
        groups = {k: v for k, v in groups.items() if k == "Neu"}  # the full history is too long for a first release
    if breaking:
        out += ["### Achtung", "", *(f"- {b}" for b in breaking), ""]
    for title in [t for _, t in SECTIONS] + [OTHER]:
        if groups.get(title):
            out += [f"### {title}", "", *(f"- {line}" for line in groups[title]), ""]

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
    print("\n".join(out))


if __name__ == "__main__":
    main()
