# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Schema version of the database, step-by-step migrations, the copy before an upgrade and the rollback (#166)."""

import sqlite3
from collections import namedtuple

import pytest
from fastapi.testclient import TestClient

from openampere import storage as storage_module
from openampere.api import create_app
from openampere.runtime import Runtime
from openampere.storage import SchemaError, Storage

from conftest import login


def version_of(path) -> int:
    db = sqlite3.connect(path)
    try:
        return db.execute("PRAGMA user_version").fetchone()[0]
    finally:
        db.close()


def tables_of(path) -> set[str]:
    db = sqlite3.connect(path)
    try:
        return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        db.close()


def add_notes(db):
    db.execute("CREATE TABLE notes (ts REAL PRIMARY KEY, text TEXT NOT NULL)")


def add_note_author(db):
    db.execute("ALTER TABLE notes ADD COLUMN author TEXT")


@pytest.fixture
def version3(monkeypatch):
    """A future OpenAmpere with two more schema versions."""
    monkeypatch.setattr(storage_module, "MIGRATIONS", {1: add_notes, 2: add_note_author})
    monkeypatch.setattr(storage_module, "SCHEMA_VERSION", 3)
    return monkeypatch


def test_migrations_are_complete():
    assert sorted(storage_module.MIGRATIONS) == list(range(1, storage_module.SCHEMA_VERSION))


def test_a_new_database_gets_the_current_version_without_a_copy(tmp_path, version3):
    Storage(tmp_path / "t.db").close()
    assert version_of(tmp_path / "t.db") == 3 and "notes" in tables_of(tmp_path / "t.db")
    assert not list(tmp_path.glob("t.db.schema-*"))


def test_a_database_from_before_schema_versions_counts_as_version_1(tmp_path):
    path = tmp_path / "t.db"
    db = sqlite3.connect(path)  # as the first releases created it: no version, fewer columns
    db.executescript("CREATE TABLE samples (ts REAL PRIMARY KEY, pv REAL, house REAL, grid REAL, battery REAL, soc REAL);"
                     "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
                     "INSERT INTO samples VALUES (1750000000, 1, 2, 3, 4, 50);")
    db.close()
    storage = Storage(path)
    assert len(storage.samples(0, 2e9)) == 1 and "t_cell_min" in storage.samples(0, 2e9)[0]
    storage.close()
    assert version_of(path) == 1
    assert not list(tmp_path.glob("t.db.schema-*"))  # older versions read version 1 as they always did


def test_upgrade_copies_the_database_and_migrates_step_by_step(tmp_path, monkeypatch):
    path = tmp_path / "t.db"
    storage = Storage(path)
    storage.set_meta("installation_id", "before-upgrade")
    storage.close()
    (tmp_path / "t.db.schema-0").write_bytes(b"an older copy")

    monkeypatch.setattr(storage_module, "MIGRATIONS", {1: add_notes, 2: add_note_author})
    monkeypatch.setattr(storage_module, "SCHEMA_VERSION", 3)
    storage = Storage(path)
    assert storage.get_meta("installation_id") == "before-upgrade"
    storage.close()
    assert version_of(path) == 3
    db = sqlite3.connect(path)
    assert "author" in {row[1] for row in db.execute("PRAGMA table_info(notes)")}
    db.close()
    copy = tmp_path / "t.db.schema-1"
    assert version_of(copy) == 1 and "notes" not in tables_of(copy)  # the database before the upgrade
    assert not (tmp_path / "t.db.schema-0").exists()  # only the newest copy is kept
    assert not list(tmp_path.glob("*.partial"))


def test_a_failed_step_is_rolled_back_and_the_start_stops(tmp_path, monkeypatch):
    path = tmp_path / "t.db"
    Storage(path).close()

    def broken(db):
        db.execute("CREATE TABLE half_done (x)")
        raise sqlite3.OperationalError("step failed")

    monkeypatch.setattr(storage_module, "MIGRATIONS", {1: add_notes, 2: broken})
    monkeypatch.setattr(storage_module, "SCHEMA_VERSION", 3)
    with pytest.raises(sqlite3.OperationalError):
        Storage(path)
    assert version_of(path) == 2 and "half_done" not in tables_of(path)  # first step done, second not at all
    assert not list(tmp_path.glob("t.db.damaged-*"))  # a failed migration is no damage


def test_rollback_uses_the_copy_from_before_the_upgrade(tmp_path, monkeypatch):
    path = tmp_path / "t.db"
    storage = Storage(path)
    storage.set_meta("installation_id", "kept")
    storage.close()
    with monkeypatch.context() as newer:  # the new version upgrades the database and writes to it
        newer.setattr(storage_module, "MIGRATIONS", {1: add_notes, 2: add_note_author})
        newer.setattr(storage_module, "SCHEMA_VERSION", 3)
        storage = Storage(path)
        storage.set_meta("written_by", "newer version")
        storage.close()

    runtime = Runtime({}, Storage(path))  # the updater went back to the previous version
    client = TestClient(create_app(runtime))
    assert runtime.storage.get_meta("installation_id") == "kept"
    assert runtime.storage.get_meta("written_by") is None
    rollback = client.get("/api/status").json()["database"]["rollback"]
    assert rollback["version"] == 3
    kept = tmp_path / rollback["kept"].rsplit("/", 1)[-1]
    assert kept.name.startswith("t.db.newer-3-") and version_of(kept) == 3
    assert version_of(path) == 1 and not (tmp_path / "t.db.schema-1").exists()  # the copy is in use now

    login(client)
    assert client.delete("/api/database/rollback").status_code == 200
    assert client.get("/api/status").json()["database"]["rollback"] is None
    runtime.storage.close()


def test_a_newer_database_without_a_copy_stops_with_a_clear_message(tmp_path):
    path = tmp_path / "t.db"
    Storage(path).close()
    db = sqlite3.connect(path)
    db.execute("PRAGMA user_version=7")
    db.close()
    with pytest.raises(SchemaError, match="neueren Version"):
        Storage(path)
    assert version_of(path) == 7 and not list(tmp_path.glob("t.db.newer-*"))  # nothing was changed


def test_no_upgrade_without_space_for_the_copy(tmp_path, monkeypatch):
    path = tmp_path / "t.db"
    Storage(path).close()
    monkeypatch.setattr(storage_module, "MIGRATIONS", {1: add_notes, 2: add_note_author})
    monkeypatch.setattr(storage_module, "SCHEMA_VERSION", 3)
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(storage_module.shutil, "disk_usage", lambda _path: usage(10**9, 10**9, 1000))
    with pytest.raises(SchemaError, match="Speicherplatz"):
        Storage(path)
    assert version_of(path) == 1 and not list(tmp_path.glob("t.db.schema-*"))


def test_a_backup_from_a_newer_version_is_not_restored(tmp_path):
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    client = login(TestClient(create_app(runtime)))
    backup = tmp_path / "backup.db"
    backup.write_bytes(client.get("/api/backup").content)
    db = sqlite3.connect(backup)
    db.execute("PRAGMA user_version=99")
    db.close()
    response = client.post("/api/backup/restore", content=backup.read_bytes())
    assert response.status_code == 400 and "neueren Version" in response.json()["detail"]
    runtime.storage.close()


def test_the_start_stops_with_the_message(tmp_path, monkeypatch):
    import sys

    from openampere import __main__ as cli
    from openampere.runtime import Runtime

    monkeypatch.setattr(sys, "argv", ["openampere"])
    monkeypatch.setattr(cli, "check_data_dir", lambda _config: None)

    def newer(_config):
        raise SchemaError("Die Datenbank stammt von einer neueren Version von OpenAmpere.")

    monkeypatch.setattr(Runtime, "from_files", newer)  # imported in main(), see the health check (#167)
    with pytest.raises(SystemExit, match="neueren Version"):
        cli.main()
