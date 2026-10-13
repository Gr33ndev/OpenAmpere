# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Hardening of the backup restore and the access state (#350)."""

import json
import sqlite3

import pytest
from conftest import PASSWORD, login
from fastapi.testclient import TestClient
from test_charging import raised_by_0_23_1
from test_database_recovery import backup_of, restart, start

from openampere import charging, ha_app
from openampere import storage as storage_module
from openampere.api import create_app
from openampere.storage import KEEP_ON_RESTORE, RESTORE_SUFFIX, Storage

NEW_PASSWORD = "nach-dem-hochladen-1"
RAISED = {"original": 20, "raised": 61, "target": 61, "ts": 1}


# ---- the reserve 0.23.1 raised (#320) -----------------------------------

@pytest.mark.parametrize("raised", [
    {"original": -1, "raised": 30},
    {"original": "20", "raised": 61},
    {"original": 20.5, "raised": 61},
    {"original": True, "raised": 61},
    {"original": 120, "raised": 61},
    {"original": 20, "raised": "61"},
    {"original": None, "raised": 61},
    ["original", 20],
])
async def test_an_invalid_remembered_reserve_is_not_written(tmp_path, raised):
    sim, server, runtime, grid = await raised_by_0_23_1(tmp_path, raised=30, current=30)
    runtime.storage.set_meta(charging.RAISED_RESERVE, raised)
    async with server:
        try:
            await grid.tick(now=10_000)
            assert sim._get_setting("min_soc_on_grid") == 30
            assert runtime.storage.get_meta(charging.RAISED_RESERVE) is None
            assert not [e for e in runtime.storage.control_log() if e["action"] == "grid_charging_reserve"]
        finally:
            await runtime.collector.stop()


async def test_a_low_reserve_the_inverter_had_is_set_back(tmp_path):
    """A reserve below 10 % that was set on the inverter before 0.23.1 raised it counts as allowed (#313)."""
    sim, server, runtime, grid = await raised_by_0_23_1(tmp_path, original=5, raised=61, current=61)
    sim._put("min_soc", 0, sim.map.settings)
    sim.on_write(sim.map.settings["min_soc"].address)
    async with server:
        try:
            await grid.tick(now=10_000)
            assert sim._get_setting("min_soc_on_grid") == 5
            assert runtime.storage.get_meta(charging.RAISED_RESERVE) is None
        finally:
            await runtime.collector.stop()


async def test_a_reserve_outside_the_current_limits_is_not_set_back(tmp_path):
    """The charge limit was lowered below the remembered reserve since: setting it back would break the limits."""
    sim, server, runtime, grid = await raised_by_0_23_1(tmp_path, original=60, raised=30, current=30)
    sim._put("max_soc", 50, sim.map.settings)
    sim.on_write(sim.map.settings["max_soc"].address)
    async with server:
        try:
            await grid.tick(now=10_000)
            assert sim._get_setting("min_soc_on_grid") == 30
            assert runtime.storage.get_meta(charging.RAISED_RESERVE) is None
        finally:
            await runtime.collector.stop()


async def test_the_reserve_is_not_written_to_a_device_without_control(tmp_path):
    sim, server, runtime, grid = await raised_by_0_23_1(tmp_path)
    async with server:
        try:
            runtime.collector.device.supports_control = False
            await grid.tick(now=10_000)
            assert sim._get_setting("min_soc_on_grid") == 61
            assert runtime.storage.get_meta(charging.RAISED_RESERVE) == RAISED  # kept for a device that may be controlled
        finally:
            await runtime.collector.stop()


def test_a_backup_neither_brings_back_nor_drops_the_raised_reserve(tmp_path):
    assert charging.RAISED_RESERVE in KEEP_ON_RESTORE and charging.STOPS_AT_RESERVE in KEEP_ON_RESTORE
    path = tmp_path / "openampere.db"
    runtime, client = start(path)
    login(client)
    runtime.storage.set_meta(charging.RAISED_RESERVE, {"original": 0, "raised": 30})
    backup = tmp_path / "backup.db"
    backup.write_bytes(backup_of(client, tmp_path))
    with sqlite3.connect(backup) as check:
        assert check.execute("SELECT COUNT(*) FROM meta WHERE key=?", (charging.RAISED_RESERVE,)).fetchone()[0] == 0
    db = sqlite3.connect(backup)  # an old backup, or one made by 0.23.1 itself
    with db:
        db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (charging.RAISED_RESERVE, json.dumps(RAISED)))
    db.close()
    runtime.storage.delete_meta(charging.RAISED_RESERVE)
    runtime.restart_hook = lambda: None
    assert client.post("/api/backup/restore", content=backup.read_bytes()).status_code == 200
    runtime, client = restart(runtime, client)
    assert runtime.storage.get_meta(charging.RAISED_RESERVE) is None  # not brought back
    runtime.storage.set_meta(charging.RAISED_RESERVE, RAISED)  # pending: stays after a restore of an older backup
    runtime.restart_hook = lambda: None
    assert client.post("/api/backup/restore", content=backup_of(client, tmp_path)).status_code == 200
    runtime, client = restart(runtime, client)
    assert runtime.storage.get_meta(charging.RAISED_RESERVE) == RAISED
    runtime.storage.close()


# ---- changes between the upload and the next start -----------------------

def test_a_password_change_and_logout_after_the_upload_stay(tmp_path):
    """The restore is applied at the next start: what changed in between (password, logout everywhere, a revoked app
    access) is taken from the database it replaces, not from the time of the upload."""
    path = tmp_path / "openampere.db"
    runtime, client = start(path)
    login(client)
    runtime.storage.set_meta("api_tokens", [{"id": "old-app"}])
    backup = backup_of(client, tmp_path)
    runtime.restart_hook = lambda: None
    assert client.post("/api/backup/restore", content=backup).status_code == 200
    assert (tmp_path / f"openampere.db{RESTORE_SUFFIX}").is_file()

    # before the restart: another password, logged out everywhere, the app access revoked
    assert client.post("/api/auth/password", json={"current": PASSWORD, "new": NEW_PASSWORD}).status_code == 200
    assert client.post("/api/auth/logout?everywhere=true").status_code == 200
    runtime.storage.delete_meta("api_tokens")

    runtime, client = restart(runtime, client)
    assert runtime.storage.get_meta("database_restored") is not None  # the backup was restored
    assert client.get("/api/auth/status").json()["authenticated"] is False  # the logout stays
    assert runtime.storage.get_meta("api_tokens") is None
    anonymous = TestClient(create_app(runtime))
    anonymous.headers["x-openampere"] = "1"
    assert anonymous.post("/api/auth/login", json={"password": PASSWORD}).status_code == 401
    assert anonymous.post("/api/auth/login", json={"password": NEW_PASSWORD}).status_code == 200
    runtime.storage.close()


def test_without_the_current_access_state_the_backup_is_not_restored(tmp_path, monkeypatch):
    path = tmp_path / "openampere.db"
    storage = Storage(path)
    storage.set_meta("auth", {"salt": "s", "hash": "current"})
    storage.close()
    pending = tmp_path / f"openampere.db{RESTORE_SUFFIX}"
    db = sqlite3.connect(pending)
    with db:
        db.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        db.execute("INSERT INTO meta VALUES ('auth', ?)", (json.dumps({"salt": "s", "hash": "old"}),))
    db.close()

    def full_disk(_path):
        raise sqlite3.OperationalError("database or disk is full")
    monkeypatch.setattr(storage_module, "_current_access_rows", full_disk)
    storage = Storage(path)  # the old password must not come back
    assert storage.get_meta("auth") == {"salt": "s", "hash": "current"}
    assert storage.get_meta("database_restored") is None and not pending.exists()
    storage.close()


# ---- the control log ------------------------------------------------------

def test_every_restore_is_in_the_control_log_with_who_restored_it(tmp_path):
    path = tmp_path / "openampere.db"
    runtime, client = start(path)
    login(client)
    backup = tmp_path / "backup.db"
    backup.write_bytes(backup_of(client, tmp_path))
    runtime.restart_hook = lambda: None
    assert client.post("/api/backup/restore", content=backup.read_bytes()).status_code == 200  # no switch changed
    runtime, client = restart(runtime, client)
    entry = runtime.storage.control_log()[0]
    assert entry["action"] == "control_switches" and entry["result"] == "Datensicherung wiederhergestellt"
    assert entry["details"] == {"from": {}, "to": {}}  # in the web app itself
    assert client.get("/api/control/log").json()["entries"][0]["note"] is None  # the sentence says it already

    runtime.storage.stage_restore(backup, source="Admin (Home Assistant)")
    runtime, client = restart(runtime, client)
    assert runtime.storage.control_log()[0]["details"]["source"] == "Admin (Home Assistant)"
    runtime.storage.close()


# ---- Home Assistant backups -------------------------------------------------

def test_a_home_assistant_backup_has_no_sessions_and_asks_to_check_the_access(tmp_path):
    db = tmp_path / "data" / "openampere.db"
    db.parent.mkdir()
    storage = Storage(db)
    storage.set_meta("auth", {"hash": "kept"})
    storage.set_meta("sessions", {"token": 1})
    copy = ha_app.write_backup_copy(db)
    storage.close()
    with sqlite3.connect(copy) as check:
        keys = {row[0] for row in check.execute("SELECT key FROM meta")}
    assert "auth" in keys and "sessions" not in keys

    restored = tmp_path / "restored"
    restored.mkdir()
    old = sqlite3.connect(restored / ha_app.BACKUP_COPY)  # a copy made by an older version, with sessions
    with old:
        old.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        old.execute("INSERT INTO meta VALUES ('sessions', ?)", (json.dumps({"token": 1}),))
    old.close()
    assert ha_app.adopt_backup_copy(restored / "openampere.db")
    storage = Storage(restored / "openampere.db")
    assert storage.get_meta("sessions") is None
    assert storage.get_meta(ha_app.BACKUP_RESTORED)["ts"] > 0
    runtime_client = TestClient(create_app(_runtime(storage)))
    assert runtime_client.get("/api/status").json()["database"]["ha_backup"] is not None
    login(runtime_client)
    assert runtime_client.delete("/api/database/ha-backup").status_code == 200
    assert storage.get_meta(ha_app.BACKUP_RESTORED) is None
    storage.close()


def _runtime(storage):
    from openampere.runtime import Runtime
    return Runtime({}, storage)
