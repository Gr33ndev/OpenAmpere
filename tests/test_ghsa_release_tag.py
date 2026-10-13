# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""The release workflow signs only the files of a version tag on main, also when it is started by hand, and every
documented provenance check pins that tag (GHSA-3xmc-fc67-chgq)."""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
CHECK = "The tag is a version tag of main"


def release_steps() -> list[dict]:
    return yaml.safe_load(WORKFLOW.read_text())["jobs"]["release"]["steps"]


def check_step() -> dict:
    steps = [s for s in release_steps() if s.get("name") == CHECK]
    assert len(steps) == 1, f"the release job needs the step {CHECK!r}"
    return steps[0]


def test_the_tag_is_checked_before_anything_is_signed():
    names = [s.get("name") or s.get("uses", "") for s in release_steps()]
    check = names.index(CHECK)
    assert names[0].startswith("actions/checkout@") and check == 1  # right after the checkout
    assert all("attest-build-provenance" not in n for n in names[:check])
    assert check < names.index("Collect the files to attach")


def test_the_input_reaches_scripts_only_through_the_environment():
    for job in yaml.safe_load(WORKFLOW.read_text())["jobs"].values():
        for step in job["steps"]:
            assert "inputs." not in step.get("run", ""), step  # never ${{ inputs.tag }} inside a shell script


GIT_ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "t",
           "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, env=GIT_ENV, capture_output=True, text=True, check=True)


@pytest.fixture(scope="module")
def checkout(tmp_path_factory) -> Path:
    """A clone like the one of actions/checkout with fetch-depth 0: main, a side branch and tags of all kinds."""
    if shutil.which("git") is None or shutil.which("bash") is None:
        pytest.skip("needs git and bash")
    base = tmp_path_factory.mktemp("release")
    git(base, "init", "-q", "--bare", "-b", "main", "origin.git")
    git(base, "init", "-q", "-b", "main", "work")
    work = base / "work"
    git(work, "commit", "-q", "--allow-empty", "-m", "release")
    git(work, "tag", "-a", "v1.0.0", "-m", "1.0.0")
    git(work, "tag", "-a", "v1.0.1-rc.1", "-m", "1.0.1-rc.1")
    git(work, "tag", "v1.2.0")  # lightweight, scripts/release.sh always makes an annotated (signed) tag
    git(work, "remote", "add", "origin", str(base / "origin.git"))
    git(work, "push", "-q", "origin", "main")
    git(work, "switch", "-q", "-c", "side")
    git(work, "commit", "-q", "--allow-empty", "-m", "not on main")
    git(work, "tag", "-a", "v1.1.0", "-m", "1.1.0")
    git(work, "tag", "-a", "other", "-m", "other")
    git(work, "push", "-q", "origin", "side", "--tags")
    git(base, "clone", "-q", "origin.git", "checkout")
    return base / "checkout"


def run_check(checkout: Path, tag: str, event: str = "workflow_dispatch") -> subprocess.CompletedProcess:
    env = {**GIT_ENV, "TAG": tag, "EVENT": event}
    return subprocess.run(["bash", "-e", "-c", check_step()["run"]], cwd=checkout, env=env, capture_output=True,
                          text=True)


@pytest.mark.parametrize("tag", ["v1.0.0", "v1.0.1-rc.1"])
def test_a_version_tag_on_main_is_released(checkout, tag):
    done = run_check(checkout, tag)
    assert done.returncode == 0, done.stderr


@pytest.mark.parametrize("tag", [
    "v1.1.0",            # a signed-looking version tag, but its commit is not on main
    "other",             # any other tag
    "main",              # a branch
    "v1.2.0",            # a lightweight tag
    "v9.9.9",            # no such tag
    "1.0.0",             # without the v
    "v1.0.0\nother",     # a second line
    "v1.0.0:scripts",    # anything git would read as a path
    "",
])
def test_by_hand_anything_else_stops_the_run(checkout, tag):
    assert run_check(checkout, tag).returncode != 0


def test_a_pushed_tag_is_not_checked_against_main(checkout):
    """scripts/release.sh pushes the tag first and main only after the workflow; pushing a v* tag needs admin rights
    (ruleset)."""
    assert run_check(checkout, "v1.1.0", event="push").returncode == 0
    assert run_check(checkout, "other", event="push").returncode != 0  # the version format is checked for both


def verify_commands(text: str) -> list[str]:
    """Every `gh attestation verify` with its continuation lines (ending with a backslash)."""
    lines, commands = text.splitlines(), []
    for i, line in enumerate(lines):
        if "gh attestation verify" in line:
            command = line
            while command.rstrip().endswith("\\") and i + 1 < len(lines):
                i += 1
                command += "\n" + lines[i]
            commands.append(command)
    return commands


@pytest.mark.parametrize("name", ["README.md", "README.de.md", ".github/workflows/release.yml"])
def test_every_documented_check_pins_the_release_tag(name):
    commands = verify_commands((ROOT / name).read_text())
    assert len(commands) == 2  # the image and the attached files
    for command in commands:
        assert re.search(r"--source-ref refs/tags/v<version>", command), command
        assert "--signer-workflow Gr33ndev/OpenAmpere/.github/workflows/release.yml" in command, command
