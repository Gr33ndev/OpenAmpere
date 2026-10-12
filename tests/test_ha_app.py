# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""OpenAmpere as a Home Assistant app (#300): sidebar (ingress), direct access, device search, updates, backups."""

import asyncio
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from openampere import discovery, ha_app
from openampere.api import create_app
from openampere.remote import Remote
from openampere.runtime import Runtime
from openampere.storage import Storage
from openampere.updates import Updates

from conftest import PASSWORD, login

HA_HOST = "ha.example.com"  # Home Assistant opened through a remote address: the sidebar passes its Host on
WRITE = {"x-openampere": "1"}


# the users of Home Assistant as config/auth/list lists them
USERS = [
    {"id": "owner", "is_owner": True, "is_active": True, "group_ids": ["system-users"]},
    {"id": "admin", "is_owner": False, "is_active": True, "group_ids": ["system-admin"]},
    {"id": "family", "is_owner": False, "is_active": True, "group_ids": ["system-users"]},
    {"id": "old-admin", "is_owner": False, "is_active": False, "group_ids": ["system-admin"]},
]


def clients(tmp_path, user="admin"):
    """The sidebar as an admin of Home Assistant (the Supervisor sends the user's id) and direct access by IP."""
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    app = create_app(runtime)
    sidebar = TestClient(app, client=(ha_app.INGRESS_PROXY, 50000),
                         headers={**WRITE, "host": HA_HOST, ha_app.USER_HEADER: user})
    direct = TestClient(app, client=("192.0.2.10", 50000), headers=WRITE)
    return runtime, sidebar, direct


@pytest.fixture
def app_mode(monkeypatch):
    monkeypatch.setenv("SUPERVISOR_TOKEN", "test-token")

    async def users():
        return USERS
    monkeypatch.setattr(ha_app, "home_assistant_users", users)


def test_docker_installations_do_not_trust_the_ingress_address(tmp_path, monkeypatch):
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    _, sidebar, _ = clients(tmp_path)
    assert sidebar.get("/api/status").status_code == 421  # the host check applies
    local = TestClient(sidebar.app, client=(ha_app.INGRESS_PROXY, 50000), headers=WRITE)
    status = local.get("/api/auth/status")
    assert status.json() == {"configured": False, "authenticated": False}
    assert status.headers["x-frame-options"] == "DENY"
    assert local.put("/api/settings", json={"tariff.feed_in_ct": 7}).json()["code"] == "setup_required"


def test_the_sidebar_needs_no_login_and_may_frame_the_app(tmp_path, app_mode):
    _, sidebar, _ = clients(tmp_path)
    status = sidebar.get("/api/auth/status")
    assert status.json() == {"configured": False, "authenticated": True, "home_assistant": "ingress"}
    assert status.headers["content-security-policy"] == "frame-ancestors 'self'"
    assert "x-frame-options" not in status.headers
    assert sidebar.put("/api/settings", json={"tariff.feed_in_ct": 7}).status_code == 200
    assert sidebar.get("/api/control/log").status_code == 200  # protected reads too


def test_the_sidebar_sets_no_cookies(tmp_path, app_mode):
    _, sidebar, direct = clients(tmp_path)
    response = sidebar.post("/api/auth/setup", json={"password": PASSWORD})
    assert response.status_code == 200
    assert "set-cookie" not in response.headers  # would belong to the address of Home Assistant
    login(direct)  # the password works for direct access


def test_the_sidebar_can_set_a_new_password_without_the_old_one(tmp_path, app_mode):
    _, sidebar, direct = clients(tmp_path)
    assert sidebar.post("/api/auth/setup", json={"password": PASSWORD}).status_code == 200
    login(direct)
    assert direct.post("/api/auth/password", json={"current": "", "new": "anders123"}).status_code == 401
    response = sidebar.post("/api/auth/password", json={"new": "anders123"})
    assert response.status_code == 200 and "set-cookie" not in response.headers
    assert direct.put("/api/settings", json={"tariff.feed_in_ct": 7}).json()["code"] == "login_required"
    assert direct.post("/api/auth/login", json={"password": "anders123"}).status_code == 200


def test_direct_access_is_locked_until_a_password_is_set_in_the_sidebar(tmp_path, app_mode):
    _, _, direct = clients(tmp_path)
    assert direct.get("/api/auth/status").json() == {"configured": False, "authenticated": False,
                                                      "home_assistant": "direct"}
    assert direct.post("/api/auth/setup", json={"password": PASSWORD}).status_code == 403
    assert direct.put("/api/settings", json={"tariff.feed_in_ct": 7}).json()["code"] == "setup_required"
    response = direct.get("/api/status")
    assert response.headers["x-frame-options"] == "DENY"


def test_forwarded_for_does_not_make_a_request_come_from_the_sidebar(tmp_path, app_mode):
    _, _, direct = clients(tmp_path)
    headers = {"x-forwarded-for": ha_app.INGRESS_PROXY, "x-real-ip": ha_app.INGRESS_PROXY}
    assert direct.get("/api/auth/status", headers=headers).json()["home_assistant"] == "direct"
    assert direct.put("/api/settings", json={"tariff.feed_in_ct": 7}, headers=headers).status_code == 401


def test_home_network_comes_from_the_supervisor(monkeypatch, app_mode):
    info = {"interfaces": [
        {"interface": "eth0", "connected": True,
         "ipv4": {"address": ["192.168.178.20/24"]}, "ipv6": {"address": ["fd00::20/64"]}},
        {"interface": "wlan0", "connected": False, "ipv4": {"address": ["10.0.0.5/24"]}},
        {"interface": "eth1", "connected": True, "ipv4": {"address": ["not an address"]}},
    ]}
    asked = []
    monkeypatch.setattr(ha_app, "supervisor", lambda path: asked.append(path) or info)
    assert discovery.local_prefixes() == ["192.168.178"]
    assert asked == ["/network/info"]


def test_no_home_network_when_the_supervisor_does_not_answer(monkeypatch, app_mode):
    def fail(path):
        raise OSError("no supervisor")
    monkeypatch.setattr(ha_app, "supervisor", fail)
    assert discovery.local_prefixes() == []


def test_remote_access_goes_through_home_assistant(tmp_path, app_mode):
    assert Remote(Runtime({}, Storage(tmp_path / "t.db"))).view()["home_assistant"] is True


def test_updates_come_through_home_assistant(tmp_path, app_mode):
    updates = Updates(Runtime({}, Storage(tmp_path / "t.db")), "1.0.0")
    assert updates.view()["home_assistant"] is True
    with pytest.raises(RuntimeError, match="Home Assistant"):
        updates.request()


def test_backup_copy_keeps_the_password_and_is_used_after_a_restore(tmp_path):
    db = tmp_path / "data" / "openampere.db"
    db.parent.mkdir()
    storage = Storage(db)
    storage.set_meta("auth", {"hash": "kept"})
    storage.set_meta("api_tokens", [{"id": "kept"}])
    copy = ha_app.write_backup_copy(db)  # while the server has the database open
    storage.close()
    with sqlite3.connect(copy) as check:
        keys = {row[0] for row in check.execute("SELECT key FROM meta")}
    assert {"auth", "api_tokens"} <= keys

    restored = tmp_path / "restored"  # what Home Assistant restores: the copy without the database
    restored.mkdir()
    (restored / ha_app.BACKUP_COPY).write_bytes(copy.read_bytes())
    assert ha_app.adopt_backup_copy(restored / "openampere.db")
    assert Storage(restored / "openampere.db").get_meta("auth") == {"hash": "kept"}
    assert not (restored / ha_app.BACKUP_COPY).exists()


def test_a_left_over_backup_copy_does_not_replace_the_database(tmp_path):
    db = tmp_path / "openampere.db"
    db.write_bytes(b"current")
    (tmp_path / ha_app.BACKUP_COPY).write_bytes(b"old copy")
    assert not ha_app.adopt_backup_copy(db)
    assert db.read_bytes() == b"current"
    assert not (tmp_path / ha_app.BACKUP_COPY).exists()


NETWORK = {"interfaces": [
    {"interface": "wlan0", "primary": False, "ipv4": {"address": ["10.0.0.5/24"]}},
    {"interface": "eth0", "primary": True, "ipv4": {"address": ["192.168.178.20/24"]}},
]}
DIRECT_URL = "http://192.168.178.20:8780"


def test_direct_address_uses_the_primary_interface_and_the_chosen_port():
    assert ha_app.direct_url({"network": {"8080/tcp": 8780}}, NETWORK) == DIRECT_URL
    assert ha_app.direct_url({"network": {"8080/tcp": None}}, NETWORK) is None  # turned off by the owner
    assert ha_app.direct_url({"network": {"8080/tcp": 8780}}, {"interfaces": []}) is None


def test_status_shows_the_direct_address_only_in_the_app(tmp_path, monkeypatch):
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    _, _, direct = clients(tmp_path)
    with direct:
        assert direct.get("/api/status").json()["home_assistant"] is None
    monkeypatch.setenv("SUPERVISOR_TOKEN", "test-token")
    answers = {"/addons/self/info": {"network": {"8080/tcp": 8780}}, "/network/info": NETWORK, "/discovery": {}}
    monkeypatch.setattr(ha_app, "supervisor", lambda path, body=None: answers[path])
    _, _, direct = clients(tmp_path / "app")
    with direct:  # runs the start of the app, which reads the Supervisor
        assert direct.get("/api/status").json()["home_assistant"] == {"direct_url": DIRECT_URL, "web_port": 8780,
                                                                      "https_port": None}


def test_status_shows_the_port_when_the_supervisor_knows_no_address(tmp_path, monkeypatch, app_mode):
    """Seen in the devcontainer of Home Assistant: no network interfaces. Direct access is on all the same."""
    answers = {"/addons/self/info": {"network": {"8080/tcp": 8780, "8443/tcp": 8743}},
               "/network/info": {"interfaces": []}, "/discovery": {}}
    monkeypatch.setattr(ha_app, "supervisor", lambda path, body=None: answers[path])
    _, _, direct = clients(tmp_path)
    with direct:
        assert direct.get("/api/status").json()["home_assistant"] == {"direct_url": None, "web_port": 8780,
                                                                      "https_port": 8743}


def test_the_app_announces_itself_to_home_assistant_without_a_token(tmp_path, monkeypatch, app_mode):
    sent = []
    monkeypatch.setattr(ha_app, "supervisor", lambda path, body=None: sent.append((path, body)) or {})
    runtime, _, direct = clients(tmp_path)
    with direct:
        pass
    discovery = dict(sent)["/discovery"]
    assert discovery["service"] == "openampere"
    assert discovery["config"]["port"] == runtime.config.server.tls_port
    assert discovery["config"]["fingerprint"] == runtime.tls.fingerprint
    assert discovery["config"]["installation_id"] == runtime.storage.get_meta("installation_id")
    assert set(discovery["config"]) == {"host", "port", "fingerprint", "installation_id"}  # no token


def test_docker_installations_announce_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    sent = []
    monkeypatch.setattr(ha_app, "supervisor", lambda path, body=None: sent.append(path) or {})
    _, _, direct = clients(tmp_path)
    with direct:
        pass
    assert sent == []


def integration_source(tmp_path, version="1.2.3"):
    source = tmp_path / "image" / "openampere"
    source.mkdir(parents=True)
    (source / "manifest.json").write_text(json.dumps({"domain": "openampere", "version": version}))
    (source / "__init__.py").write_text("# integration\n")
    (source / "__pycache__").mkdir()
    return source


def test_the_app_installs_its_integration_so_no_hacs_is_needed(tmp_path):
    config = tmp_path / "homeassistant"
    config.mkdir()
    assert ha_app.install_integration(integration_source(tmp_path), config) == {"result": "installed", "version": "1.2.3"}
    target = config / "custom_components" / "openampere"
    assert (target / "__init__.py").is_file() and not (target / "__pycache__").exists()
    assert (target / ha_app.INTEGRATION_MARKER).read_text().strip() == "1.2.3"
    assert ha_app.install_integration(integration_source(tmp_path / "again"), config)["result"] == "current"
    newer = integration_source(tmp_path / "newer", version="1.2.4")
    assert ha_app.install_integration(newer, config) == {"result": "updated", "version": "1.2.4"}
    assert json.loads((target / "manifest.json").read_text())["version"] == "1.2.4"
    assert sorted(p.name for p in target.parent.iterdir()) == ["openampere"]  # no half-copied folder left


def test_an_integration_from_hacs_is_left_alone(tmp_path):
    config = tmp_path / "homeassistant"
    hacs = config / "custom_components" / "openampere"
    hacs.mkdir(parents=True)
    (hacs / "manifest.json").write_text('{"version": "1.0.0"}')
    assert ha_app.install_integration(integration_source(tmp_path), config)["result"] == "foreign"
    assert (hacs / "manifest.json").read_text() == '{"version": "1.0.0"}'
    assert ha_app.install_integration(integration_source(tmp_path / "x"), tmp_path / "none")["result"] == "missing"


def test_the_notice_comes_once_in_the_language_of_home_assistant(tmp_path, monkeypatch, app_mode):
    monkeypatch.setenv(ha_app.INTEGRATION_ENV, json.dumps({"result": "updated", "version": "1.2.4"}))
    sent = []

    def supervisor(path, body=None):
        sent.append((path, body))
        return {"language": "en"} if path == "/core/api/config" else {}
    monkeypatch.setattr(ha_app, "supervisor", supervisor)
    asyncio.run(ha_app.HomeAssistant().notify_integration(tmp_path))
    asyncio.run(ha_app.HomeAssistant().notify_integration(tmp_path))  # the next start: no second notice
    notices = [body for path, body in sent if path.endswith("persistent_notification/create")]
    assert len(notices) == 1
    assert notices[0]["message"].startswith("The OpenAmpere integration is now at version 1.2.4.")
    assert ha_app.integration_notice({"result": "current", "version": "1.2.4"}) is None


def test_users_of_home_assistant_who_are_not_admins_can_only_view(tmp_path, app_mode):
    """panel_admin only hides the sidebar entry: the Supervisor forwards every user of Home Assistant (#300)."""
    _, admin, direct = clients(tmp_path)
    assert admin.post("/api/auth/setup", json={"password": PASSWORD}).status_code == 200
    login(direct)
    family = TestClient(admin.app, client=(ha_app.INGRESS_PROXY, 50000),
                        headers={**WRITE, "host": HA_HOST, ha_app.USER_HEADER: "family"})
    status = family.get("/api/auth/status").json()
    assert status == {"configured": True, "authenticated": False, "home_assistant": "ingress"}
    changed = family.put("/api/settings", json={"tariff.feed_in_ct": 7})
    assert changed.status_code == 403 and changed.json()["code"] == "home_assistant_admin"
    assert family.get("/api/control/log").status_code == 403  # protected reads too
    assert family.get("/api/status").status_code == 200  # viewing stays open, as in the home network
    assert family.post("/api/auth/password", json={"new": "geklaut123"}).status_code == 403
    assert family.post("/api/auth/setup", json={"password": "geklaut123"}).status_code == 403
    family.post("/api/auth/logout?everywhere=true")
    assert direct.put("/api/settings", json={"tariff.feed_in_ct": 8}).status_code == 200  # still logged in


def test_without_a_user_from_the_supervisor_nobody_is_admin(tmp_path, app_mode):
    """E.g. the watchdog of the Supervisor, which also connects from the ingress address."""
    _, sidebar, _ = clients(tmp_path)
    del sidebar.headers[ha_app.USER_HEADER]
    assert sidebar.get("/api/auth/status").json()["authenticated"] is False
    assert sidebar.put("/api/settings", json={"tariff.feed_in_ct": 7}).status_code == 403


def test_owner_counts_as_admin_but_not_an_inactive_admin():
    assert [user["id"] for user in USERS if ha_app.is_admin_user(user)] == ["owner", "admin"]


def test_admins_are_asked_again_but_not_on_every_request():
    calls, answer = [], [USERS]

    async def fetch():
        calls.append(1)
        return answer[0]
    admins = ha_app.Admins(fetch)
    check = lambda user, now: asyncio.run(admins.is_admin(user, now))  # noqa: E731
    assert check("admin", 0) and check("admin", 30) and len(calls) == 1
    assert not check("family", 33) and len(calls) == 2  # an unknown user: Home Assistant is asked again
    assert not check("family", 35) and len(calls) == 2  # but not again within a few seconds
    answer[0] = [user for user in USERS if user["id"] != "admin"]  # the admin rights were taken away
    assert check("admin", 60)  # still known from the answer at 33 s
    assert not check("admin", 94)  # at most a minute later they are gone
    assert not check(None, 200) and not check("", 200)


def test_without_an_answer_from_home_assistant_nobody_is_admin():
    async def fail():
        raise OSError("no Home Assistant")
    assert not asyncio.run(ha_app.Admins(fail).is_admin("admin", 0))


def test_the_users_come_from_the_websocket_api_of_home_assistant(monkeypatch):
    """The conversation with Home Assistant behind the Supervisor: authenticate with the app's token, list users."""
    import websockets
    monkeypatch.setenv("SUPERVISOR_TOKEN", "app-token")
    seen = []

    async def home_assistant(ws):
        await ws.send(json.dumps({"type": "auth_required"}))
        auth = json.loads(await ws.recv())
        seen.append(auth)
        if auth.get("access_token") != "app-token":
            await ws.send(json.dumps({"type": "auth_invalid"}))
            return
        await ws.send(json.dumps({"type": "auth_ok"}))
        command = json.loads(await ws.recv())
        seen.append(command)
        await ws.send(json.dumps({"id": command["id"], "type": "result", "success": True, "result": USERS}))

    async def run():
        async with websockets.serve(home_assistant, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            monkeypatch.setattr(ha_app, "SUPERVISOR_URL", f"http://127.0.0.1:{port}")
            users = await ha_app.home_assistant_users(timeout=5)
            monkeypatch.setenv("SUPERVISOR_TOKEN", "wrong")
            with pytest.raises(OSError):
                await ha_app.home_assistant_users(timeout=5)
            return users
    assert asyncio.run(run()) == USERS
    assert seen[0] == {"type": "auth", "access_token": "app-token"}
    assert seen[1] == {"id": 1, "type": "config/auth/list"}


def test_a_new_version_brings_the_notice_again(tmp_path, monkeypatch, app_mode):
    sent = []
    monkeypatch.setattr(ha_app, "supervisor", lambda path, body=None: sent.append(path) or {})
    for version in ("1.2.4", "1.2.4", "1.2.5"):
        monkeypatch.setenv(ha_app.INTEGRATION_ENV, json.dumps({"result": "updated", "version": version}))
        asyncio.run(ha_app.HomeAssistant().notify_integration(tmp_path))
    assert sum(path.endswith("persistent_notification/create") for path in sent) == 2


def test_the_root_step_writes_nothing_into_the_data_folder(tmp_path, monkeypatch, capsys):
    """ha-app-start runs it as root; the data folder and the config belong to the app user, who could place a link
    there or point the storage path anywhere (#300). It only prints its result."""
    import sys

    from openampere import __main__
    data = tmp_path / "data"
    data.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (data / "config.yaml").write_text(f"storage:\n  path: {elsewhere / 'x.db'}\n")
    monkeypatch.setenv("OPENAMPERE_CONFIG", str(data / "config.yaml"))
    monkeypatch.setattr(ha_app, "install_integration", lambda source, config: {"result": "installed", "version": "9"})
    monkeypatch.setattr(sys, "argv", ["openampere", "ha-integration"])
    __main__.main()
    assert json.loads(capsys.readouterr().out) == {"result": "installed", "version": "9"}
    assert sorted(p.name for p in data.iterdir()) == ["config.yaml"] and not any(elsewhere.iterdir())
