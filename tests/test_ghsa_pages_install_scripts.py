# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""The website serves install.sh, updater.sh and tailscale.sh that no npm code of the site build can replace.

Owners pipe install.sh from the website into bash and it uses sudo. The build job of the website workflow runs code
of npm packages. If the install scripts travelled as an artifact of the same run, that code could store another
artifact under the same name, and the job that assembles the site would publish it. So the assembling job writes
the scripts itself from its own checkout, downloads nothing but the site, and replaces whatever the site brings
under these names.
"""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = yaml.safe_load((ROOT / ".github" / "workflows" / "pages.yml").read_text())
JOBS = WORKFLOW["jobs"]
NAMES = ("install.sh", "tailscale.sh", "updater.sh", "SHA256SUMS")


def _uses(step, action):
    return step.get("uses", "").split("@")[0] == action


def _index(steps, predicate):
    return next(i for i, step in enumerate(steps) if predicate(step))


def _runs_npm_code(job):
    return any(_uses(s, "actions/setup-node") or "npm" in s.get("run", "") for s in job.get("steps", []))


def _assemble():
    # the job that hands the site to Pages
    name = next(n for n, job in JOBS.items()
                if any(_uses(s, "actions/upload-pages-artifact") for s in job.get("steps", [])))
    return name, JOBS[name]


def test_only_the_site_travels_as_an_artifact():
    uploads = [(name, s["with"]["name"]) for name, job in JOBS.items() for s in job.get("steps", [])
               if _uses(s, "actions/upload-artifact")]
    assert all(_runs_npm_code(JOBS[name]) for name, _ in uploads), uploads
    assert [artifact for _, artifact in uploads] == ["site"]

    _, job = _assemble()
    downloads = [s.get("with", {}) for s in job["steps"] if _uses(s, "actions/download-artifact")]
    assert downloads == [{"name": "site", "path": "_site"}]


def test_assemble_writes_the_install_scripts_itself_before_it_downloads_the_site():
    _, job = _assemble()
    steps = job["steps"]
    assert not _runs_npm_code(job)
    assert job["permissions"].get("contents") == "read"

    checkout = _index(steps, lambda s: _uses(s, "actions/checkout"))
    assert steps[checkout]["with"]["persist-credentials"] is False
    assert steps[checkout]["with"]["fetch-depth"] == 0  # the release tags the scripts come from
    scripts = _index(steps, lambda s: "scripts/site-scripts.sh _scripts" in s.get("run", ""))
    download = _index(steps, lambda s: _uses(s, "actions/download-artifact"))
    replace = _index(steps, lambda s: "cp _scripts/" in s.get("run", ""))
    upload = _index(steps, lambda s: _uses(s, "actions/upload-pages-artifact"))
    assert checkout < scripts < download < replace < upload
    assert all(f"_site/{name}" in steps[replace]["run"] for name in NAMES)


@pytest.mark.skipif(shutil.which("sha256sum") is None and shutil.which("shasum") is None, reason="needs sha256sum")
def test_assemble_replaces_install_scripts_the_site_brings(tmp_path):
    _, job = _assemble()
    replace = next(s["run"] for s in job["steps"] if "cp _scripts/" in s.get("run", ""))
    if shutil.which("sha256sum") is None:  # macOS without coreutils
        replace = replace.replace("sha256sum -c", "shasum -a 256 -c")

    trusted = tmp_path / "_scripts"
    trusted.mkdir()
    for name in NAMES[:3]:
        (trusted / name).write_text(f"echo trusted {name}\n")
    sums = subprocess.run(["sh", "-c", "sha256sum * 2>/dev/null || shasum -a 256 *"], cwd=trusted, check=True,
                          capture_output=True, text=True).stdout
    (trusted / "SHA256SUMS").write_text(sums)
    site = tmp_path / "_site"
    (site / "updater.sh").mkdir(parents=True)  # even a folder under one of the names goes
    (site / "install.sh").write_text("echo replaced\n")
    (site / "SHA256SUMS").write_text("0" * 64 + "  install.sh\n")
    (site / "index.html").write_text("<html></html>\n")

    subprocess.run(["bash", "-e", "-c", replace], cwd=tmp_path, check=True, capture_output=True)
    for name in NAMES:
        assert (site / name).read_text() == (trusted / name).read_text()
    assert (site / "index.html").exists()
