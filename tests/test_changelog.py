# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""The changelog of app, website and release notes (#155)."""

import json
import runpy
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
changelog = runpy.run_path(str(ROOT / "scripts" / "changelog.py"))
sys.path.insert(0, str(ROOT / "scripts"))  # release_notes.py imports changelog.py
release_notes = runpy.run_path(str(ROOT / "scripts" / "release_notes.py"))


def log(*commits: tuple[str, str]) -> str:
    return "".join(f"{subject}\x1f{body}\x1e" for subject, body in commits)


def test_commits_are_grouped_like_the_release_notes():
    groups, breaking = changelog["parse"](log(
        ("feat(report): show a changelog (#155)", ""),
        ("fix: keep daily totals after midnight", ""),
        ("perf(api): cache the summary", ""),
        ("docs: explain the VPN setup", ""),
        ("chore(release): 0.13.3", ""),
        ("Merge something odd", ""),
        ("feat(api)!: rename the settings", "BREAKING CHANGE: adjust the settings"),
    ))
    assert groups["feat"] == [{"scope": "report", "text": "Show a changelog (#155)"},
                              {"scope": "api", "text": "Rename the settings"}]
    assert groups["fix"] == [{"scope": None, "text": "Keep daily totals after midnight"}]
    assert groups["perf"] == [{"scope": "api", "text": "Cache the summary"}]
    assert groups["other"] == [{"scope": None, "text": "Explain the VPN setup"}, {"scope": None, "text": "Merge something odd"}]
    assert breaking == [{"scope": "api", "text": "Rename the settings"}]


def test_the_file_has_every_released_version_newest_first():
    versions = json.loads((ROOT / "web" / "public" / "changelog.json").read_text(encoding="utf-8"))["versions"]
    numbers = [tuple(map(int, v["version"].split("."))) for v in versions]
    assert numbers == sorted(numbers, reverse=True) and len(set(numbers)) == len(numbers)
    # scripts/release.sh adds the entry in the release commit, so the version in pyproject.toml is always in the file
    current = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    assert versions[0]["version"] == current
    assert versions[-1]["first"] and not any(v["first"] for v in versions[:-1])
    for v in versions:
        assert set(v["groups"]) == {"feat", "fix", "perf", "other"} and len(v["date"]) == 10
        # a summary (#176) has German, every text filled
        assert "summary" not in v or (v["summary"].get("de") and all(v["summary"].values()))


def version(number: str, **extra) -> dict:
    return {"version": number, "date": "2026-10-01", "first": False, "breaking": [],
            "groups": {"feat": [], "fix": [], "perf": [], "other": []}, **extra}


def test_summary_comes_from_its_file_and_stays_when_the_file_is_gone(tmp_path):
    """#176: the maintainer writes changelog/<version>.json before the release, the changelog keeps it."""
    (tmp_path / "0.17.0.json").write_text(json.dumps({"de": " Kurz gesagt: neu. ", "en": "In short: new."}),
                                          encoding="utf-8")
    old = version("0.16.0", summary={"de": "Schon da."})
    versions = changelog["with_summaries"]([version("0.17.0"), version("0.16.0"), version("0.15.0")], [old], tmp_path)
    assert versions[0]["summary"] == {"de": "Kurz gesagt: neu.", "en": "In short: new."}
    assert versions[1]["summary"] == {"de": "Schon da."}  # built again without a file: kept
    assert "summary" not in versions[2]  # none written: none invented


def test_a_summary_file_replaces_the_summary_in_the_changelog(tmp_path):
    (tmp_path / "0.16.0.json").write_text(json.dumps({"de": "Neu formuliert."}), encoding="utf-8")
    versions = changelog["with_summaries"]([version("0.16.0")], [version("0.16.0", summary={"de": "Alt."})], tmp_path)
    assert versions[0]["summary"] == {"de": "Neu formuliert."}


@pytest.mark.parametrize("content", ['{"en": "English only"}', '{"de": " "}', '{"de": 1}', '["de"]', '{"de": "x",'])
def test_a_broken_summary_file_stops_the_release(tmp_path, content):
    (tmp_path / "0.17.0.json").write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match="0.17.0.json"):
        changelog["read_summary"]("0.17.0", tmp_path)


def test_release_notes_start_with_the_summary():
    feat = {"feat": [{"scope": "ui", "text": "Show it"}], "fix": [], "perf": [], "other": []}
    notes = release_notes["notes"](version("0.17.0", groups=feat, summary={"de": "Kurz gesagt: neu.", "en": "In short."}),
                                   "0.17.0", "v0.17.0", "v0.16.0")
    assert notes.startswith("Kurz gesagt: neu.\n\n**English:** In short.\n\n### Neu\n\n- **ui:** Show it\n")
    german_only = release_notes["notes"](version("0.17.0", groups=feat, summary={"de": "Nur Deutsch."}),
                                         "0.17.0", "v0.17.0", "v0.16.0")
    assert german_only.startswith("Nur Deutsch.\n\n### Neu") and "English" not in german_only
    assert release_notes["notes"](version("0.17.0", groups=feat), "0.17.0", "v0.17.0", "v0.16.0").startswith("### Neu")
