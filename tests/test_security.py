"""Access protection: password, sessions, CSRF/origin/host checks, secrets in backups."""

import sqlite3

from fastapi.testclient import TestClient

from openampere.api import create_app
from openampere.runtime import Runtime
from openampere.storage import Storage

from conftest import PASSWORD, login


def app_client(tmp_path):
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    return runtime, TestClient(create_app(runtime))


def test_writes_need_password_and_session(tmp_path):
    _, client = app_client(tmp_path)
    headers = {"x-openampere": "1"}
    assert client.get("/api/status").status_code == 200  # reading stays open in the home network
    first = client.put("/api/settings", json={"control.enabled": True}, headers=headers)
    assert first.status_code == 401 and first.json()["code"] == "setup_required"
    assert client.post("/api/auth/setup", json={"password": "123"}, headers=headers).status_code == 400  # too short
    assert client.post("/api/auth/setup", json={"password": PASSWORD}, headers=headers).status_code == 200
    assert client.put("/api/settings", json={"tariff.feed_in_ct": 7}, headers=headers).status_code == 200
    assert client.post("/api/auth/setup", json={"password": "andere123"}, headers=headers).status_code == 409

    other = TestClient(client.app)  # another device without session
    denied = other.put("/api/settings", json={"control.enabled": True}, headers=headers)
    assert denied.status_code == 401 and denied.json()["code"] == "login_required"
    assert other.get("/api/backup").status_code == 401
    assert other.post("/api/auth/login", json={"password": "falsch"}, headers=headers).status_code == 401
    assert other.post("/api/auth/login", json={"password": PASSWORD}, headers=headers).status_code == 200
    assert other.put("/api/settings", json={"tariff.feed_in_ct": 6}, headers=headers).status_code == 200


def test_cross_site_requests_are_rejected(tmp_path):
    _, client = app_client(tmp_path)
    client.post("/api/auth/setup", json={"password": PASSWORD}, headers={"x-openampere": "1"})
    # a form post from another web site: no custom header
    assert client.post("/api/import/cloud/start").status_code == 403
    # fetch from another origin with the header (would need CORS anyway)
    evil = {"x-openampere": "1", "origin": "http://evil.example"}
    assert client.put("/api/settings", json={"control.enabled": True}, headers=evil).status_code == 403
    # DNS rebinding: attacker domain resolving to the LAN IP
    rebinding = client.get("/api/status", headers={"host": "evil.example"})
    assert rebinding.status_code == 421
    for ok_host in ("192.168.178.50:8080", "openampere.local", "proxmox", "pi.fritz.box", "[::1]:8080"):
        assert client.get("/api/status", headers={"host": ok_host}).status_code == 200, ok_host


def test_login_lockout_after_failed_attempts(tmp_path):
    _, client = app_client(tmp_path)
    headers = {"x-openampere": "1"}
    client.post("/api/auth/setup", json={"password": PASSWORD}, headers=headers)
    codes = [client.post("/api/auth/login", json={"password": f"x{i}"}, headers=headers).status_code for i in range(6)]
    assert codes[:5] == [401] * 5 and codes[5] == 429


def test_backup_contains_no_secrets_or_sessions(tmp_path):
    runtime, client = app_client(tmp_path)
    headers = {"x-openampere": "1"}
    client.post("/api/auth/setup", json={"password": PASSWORD}, headers=headers)
    client.put("/api/settings", json={"cloud.api_key": "super-secret-key-1234"}, headers=headers)
    backup = client.get("/api/backup")
    assert backup.status_code == 200
    path = tmp_path / "backup.db"
    path.write_bytes(backup.content)
    assert b"super-secret-key-1234" not in backup.content
    db = sqlite3.connect(path)
    keys = {row[0] for row in db.execute("SELECT key FROM meta")}
    assert "sessions" not in keys


def test_control_switches_are_audited(tmp_path):
    runtime, client = app_client(tmp_path)
    headers = {"x-openampere": "1"}
    client.post("/api/auth/setup", json={"password": PASSWORD}, headers=headers)
    client.put("/api/settings", json={"control.enabled": True}, headers=headers)
    log = client.get("/api/control/log").json()["entries"]
    assert log[0]["action"] == "control_switches" and log[0]["details"]["to"] == {"control.enabled": True}


def test_reset_password_command(tmp_path, monkeypatch):
    import sys

    from openampere import __main__ as cli

    runtime, client = app_client(tmp_path)
    client.post("/api/auth/setup", json={"password": PASSWORD}, headers={"x-openampere": "1"})
    monkeypatch.setenv("OPENAMPERE_STORAGE_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(sys, "argv", ["openampere", "reset-password"])
    cli.main()  # works while the app (runtime) still holds the database
    assert client.get("/api/auth/status").json() == {"configured": False, "authenticated": False}




def test_logout_everywhere_needs_session(tmp_path):
    _, client = app_client(tmp_path)
    login(client)
    other = TestClient(client.app)
    other.headers["x-openampere"] = "1"
    other.post("/api/auth/logout?everywhere=true")
    assert client.get("/api/auth/status").json()["authenticated"]
