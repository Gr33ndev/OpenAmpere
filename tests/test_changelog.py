# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""The changelog of app, website and release notes (#155)."""

import json
import runpy
import subprocess
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


def test_a_fix_merged_from_a_security_advisory_is_listed_with_the_advisory():
    """GitHub merges the fix of an advisory's private fork as "Merge commit from fork"; the message names it."""
    titles = {"GHSA-q3c2-xqgm-w8jw": "Restoring a crafted backup can plant database triggers"}
    groups, _ = changelog["parse"](log(
        ("Merge commit from fork", "Restore only the data of a backup.\n\nGHSA-q3c2-xqgm-w8jw\n\nCo-authored-by: x"),
        ("Merge commit from fork", "* fix(api): one\n\nGHSA-vrrc-53mr-hc3c\n\n* fix(api): two\n\nGHSA-gfr8-g665-933w"),
        ("Merge commit from fork", "GHSA-vrrc-53mr-hc3c"),  # already listed
    ), title=titles.get)
    assert groups["fix"] == [
        {"scope": "security", "text": "Restoring a crafted backup can plant database triggers (GHSA-q3c2-xqgm-w8jw)"},
        {"scope": "security", "text": "Close security advisory GHSA-vrrc-53mr-hc3c"},
        {"scope": "security", "text": "Close security advisory GHSA-gfr8-g665-933w"}]
    assert groups["other"] == []


def test_a_listed_fix_commit_without_the_id_is_listed_with_its_advisory():
    """A fix merged without the advisory id on a line of its own: changelog/advisory-commits.json names it."""
    raw = "aaa\x1fMerge commit from fork\x1fFix the search.\x1ebbb\x1ffix(web): other\x1f\x1e"
    groups, _ = changelog["parse"](changelog["with_advisories"](raw, {"aaa": "GHSA-q7r4-hqhv-322x"}), title={}.get)
    assert groups["fix"] == [{"scope": "security", "text": "Close security advisory GHSA-q7r4-hqhv-322x"},
                             {"scope": "web", "text": "Other"}]
    assert groups["other"] == []


def test_every_listed_advisory_commit_is_a_merge_from_a_fork():
    listed = json.loads((ROOT / "changelog" / "advisory-commits.json").read_text())
    assert all(changelog["GHSA"].fullmatch(ghsa) for ghsa in listed.values())
    for commit in listed:
        subject = subprocess.run(["git", "log", "-1", "--format=%s", commit], cwd=ROOT, capture_output=True,
                                 text=True).stdout.strip()
        assert subject in ("Merge commit from fork", ""), commit  # "" in a shallow clone without that commit


def test_an_advisory_only_mentioned_in_the_text_is_not_listed():
    """#370: the fix of one advisory refers to an earlier one; only the advisory on a line of its own is listed."""
    groups, _ = changelog["parse"](log(
        ("Merge commit from fork", "GHSA-2j3r-6fcx-9m7v moved the folders, the example did not (the rest of\n"
                                   "GHSA-2j3r-6fcx-9m7v).\n\nGHSA-3grw-j32m-8gcj\n\nCo-Authored-By: x"),
    ), title={}.get)
    assert groups["fix"] == [{"scope": "security", "text": "Close security advisory GHSA-3grw-j32m-8gcj"}]


def test_security_advisories_are_linked_in_the_release_notes():
    item = {"scope": "security", "text": "Restoring a crafted backup takes over unchecked data (GHSA-4jmw-6h58-vc78)"}
    assert release_notes["line"](item) == (
        "**security:** Restoring a crafted backup takes over unchecked data "
        "([GHSA-4jmw-6h58-vc78](https://github.com/Gr33ndev/OpenAmpere/security/advisories/GHSA-4jmw-6h58-vc78))")


def test_the_changelog_of_the_home_assistant_app_is_built_from_the_same_file():
    """ha-app/CHANGELOG.md is what Home Assistant shows with an update of the app (#300); scripts/changelog.py writes
    it together with web/public/changelog.json, so a release changes both."""
    versions = json.loads((ROOT / "web" / "public" / "changelog.json").read_text(encoding="utf-8"))["versions"]
    assert (ROOT / "ha-app" / "CHANGELOG.md").read_text(encoding="utf-8") == changelog["app_changelog"](versions)
    text = changelog["app_changelog"]([version("9.9.9", summary={"de": "Kurz gesagt.", "en": "In short."},
                                               groups={"feat": [{"scope": "app", "text": "New"}], "fix": [],
                                                       "perf": [], "other": []})])
    assert "## 9.9.9 (2026-10-01)\n\nKurz gesagt.\n\n**English:** In short.\n\n### Neu\n\n- **app:** New" in text
