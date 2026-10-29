# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""scripts/ai-review-changed.sh: what the AI review changed in the checkout besides its summary (GHSA-mwqm-mf75-9jhh)."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "ai-review-changed.sh"
pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git, as on the GitHub runner")


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args], cwd=repo, check=True,
                   capture_output=True)


@pytest.fixture
def repo(tmp_path):
    """A checkout as before the review, its git configuration saved like the workflow does."""
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    git(checkout, "init", "-q")
    (checkout / ".gitignore").write_text(".env\npackage.json\n.claude-pr/\n")
    (checkout / "README.md").write_text("readme\n")
    git(checkout, "add", ".")
    git(checkout, "commit", "-qm", "init")
    subprocess.run(["sh", str(SCRIPT), "--save", str(tmp_path / "git-state")], cwd=checkout, check=True)
    return checkout


def changed(repo: Path) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(["sh", str(SCRIPT), str(repo.parent / "git-state")], cwd=repo, capture_output=True,
                          text=True, env=env)


def test_the_summary_the_sandboxs_placeholders_and_the_actions_folder_pass(repo):
    (repo / ".ai-review-summary.md").write_text("## AI review\n")
    (repo / ".env").write_text("")
    (repo / ".claude-pr").mkdir()
    (repo / ".claude-pr" / "data.json").write_text("{}")
    done = changed(repo)
    assert (done.returncode, done.stdout) == (0, "")


def test_other_changes_are_listed(repo):
    (repo / "package.json").write_text("{}")  # ignored, but not empty
    (repo / "empty").write_text("")
    (repo / ".env").symlink_to(repo / "empty")  # ignored and empty, but a link
    (repo / "new.sh").write_text("echo\n")
    (repo / "README.md").write_text("changed\n")
    done = changed(repo)
    assert done.returncode == 0
    assert set(done.stdout.splitlines()) == {"!! .env", "!! package.json", " M README.md", "?? empty", "?? new.sh"}


def test_a_changed_git_configuration_fails_before_git_status_runs(repo):
    # a clean filter in .git/config would run in git status: compared first, and git status is not reached
    marker = repo.parent / "filter-ran"
    with open(repo / ".git" / "config", "a") as config:
        config.write(f'[filter "x"]\n\tclean = touch {marker}; cat\n')
    (repo / ".gitattributes").write_text("README.md filter=x\n")
    (repo / "README.md").write_text("changed\n")
    done = changed(repo)
    assert done.returncode == 1 and "git configuration" in done.stderr
    assert not marker.exists()


def test_a_broken_checkout_fails(repo, tmp_path):
    (repo / ".git" / "HEAD").write_text("broken\n")  # git status fails without the configuration changing
    done = changed(repo)
    assert done.returncode == 1 and done.stdout == ""


def test_without_the_saved_state_it_fails(repo):
    done = subprocess.run(["sh", str(SCRIPT)], cwd=repo, capture_output=True, text=True)
    assert done.returncode == 1
