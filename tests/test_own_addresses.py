# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Own host names set in the app (#437): the protection against DNS rebinding stays in place."""

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from openampere.api import create_app
from openampere.config import validate
from openampere.privacy import control_log_for_report
from openampere.runtime import Runtime
from openampere.storage import Storage

from conftest import login
from test_database_recovery import backup_of, craft, restart, start

OWN = "solar.example.de"


def app_client(tmp_path):
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    return runtime, TestClient(create_app(runtime))


def test_names_are_cleaned():
    assert validate({"server.allowed_hosts": [" Solar.Example.DE ", "", "solar.example.de", "proxy-1.example.org"]}) \
        == {"server.allowed_hosts": ["solar.example.de", "proxy-1.example.org"]}
    assert validate({"server.allowed_hosts": []}) == {"server.allowed_hosts": []}


def test_a_wildcard_is_refused():
    for wildcard in (["*"], ["*.example.de"], [OWN, "*"]):
        with pytest.raises(ValueError, match="DNS-Rebinding"):
            validate({"server.allowed_hosts": wildcard})


@pytest.mark.parametrize("bad", ["https://solar.example.de", "solar.example.de:8443", "solar.example.de/app",
                                 "user@solar.example.de", "solar example.de", ".example.de", "x" * 254])
def test_only_plain_names_are_taken(bad):
    with pytest.raises(ValueError, match="kein Hostname"):
        validate({"server.allowed_hosts": [bad]})


def test_the_number_of_names_is_limited():
    with pytest.raises(ValueError):
        validate({"server.allowed_hosts": [f"n{i}.example.de" for i in range(21)]})
    with pytest.raises(ValueError):
        validate({"server.allowed_hosts": OWN})  # a list, not a text


def test_a_name_saved_in_the_app_is_allowed_at_once_and_audited(tmp_path):
    _, client = app_client(tmp_path)
    login(client)
    assert client.get("/api/status", headers={"host": OWN}).status_code == 421
    saved = client.put("/api/settings", json={"server.allowed_hosts": ["Solar.Example.de"]})
    assert saved.status_code == 200 and saved.json()["values"]["server.allowed_hosts"] == [OWN]
    assert client.get("/api/status", headers={"host": OWN}).status_code == 200
    assert client.get("/api/status", headers={"host": "evil.example"}).status_code == 421

    entry = client.get("/api/control/log").json()["entries"][0]
    assert entry["action"] == "control_switches"
    assert entry["details"]["from"] == {"server.allowed_hosts": []}
    assert entry["details"]["to"] == {"server.allowed_hosts": [OWN]}

    refused = client.put("/api/settings", json={"server.allowed_hosts": ["*"]})
    assert refused.status_code == 400 and "DNS-Rebinding" in refused.json()["detail"]
    assert client.get("/api/status", headers={"host": "evil.example"}).status_code == 421


def test_the_names_need_a_login(tmp_path):
    _, client = app_client(tmp_path)
    login(client)
    client.put("/api/settings", json={"server.allowed_hosts": [OWN]})
    other = TestClient(client.app)  # another device in the home network, not logged in
    body = other.get("/api/settings").json()
    assert body["values"]["server.allowed_hosts"] is None and "server.allowed_hosts" in body["hidden"]
    denied = other.put("/api/settings", json={"server.allowed_hosts": ["evil.example"]}, headers={"x-openampere": "1"})
    assert denied.status_code == 401


def test_an_environment_variable_locks_the_names(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAMPERE_SERVER_ALLOWED_HOSTS", OWN)
    _, client = app_client(tmp_path)
    login(client)
    assert "server.allowed_hosts" in client.get("/api/settings").json()["locked"]
    assert client.put("/api/settings", json={"server.allowed_hosts": []}).status_code == 409
    assert client.get("/api/status", headers={"host": OWN}).status_code == 200


def test_a_restore_keeps_the_current_names(tmp_path):
    """A crafted backup must not allow a name of an attacker: the current names stay, whatever the backup holds."""
    path = tmp_path / "openampere.db"
    runtime, client = start(path)
    login(client)
    client.put("/api/settings", json={"server.allowed_hosts": [OWN], "tariff.feed_in_ct": 7})
    backup = craft(backup_of(client, tmp_path), tmp_path, {"server.allowed_hosts": ["evil.example"],
                                                           "tariff.feed_in_ct": 9.5})
    runtime.restart_hook = lambda: None
    assert client.post("/api/backup/restore", content=backup).status_code == 200

    runtime, client = restart(runtime, client)
    assert runtime.config.server.allowed_hosts == [OWN]
    assert runtime.config.tariff.feed_in_ct == 9.5  # the rest of the backup is restored
    runtime.storage.close()


def test_a_restore_of_an_older_backup_keeps_the_current_names(tmp_path):
    """The backup is upgraded when it is restored: the upgrade step that removes the names runs before, not after."""
    path = tmp_path / "openampere.db"
    runtime, client = start(path)
    login(client)
    client.put("/api/settings", json={"server.allowed_hosts": [OWN]})
    older = tmp_path / "older.db"
    older.write_bytes(backup_of(client, tmp_path))
    db = sqlite3.connect(older)
    db.execute("PRAGMA user_version=3")
    db.close()
    runtime.restart_hook = lambda: None
    assert client.post("/api/backup/restore", content=older.read_bytes()).status_code == 200

    runtime, client = restart(runtime, client)
    assert runtime.config.server.allowed_hosts == [OWN]
    runtime.storage.close()


def test_names_in_an_existing_database_are_removed_by_the_upgrade(tmp_path):
    """Before #437 the app could not set the names: in the settings they come from a crafted backup restored by an
    older version, which every start ignored. The upgrade removes them instead of letting them take effect."""
    path = tmp_path / "t.db"
    storage = Storage(path)
    storage.save_settings({"server.allowed_hosts": ["*"], "tariff.feed_in_ct": 9.5})
    storage.close()
    db = sqlite3.connect(path)
    db.execute("PRAGMA user_version=3")
    db.close()

    storage = Storage(path)
    assert storage.get_settings() == {"tariff.feed_in_ct": 9.5}
    runtime = Runtime({}, storage)
    assert runtime.config.server.allowed_hosts == [] and runtime.config.tariff.feed_in_ct == 9.5
    storage.close()


def test_an_upgrade_without_settings_or_with_broken_ones_works(tmp_path):
    for value in (None, "not json"):
        path = tmp_path / f"t-{value is None}.db"
        storage = Storage(path)
        storage.close()
        db = sqlite3.connect(path)
        if value is not None:
            db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('settings', ?)", (value,))
        db.execute("PRAGMA user_version=3")
        db.commit()
        db.close()
        Storage(path).close()


def test_the_names_are_pseudonymised_in_a_report(tmp_path):
    runtime, client = app_client(tmp_path)
    login(client)
    client.put("/api/settings", json={"server.allowed_hosts": [OWN]})
    entries = runtime.storage.control_log()
    report = control_log_for_report(runtime, entries, entries[0]["ts"])
    assert OWN not in json.dumps(report)
    assert report[0]["details"]["to"]["server.allowed_hosts"] == ["<Host-1>"]
