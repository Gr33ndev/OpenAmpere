# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""The "Dependency files" workflow keeps the code of updated packages away from its write token.

The generate job runs new, unreviewed package versions without write access. The sync job commits what it produced
with a token that can write. It must treat the artifact of the generate job as data only: unpack it outside its
checkout, take over exactly the expected regular files, and run nothing that came with it. The generate job must not
leave caches behind that other workflows restore.
"""

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "dependencies.yml"
LOCKFILES = ["requirements.lock", "requirements-dev.lock", "requirements-build.lock", "tests_ha/requirements.lock"]
HELPER = "#!/usr/bin/env python3\nprint('the helper of the checkout')\n"


def jobs() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())["jobs"]


def step_using(job: dict, action: str) -> dict:
    found = [step for step in job["steps"] if step.get("uses", "").startswith(action + "@")]
    assert len(found) == 1, action
    return found[0]


def uploaded_files() -> list[str]:
    upload = step_using(jobs()["generate"], "actions/upload-artifact")
    return upload["with"]["path"].split()


def take_over_step() -> dict:
    """The step of the sync job between unpacking the artifact and the first step that gets the token."""
    steps = jobs()["sync"]["steps"]
    start = next(i for i, step in enumerate(steps) if step.get("uses", "").startswith("actions/download-artifact@"))
    between = []
    for step in steps[start + 1:]:
        if "GH_TOKEN" in (step.get("env") or {}):
            break
        between.append(step)
    assert len(between) == 1, "one step must check and copy the artifact before the token is used"
    return between[0]


def test_artifact_is_unpacked_outside_the_checkout():
    download = step_using(jobs()["sync"], "actions/download-artifact")
    assert download["with"]["name"] == "dependency-files"
    assert download["with"]["path"].startswith("${{ runner.temp }}/")


def test_sync_expects_exactly_the_uploaded_files():
    assert sorted(jobs()["sync"]["env"]["FILES"].split()) == sorted(uploaded_files())
    assert sorted(uploaded_files()) == sorted([*LOCKFILES, "THIRD_PARTY_LICENSES.md",
                                               "web/public/third-party-licenses.json"])


def test_commit_helper_runs_isolated():
    """python3 -I: no script folder on sys.path, no PYTHON* variables, no user site packages."""
    runs = [step["run"] for step in jobs()["sync"]["steps"] if "commit_via_api.py" in step.get("run", "")]
    assert len(runs) == 2
    for run in runs:
        for line in run.splitlines():
            if "commit_via_api.py" in line:
                assert "python3 -I scripts/commit_via_api.py" in line


def test_generate_job_saves_no_cache():
    """The weekly upgrade runs unreviewed packages on main; caches saved there are restored by other workflows."""
    generate = jobs()["generate"]
    node = step_using(generate, "actions/setup-node")["with"]
    assert "cache" not in node and "cache-dependency-path" not in node
    assert node["package-manager-cache"] is False
    assert step_using(generate, "astral-sh/setup-uv")["with"]["enable-cache"] is False
    assert "cache" not in (step_using(generate, "actions/setup-python").get("with") or {})


# --- the take-over step itself, run against made-up artifacts ---------------------------------------------------

needs_bash = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def make_artifact(runner: Path) -> Path:
    """An artifact as the generate job uploads it, with the current generated files of the repository."""
    artifact = runner / "dependency-files"
    for name in uploaded_files():
        (artifact / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, artifact / name)
    return artifact


def make_checkout(folder: Path) -> Path:
    checkout = folder / "checkout"
    for name in uploaded_files():
        (checkout / name).parent.mkdir(parents=True, exist_ok=True)
        (checkout / name).write_text("old\n")
    (checkout / "scripts").mkdir()
    (checkout / "scripts" / "commit_via_api.py").write_text(HELPER)
    return checkout


# the shells GitHub uses for run steps on Linux: without and with "shell: bash"
SHELLS = {"default": ["bash", "-e"], "bash": ["bash", "--noprofile", "--norc", "-eo", "pipefail"]}


def take_over(runner: Path, checkout: Path, shell: str = "default") -> subprocess.CompletedProcess:
    assert "shell" not in take_over_step()
    env = {"PATH": os.environ["PATH"], "RUNNER_TEMP": str(runner), "FILES": jobs()["sync"]["env"]["FILES"]}
    return subprocess.run([*SHELLS[shell], "-c", take_over_step()["run"]],
                          cwd=checkout, env=env, capture_output=True, text=True)


def untouched(checkout: Path) -> bool:
    return (checkout / "scripts" / "commit_via_api.py").read_text() == HELPER and all(
        (checkout / name).read_text() == "old\n" for name in uploaded_files())


@needs_bash
@pytest.mark.parametrize("shell", SHELLS)
def test_take_over_copies_the_generated_files(tmp_path, shell):
    artifact = make_artifact(tmp_path / "runner")
    checkout = make_checkout(tmp_path)
    done = take_over(tmp_path / "runner", checkout, shell)
    assert done.returncode == 0, done.stdout + done.stderr
    for name in uploaded_files():
        assert (checkout / name).read_bytes() == (artifact / name).read_bytes()
        assert stat.S_IMODE((checkout / name).stat().st_mode) == 0o644
    assert (checkout / "scripts" / "commit_via_api.py").read_text() == HELPER


def add_file(artifact: Path, name: str) -> None:
    (artifact / name).parent.mkdir(parents=True, exist_ok=True)
    (artifact / name).write_text("import os\nos.system('echo owned')\n")


def link_lockfile(artifact: Path) -> None:
    (artifact / "requirements.lock").unlink()
    (artifact / "requirements.lock").symlink_to(artifact.parent / "elsewhere.lock")
    shutil.copyfile(ROOT / "requirements.lock", artifact.parent / "elsewhere.lock")


def link_folder(artifact: Path) -> None:
    shutil.move(artifact / "web", artifact.parent / "web")
    (artifact / "web").symlink_to(artifact.parent / "web")


def append_line(name: str, line: str):
    def change(artifact: Path) -> None:
        with open(artifact / name, "a") as f:
            f.write(line + "\n")
    return change


BAD_ARTIFACTS = {
    "replaces the commit helper": lambda a: add_file(a, "scripts/commit_via_api.py"),
    "adds a module next to the helper": lambda a: add_file(a, "scripts/json.py"),
    "changes the git config": lambda a: add_file(a, ".git/config"),
    "adds a file at the top": lambda a: add_file(a, "sitecustomize.py"),
    "misses a file": lambda a: (a / "THIRD_PARTY_LICENSES.md").unlink(),
    "has a symlink instead of a lockfile": link_lockfile,
    "has a symlink instead of a folder": link_folder,
    "names another package index": append_line("requirements.lock", "--index-url https://example.invalid/simple"),
    "adds an extra index": append_line("tests_ha/requirements.lock", "--extra-index-url https://example.invalid/"),
    "installs from a repository": append_line("requirements-dev.lock", "-e git+https://example.invalid/x.git#egg=x"),
    "installs a package without hash": append_line("requirements-build.lock", "example==1.0"),
    "hides an option in a marker": append_line(
        "requirements.lock", "example==1.0 ; sys_platform == 'linux' --config-settings=x=y \\"),
}


@needs_bash
@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("change", BAD_ARTIFACTS.values(), ids=BAD_ARTIFACTS.keys())
def test_take_over_refuses_an_unexpected_artifact(tmp_path, change, shell):
    change(make_artifact(tmp_path / "runner"))
    checkout = make_checkout(tmp_path)
    done = take_over(tmp_path / "runner", checkout, shell)
    assert done.returncode != 0
    assert "::error::" in done.stdout
    assert untouched(checkout)
