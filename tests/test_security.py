# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
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
    for evil_host in ("evil.example", "evil.box", "fritz.box.evil.example"):
        assert client.get("/api/status", headers={"host": evil_host}).status_code == 421, evil_host
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


def test_control_log_export_returns_every_entry(tmp_path):
    runtime, client = app_client(tmp_path)
    headers = {"x-openampere": "1"}
    client.post("/api/auth/setup", json={"password": PASSWORD}, headers=headers)
    for i in range(60):
        runtime.storage.log_control("consumer", {"from": {"on": False}, "to": {"on": True}}, False, f"test {i}")
    assert len(client.get("/api/control/log").json()["entries"]) == 50
    assert len(client.get("/api/control/log?limit=0").json()["entries"]) == 60  # export (#153)


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


def test_backup_link_works_without_the_cookie_for_a_while(tmp_path, monkeypatch):
    """The iPhone home-screen app opens downloads in their own window, which does not share the login cookie (#13)."""
    _, client = app_client(tmp_path)
    login(client)
    assert TestClient(client.app).post("/api/backup/link").status_code == 403  # not for other sites
    url = client.post("/api/backup/link").json()["url"]
    window = TestClient(client.app)  # no cookie, like the separate window on the iPhone
    assert window.get("/api/backup").status_code == 401
    assert window.get("/api/backup?token=guessed").status_code == 401
    assert window.get(url).status_code == 200
    import openampere.api as api_module
    later = api_module.time.time() + 601
    monkeypatch.setattr(api_module.time, "time", lambda: later)
    assert window.get(url).status_code == 401  # expired after 10 minutes


PRIVATE_VALUES = {"meter.provider": "netze_bw", "meter.username": "owner@example.org",
                  "meter.meter_ids": ["METER-AAA", "METER-BBB"], "notify.ntfy_url": "https://ntfy.sh/secret-topic-xyz",
                  "pv.installed_kwp": 9.5, "pv.commissioning_date": "2020-06-01", "battery.capacity_kwh": 10.0,
                  "cloud.api_key": "super-secret-key-wxyz"}
HIDDEN = {"meter.username", "meter.meter_ids", "notify.ntfy_url"}


def test_settings_hide_private_values_without_login(tmp_path):
    """Reads are open on the home network; values that identify the owner or give access to data need a login (#164)."""
    _, client = app_client(tmp_path)
    login(client)
    assert client.put("/api/settings", json=PRIVATE_VALUES, headers={"x-openampere": "1"}).status_code == 200

    shown = client.get("/api/settings").json()  # logged in: everything as before
    assert shown["hidden"] == []
    assert shown["values"]["meter.username"] == "owner@example.org"
    assert shown["values"]["meter.meter_ids"] == ["METER-AAA", "METER-BBB"]
    assert shown["values"]["notify.ntfy_url"] == "https://ntfy.sh/secret-topic-xyz"
    assert shown["values"]["pv.installed_kwp"] == 9.5 and shown["values"]["pv.commissioning_date"] == "2020-06-01"
    assert shown["values"]["battery.capacity_kwh"] == 10.0
    assert shown["secrets"]["cloud.api_key"] == {"set": True, "hint": "…wxyz"}

    other = TestClient(client.app)  # another device in the home network, not logged in
    response = other.get("/api/settings")
    assert response.status_code == 200
    for private in ("owner@example.org", "METER-AAA", "secret-topic-xyz", "wxyz"):
        assert private not in response.text, private
    body = response.json()
    assert set(body["hidden"]) == HIDDEN
    assert all(body["values"][key] is None for key in HIDDEN)
    assert body["secrets"]["cloud.api_key"] == {"set": True, "hint": None}
    # the rest stays readable, also the facts about the plant: whoever is on the home network is at the house
    assert body["values"]["meter.provider"] == "netze_bw"
    assert body["values"]["pv.installed_kwp"] == 9.5 and body["values"]["pv.commissioning_date"] == "2020-06-01"
    assert body["values"]["battery.capacity_kwh"] == 10.0
    eeg = other.get("/api/tariffs").json()["eeg"]
    assert eeg["commissioning_date"] == "2020-06-01" and eeg["installed_kwp"] == 9.5


def test_settings_hidden_before_a_password_is_set(tmp_path):
    _, client = app_client(tmp_path)
    assert set(client.get("/api/settings").json()["hidden"]) == HIDDEN


def test_hidden_placeholders_are_not_saved(tmp_path):
    """A form loaded without login holds null for hidden values; saving it after login keeps the real ones (#164)."""
    _, client = app_client(tmp_path)
    login(client)
    client.put("/api/settings", json=PRIVATE_VALUES, headers={"x-openampere": "1"})
    placeholders = {key: None for key in HIDDEN} | {"tariff.feed_in_ct": 7}
    saved = client.put("/api/settings", json=placeholders, headers={"x-openampere": "1"})
    assert saved.status_code == 200
    values = saved.json()["values"]
    assert values["meter.username"] == "owner@example.org" and values["meter.meter_ids"] == ["METER-AAA", "METER-BBB"]
    assert values["notify.ntfy_url"] == "https://ntfy.sh/secret-topic-xyz"
    assert values["pv.installed_kwp"] == 9.5 and values["pv.commissioning_date"] == "2020-06-01"
    assert values["battery.capacity_kwh"] == 10.0 and values["tariff.feed_in_ct"] == 7


def test_diagnostics_need_login(tmp_path):
    """The last report can hold the full serial number (#164)."""
    _, client = app_client(tmp_path)
    assert client.get("/api/diagnostics").json()["code"] == "setup_required"
    login(client)
    assert client.get("/api/diagnostics").status_code == 200
    denied = TestClient(client.app).get("/api/diagnostics")
    assert denied.status_code == 401 and denied.json()["code"] == "login_required"


def test_api_docs_are_switched_off(tmp_path):
    """Not needed by the app and open on the home network (#164). Unknown paths get the web app, if it is built."""
    _, client = app_client(tmp_path)
    for path in ("/docs", "/redoc", "/openapi.json"):
        response = client.get(path)
        assert response.status_code in (200, 404), path
        assert "swagger" not in response.text.lower() and "redoc" not in response.text.lower(), path
        assert '"openapi"' not in response.text, path


SWITCH = {"name": "Pumpe", "kind": "http", "power_w": 1000,
          "url_on": "http://pump.local/cm?user=admin&password=s3cret&cmnd=Power%20On",
          "url_off": "http://pump.local/cm?user=admin&password=s3cret&cmnd=Power%20Off"}


def test_switch_addresses_need_login(tmp_path):
    """Switch addresses often hold the password of the device; reads are open on the home network."""
    _, client = app_client(tmp_path)
    login(client)
    saved = client.put("/api/consumers", json={"consumers": [SWITCH]}).json()["consumers"][0]
    assert saved["url_on"] == SWITCH["url_on"]  # logged in: shown for editing
    assert client.get("/api/consumers").json()["consumers"][0]["url_off"] == SWITCH["url_off"]

    other = TestClient(client.app)  # another device in the home network, not logged in
    response = other.get("/api/consumers")
    assert response.status_code == 200 and "s3cret" not in response.text
    shown = response.json()["consumers"][0]
    assert shown["url_on"] is None and shown["url_off"] is None and shown["name"] == "Pumpe"


def test_hidden_switch_addresses_are_kept_when_saving(tmp_path):
    """The form loaded without login holds null; saving it after login keeps the stored addresses."""
    _, client = app_client(tmp_path)
    login(client)
    c_id = client.put("/api/consumers", json={"consumers": [SWITCH]}).json()["consumers"][0]["id"]
    renamed = {**SWITCH, "id": c_id, "name": "Wärmepumpe", "url_on": None, "url_off": None}
    saved = client.put("/api/consumers", json={"consumers": [renamed]}).json()["consumers"][0]
    assert saved["name"] == "Wärmepumpe"
    assert (saved["url_on"], saved["url_off"]) == (SWITCH["url_on"], SWITCH["url_off"])
    # a new device without addresses is still refused
    assert client.put("/api/consumers", json={"consumers": [{**SWITCH, "url_on": None}]}).status_code == 400
