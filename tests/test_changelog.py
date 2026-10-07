"""The changelog of app, website and release notes (#155)."""

import json
import runpy
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
changelog = runpy.run_path(str(ROOT / "scripts" / "changelog.py"))


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
