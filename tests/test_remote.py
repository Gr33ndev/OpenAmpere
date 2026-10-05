import json
import time

import pytest
from fastapi.testclient import TestClient

from openampere.api import create_app
from openampere.remote import Remote
from openampere.runtime import Runtime
from openampere.storage import Storage

from conftest import login


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    rt = Runtime({}, Storage(tmp_path / "data" / "t.db"))
    monkeypatch.setattr(rt.config.storage, "path", str(tmp_path / "data" / "t.db"))
    return rt


def helper(remote: Remote, status: dict | None = None, ts: float | None = None) -> None:
    """What scripts/tailscale.sh writes: a sign of life and the output of `tailscale status --json`."""
    remote.folder.mkdir(parents=True, exist_ok=True)
    (remote.folder / "alive").write_text(str(int(ts if ts is not None else time.time())))
    if status is not None:
        (remote.folder / "status.json").write_text(json.dumps(status))


RUNNING = {"BackendState": "Running", "AuthURL": "",
           "Self": {"DNSName": "openampere.tail1234.ts.net.", "TailscaleIPs": ["100.101.102.103", "fd7a:115c::1"],
                    "UserID": 42},
           "User": {"42": {"LoginName": "anlage@example.org"}}}


def test_without_helper_nothing_can_be_set_up(runtime):
    remote = Remote(runtime)
    assert remote.view() == {"available": False, "state": "unavailable", "port": 8080}
    with pytest.raises(RuntimeError, match="Install-Script"):
        remote.request("login")
    helper(remote, ts=time.time() - 600)  # helper stopped long ago
    assert not remote.view()["available"]


def test_login_flow(runtime):
    remote = Remote(runtime)
    helper(remote, {"BackendState": "NeedsLogin", "AuthURL": ""})
    assert remote.view()["state"] == "off"

    remote.request("login", now=1000)
    assert (remote.folder / "request").read_text() == "login"
    helper(remote, ts=1001)
    assert remote.view(now=1001)["state"] == "starting"

    helper(remote, {"BackendState": "NeedsLogin", "AuthURL": "https://login.tailscale.com/a/abc123"}, ts=1005)
    view = remote.view(now=1005)
    assert view["state"] == "login" and view["login_url"] == "https://login.tailscale.com/a/abc123"
    assert view["address"] is None

    helper(remote, RUNNING, ts=1100)
    view = remote.view(now=1100)
    assert view["state"] == "connected" and view["login_url"] is None
    assert view["address"] == "http://openampere.tail1234.ts.net:8080"
    assert view["ip"] == "100.101.102.103" and view["account"] == "anlage@example.org"


def test_login_link_must_be_https(runtime):
    remote = Remote(runtime)
    helper(remote, {"BackendState": "NeedsLogin", "AuthURL": "javascript:alert(1)"})
    assert remote.view()["login_url"] is None


def test_no_link_after_a_while_is_an_error(runtime):
    remote = Remote(runtime)
    helper(remote, {"BackendState": "NeedsLogin"}, ts=1000)
    remote.request("login", now=1000)
    helper(remote, ts=1090)
    assert remote.view(now=1090)["state"] == "failed"


def test_device_approval_and_logout(runtime):
    remote = Remote(runtime)
    helper(remote, {"BackendState": "NeedsMachineAuth"})
    assert remote.view()["state"] == "approval"
    helper(remote, RUNNING)
    remote.request("logout")
    assert (remote.folder / "request").read_text() == "logout"
    assert runtime.storage.control_log()[0]["details"] == {"from": {"remote_access": "connected"},
                                                           "to": {"remote_access": "logout"}}
    with pytest.raises(ValueError):
        remote.request("funnel")


def test_endpoint_needs_login(runtime):
    client = TestClient(create_app(runtime))
    assert client.get("/api/remote").status_code == 401  # the login link is only for the owner
    assert client.post("/api/remote", json={"action": "login"}).status_code == 403
    login(client)
    assert client.get("/api/remote").json()["state"] == "unavailable"
    assert client.post("/api/remote", json={"action": "login"}).status_code == 409  # no tailscale container
    helper(Remote(runtime), {"BackendState": "NeedsLogin"})
    assert client.post("/api/remote", json={"action": "nonsense"}).status_code == 400
    assert client.post("/api/remote", json={"action": "login"}).json()["state"] == "starting"
