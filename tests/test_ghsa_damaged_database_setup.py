# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""A damaged database must not open the first-password setup of an installation that had a password: anybody in the
home network could choose the password first and lock the owner out."""

import json
import sqlite3
import sys

from fastapi.testclient import TestClient

from openampere import __main__ as cli
from openampere import ha_app
from openampere.api import create_app
from openampere.auth import SESSION_COOKIE
from openampere.runtime import Runtime
from openampere.storage import Storage, _salvage_meta

from conftest import PASSWORD, login

WRITE = {"x-openampere": "1"}
OTHER_PASSWORD = "jemand-anderes"


def fill(storage: Storage, rows: int = 2000) -> None:
    with storage._lock, storage._db:
        storage._db.executemany("INSERT INTO samples(ts, pv, house, grid, battery, soc) VALUES (?, 1, 2, 3, 4, 50)",
                                [(1_750_000_000 + i * 10,) for i in range(rows)])


def damage_readings(path) -> None:
    """Overwrites the middle of the file (the readings), as a power cut during a write can leave it."""
    data = bytearray(path.read_bytes())
    for i in range(len(data) // 3, 2 * len(data) // 3):
        data[i] = 0xA5
    path.write_bytes(bytes(data))


def start(path) -> tuple[Runtime, TestClient]:
    runtime = Runtime({}, Storage(path))
    return runtime, TestClient(create_app(runtime))


def protected_installation(path, rows: int = 2000) -> str:
    """An installation with the owner's password; returns the owner's session."""
    runtime, owner = start(path)
    login(owner)
    fill(runtime.storage, rows)
    runtime.storage.close()
    return owner.cookies[SESSION_COOKIE]


def stranger(runtime: Runtime) -> TestClient:
    """Somebody in the home network without a cookie, with a client that sends the header and no Origin."""
    return TestClient(create_app(runtime), headers=WRITE)


def reset_password(path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAMPERE_STORAGE_PATH", str(path))
    monkeypatch.setattr(sys, "argv", ["openampere", "reset-password"])
    cli.main()


def test_the_password_is_taken_over_from_a_database_with_damaged_readings(tmp_path):
    path = tmp_path / "openampere.db"
    session = protected_installation(path)
    damage_readings(path)

    runtime, client = start(path)
    try:
        assert client.get("/api/status").json()["database"]["damaged"]  # the notice is still shown
        assert runtime.storage.samples(0, 2e9) == []
        assert client.get("/api/auth/status").json()["configured"] is True
        other = stranger(runtime)
        assert other.post("/api/auth/setup", json={"password": OTHER_PASSWORD}).status_code == 409
        assert other.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200  # the owner's password
        client.cookies.set(SESSION_COOKIE, session)
        assert client.get("/api/auth/status").json()["authenticated"] is True  # and session
    finally:
        runtime.storage.close()


def test_setup_stays_closed_until_reset_password_when_the_password_is_lost(tmp_path, monkeypatch):
    path = tmp_path / "openampere.db"
    protected_installation(path, rows=10)
    path.write_bytes(b"\x00" * 4096)  # nothing can be read any more

    runtime, client = start(path)
    try:
        assert client.get("/api/auth/status").json() == {"configured": False, "authenticated": False, "locked": True}
        other = stranger(runtime)
        response = other.post("/api/auth/setup", json={"password": OTHER_PASSWORD})
        assert response.status_code == 403 and "reset-password" in response.json()["detail"]
        assert other.get("/api/control/log").status_code == 401
        assert other.put("/api/settings", json={"tariff.feed_in_ct": 7}).json()["code"] == "setup_required"
        assert other.post("/api/auth/login", json={"password": OTHER_PASSWORD}).status_code == 401

        reset_password(path, monkeypatch)  # the owner on the server
        assert client.get("/api/auth/status").json() == {"configured": False, "authenticated": False}
        login(client)
        assert client.get("/api/auth/status").json() == {"configured": True, "authenticated": True}
    finally:
        runtime.storage.close()


def test_the_lock_survives_another_damage(tmp_path):
    path = tmp_path / "openampere.db"
    protected_installation(path, rows=10)
    path.write_bytes(b"\x00" * 4096)
    runtime, _ = start(path)
    fill(runtime.storage)
    runtime.storage.close()
    damage_readings(path)

    runtime, client = start(path)
    try:
        assert client.get("/api/auth/status").json()["locked"] is True
        assert stranger(runtime).post("/api/auth/setup", json={"password": OTHER_PASSWORD}).status_code == 403
    finally:
        runtime.storage.close()


def test_an_installation_without_a_password_can_still_set_one(tmp_path):
    path = tmp_path / "openampere.db"
    runtime, _ = start(path)
    fill(runtime.storage)
    runtime.storage.close()
    damage_readings(path)

    runtime, client = start(path)
    try:
        assert client.get("/api/auth/status").json() == {"configured": False, "authenticated": False}
        login(client)  # nothing was protected before, so nothing is lost
    finally:
        runtime.storage.close()


def test_a_password_that_cannot_be_read_counts_as_lost(tmp_path):
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    db.executemany("INSERT INTO meta VALUES (?, ?)", [("auth", json.dumps("x")), ("sessions", "{broken")])
    db.commit()
    db.close()
    assert _salvage_meta(path) == ({}, False)
    assert _salvage_meta(tmp_path / "missing.db") == ({}, False)


def app_mode(monkeypatch) -> None:
    monkeypatch.setenv("SUPERVISOR_TOKEN", "test-token")

    async def users():
        return [{"id": "admin", "name": "Admin", "is_owner": False, "is_active": True, "group_ids": ["system-admin"]}]
    monkeypatch.setattr(ha_app, "home_assistant_users", users)


def test_an_admin_of_home_assistant_sets_the_password_in_the_sidebar(tmp_path, monkeypatch):
    path = tmp_path / "openampere.db"
    protected_installation(path, rows=10)
    path.write_bytes(b"\x00" * 4096)
    app_mode(monkeypatch)

    runtime = Runtime({}, Storage(path))
    try:
        app = create_app(runtime)
        direct = TestClient(app, client=("192.0.2.10", 50000), headers=WRITE)
        assert direct.post("/api/auth/setup", json={"password": OTHER_PASSWORD}).status_code == 403
        sidebar = TestClient(app, client=(ha_app.INGRESS_PROXY, 50000),
                             headers={**WRITE, "host": "ha.example.com", ha_app.USER_HEADER: "admin"})
        assert sidebar.post("/api/auth/setup", json={"password": PASSWORD}).status_code == 200
        assert direct.get("/api/auth/status").json() == {"configured": True, "authenticated": False,
                                                         "home_assistant": "direct"}
        assert direct.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
    finally:
        runtime.storage.close()
