# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Hardening of the health check and the update helper (#356).

- The health check (Docker's HEALTHCHECK, the watchdog of Home Assistant) asks a route that needs neither a login nor
  the database lock nor the thread pool: a slow database must not count as a hung server.
- The update helper accepts only a provenance signed by the release workflow run in this repository, and a version
  that did not start is not left on the disk after the rollback.
- The link to the release notes in the app only goes to GitHub.
"""

import inspect
import re
import socket
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

import pytest
import uvicorn
import yaml
from fastapi.testclient import TestClient

from openampere import __main__ as cli
from openampere import updates as updates_module
from openampere.api import create_app
from openampere.auth import Auth
from openampere.runtime import Runtime
from openampere.storage import Storage

from test_ghsa_updater_version import FAKE_DOCKER, IMAGE, env, run_update, setup, stable

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
REPO = "Gr33ndev/OpenAmpere"


# ---- health check ----------------------------------------------------------------------------------------------

def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def served(tmp_path, monkeypatch):
    """The real app (no inverter) on the port from the environment, like in the container: (runtime, port)."""
    port = free_port()
    monkeypatch.setenv("OPENAMPERE_CONFIG", str(tmp_path / "config.yaml"))
    monkeypatch.setenv("OPENAMPERE_SERVER_PORT", str(port))
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    srv = uvicorn.Server(uvicorn.Config(create_app(runtime), host="127.0.0.1", port=port, lifespan="off",
                                        log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not srv.started and time.monotonic() < deadline:
            time.sleep(0.05)
        assert srv.started
        yield runtime, port
    finally:
        srv.should_exit = True
        thread.join(10)
        runtime.storage.close()


def test_the_health_check_answers_while_the_database_is_busy(served):
    """Before: the check asked /api/auth/status, which waits for the database lock (password set?). A long write
    (backup, clean-up on a slow SD card) made the server look hung, and the watchdog of Home Assistant restarts it."""
    runtime, port = served
    holding, release = threading.Event(), threading.Event()

    def busy():
        with runtime.storage._lock:
            holding.set()
            release.wait(20)
    thread = threading.Thread(target=busy, daemon=True)
    thread.start()
    try:
        assert holding.wait(5)
        assert cli.healthcheck(None, timeout=3) == 0
    finally:
        release.set()
        thread.join(10)
    # images and app versions before this one still ask the old address: it keeps answering
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/auth/status?healthcheck=1", timeout=5) as response:
        assert response.status == 200


def test_the_health_check_needs_no_login(tmp_path):
    storage = Storage(tmp_path / "t.db")
    Auth(storage).setup("a long enough password")
    client = TestClient(create_app(Runtime({}, storage)))
    try:
        assert client.get("/api/diagnostics").status_code == 401  # a password is set
        answer = client.get("/api/health")
        assert answer.status_code == 200 and answer.json() == {"ok": True}
    finally:
        storage.close()


def test_the_health_route_runs_on_the_event_loop(tmp_path):
    """Not in the thread pool: busy threads (slow requests) do not make the server look hung, a hung event loop does."""
    storage = Storage(tmp_path / "t.db")
    try:
        routes = [r for r in create_app(Runtime({}, storage)).routes if getattr(r, "path", None) == cli.HEALTH_PATH]
        assert len(routes) == 1 and inspect.iscoroutinefunction(routes[0].endpoint)
    finally:
        storage.close()


def test_docker_and_home_assistant_ask_the_same_health_route():
    config = yaml.safe_load((ROOT / "ha-app" / "config.yaml").read_text())
    match = re.fullmatch(r"http://\[HOST\]:\[PORT:\d+\](/\S*)", config["watchdog"])
    assert match and match.group(1) == cli.HEALTH_PATH == "/api/health"
    assert 'CMD ["openampere", "healthcheck"]' in (ROOT / "Dockerfile").read_text()  # uses HEALTH_PATH


# ---- update helper ---------------------------------------------------------------------------------------------

# a stand-in for cosign: the attestation was signed by the workflow run of the tag in $FAKE/signer, in the repository
# in $FAKE/repository (for a reusable workflow: the repository of the caller). Like cosign, the repository is only
# compared when it is asked for.
FAKE_COSIGN = r"""#!/bin/sh
identity="" repository=""
while [ $# -gt 0 ]; do
  case "$1" in
    --certificate-identity) identity=$2 ;;
    --certificate-github-workflow-repository) repository=$2 ;;
  esac
  shift
done
[ "$identity" = "$(cat "$FAKE/signer")" ] || exit 1
[ -z "$repository" ] || [ "$repository" = "$(cat "$FAKE/repository")" ]
"""


@pytest.fixture
def fake(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in {"docker": FAKE_DOCKER, "cosign": FAKE_COSIGN, "apk": "#!/bin/sh\nexit 0\n"}.items():
        tool = bin_dir / name
        tool.write_text(body)
        tool.chmod(0o755)
    (tmp_path / "fake").mkdir()
    return tmp_path


def verify(folder: Path) -> int:
    return subprocess.run(["sh", str(SCRIPTS / "updater.sh"), "verify", IMAGE, "old"], capture_output=True, text=True,
                          timeout=20, env=env(folder)).returncode


@pytest.mark.parametrize(("repository", "expected"), [(REPO, 0), ("someone/fork", 1)])
def test_the_provenance_must_come_from_this_repository(fake, repository, expected):
    """The identity is the workflow file of the tag. A workflow of another repository that calls release.yml as a
    reusable workflow would get the same identity; the certificate names the caller's repository."""
    state = setup(fake, new=stable("0.27.0"), signed="v0.27.0")
    (state / "repository").write_text(repository)
    (state / "tag").write_text("new")  # the pulled image
    assert verify(fake) == expected


def test_the_release_workflow_cannot_be_called_by_other_workflows():
    """The signer identity of updater.sh is this workflow file: it must stay the only way to run it."""
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "release.yml").read_text())
    triggers = workflow.get("on", workflow.get(True))  # YAML 1.1 reads the key "on" as true
    assert "push" in triggers and "workflow_call" not in triggers
    script = (SCRIPTS / "updater.sh").read_text()
    assert f"\nREPO={REPO}\n" in script and "$REPO/.github/workflows/release.yml@refs/tags/v" in script


def test_a_rolled_back_version_is_removed(fake):
    state = setup(fake, new=stable("0.27.0"), signed="v0.27.0", health="true 0 unhealthy")
    (state / "repository").write_text(REPO)
    log, status = run_update(fake)
    lines = log.splitlines()
    assert lines[-3:] == [f"tag old {IMAGE}", "compose up -d openampere", "image rm new"]
    assert "Die bisherige Version läuft wieder" in status


def test_a_version_that_started_is_kept(fake):
    state = setup(fake, new=stable("0.27.0"), signed="v0.27.0")
    (state / "repository").write_text(REPO)
    log, status = run_update(fake)
    assert "image rm" not in log and "Update installiert" in status


# ---- release link ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("url", "kept"), [
    ("https://github.com/Gr33ndev/OpenAmpere/releases/tag/v0.27.0", True),
    ("javascript:alert(1)", False),
    ("https://example.com/releases/tag/v0.27.0", False),
    ("https://github.com.example.com/x", False),
    ("http://github.com/Gr33ndev/OpenAmpere/releases/tag/v0.27.0", False),
    (None, False),
    (["https://github.com/"], False),
])
def test_the_release_link_only_goes_to_github(monkeypatch, url, kept):
    import json
    body = json.dumps({"tag_name": "v0.27.0", "html_url": url, "body": "", "published_at": None}).encode()
    monkeypatch.setattr(updates_module, "read_within", lambda *_, **__: ({}, body))
    latest = updates_module.fetch_latest()
    assert latest["version"] == "0.27.0" and latest["url"] == (url if kept else None)
