# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""A damaged database at the start, and restoring a backup in the app (#165)."""

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from openampere.api import create_app
from openampere.config import EDITABLE, restore_settings
from openampere.runtime import Runtime
from openampere.storage import RESTORE_SUFFIX, InvalidBackup, Storage, _is_damage, check_backup

from conftest import PASSWORD, login

NEW_PASSWORD = "anders4567"


def fill(storage: Storage, rows: int = 2000, first: int = 0) -> None:
    with storage._lock, storage._db:
        storage._db.executemany("INSERT INTO samples(ts, pv, house, grid, battery, soc) VALUES (?, 1, 2, 3, 4, 50)",
                                [(1_750_000_000 + i * 10,) for i in range(first, first + rows)])


def damage(path) -> None:
    """Overwrites the middle of the file, as a power cut during a write can leave it."""
    data = bytearray(path.read_bytes())
    for i in range(len(data) // 3, 2 * len(data) // 3):
        data[i] = 0xA5
    path.write_bytes(bytes(data))


def start(path) -> tuple[Runtime, TestClient]:
    runtime = Runtime({}, Storage(path))
    return runtime, TestClient(create_app(runtime))


def restart(runtime: Runtime, client: TestClient) -> tuple[Runtime, TestClient]:
    """What the process does after a restore: the next start, with the browser's cookies."""
    runtime.storage.close()
    runtime, again = start(runtime.storage.path)
    again.cookies = client.cookies
    again.headers.update(client.headers)
    return runtime, again


def test_a_damaged_database_is_kept_aside_and_the_app_starts_empty(tmp_path):
    path = tmp_path / "openampere.db"
    storage = Storage(path)
    fill(storage)
    storage.set_meta("auth", {"salt": "00", "hash": "x"})
    storage.close()
    damage(path)
    broken = path.read_bytes()

    runtime, client = start(path)  # failed with sqlite3.DatabaseError before
    status = client.get("/api/status").json()
    damaged = status["database"]["damaged"]
    assert damaged and damaged["file"].startswith(str((tmp_path / "openampere.db.damaged-").resolve()))
    assert (tmp_path / damaged["file"].rsplit("/", 1)[-1]).read_bytes() == broken  # kept as it was
    assert runtime.storage.samples(0, 2e9) == []
    assert client.get("/api/auth/status").json()["configured"] is False  # the owner must set a password again

    login(client)
    assert client.delete("/api/database/damaged").status_code == 200
    assert client.get("/api/status").json()["database"]["damaged"] is None
    runtime.storage.close()


def test_a_file_that_is_no_database_counts_as_damaged(tmp_path):
    path = tmp_path / "openampere.db"
    path.write_bytes(b"\x00garbage" * 1000)
    storage = Storage(path)
    assert storage.get_meta("database_damaged")
    assert list(tmp_path.glob("openampere.db.damaged-*"))
    storage.close()


def test_a_database_that_cannot_be_opened_is_not_moved(tmp_path):
    """Locked, read-only or a missing disk is no damage: moving the file aside would lose the data."""
    assert not _is_damage(sqlite3.OperationalError("database is locked"))
    path = tmp_path / "openampere.db"
    path.mkdir()  # cannot be opened as a file
    with pytest.raises(sqlite3.DatabaseError):
        Storage(path)
    assert path.is_dir() and not list(tmp_path.glob("*.damaged-*"))


def backup_of(client: TestClient, tmp_path):
    response = client.get("/api/backup")
    assert response.status_code == 200
    return response.content


def test_restore_brings_back_the_data_but_keeps_password_sessions_and_secrets(tmp_path):
    path = tmp_path / "openampere.db"
    runtime, client = start(path)
    login(client)
    client.put("/api/settings", json={"tariff.feed_in_ct": 8.1, "cloud.api_key": "key-in-backup-1234"})
    fill(runtime.storage, 10)
    backup = backup_of(client, tmp_path)

    # after the backup: other settings, another password and key, newer readings
    client.put("/api/settings", json={"tariff.feed_in_ct": 7.0, "cloud.api_key": "current-key-5678"})
    assert client.post("/api/auth/password", json={"current": PASSWORD, "new": NEW_PASSWORD}).status_code == 200
    fill(runtime.storage, 10, first=10)
    restarted = []
    runtime.restart_hook = lambda: restarted.append(True)

    response = client.post("/api/backup/restore", content=backup)
    assert response.status_code == 200 and response.json() == {"restarting": True} and restarted
    assert (tmp_path / f"openampere.db{RESTORE_SUFFIX}").is_file()
    assert not list(tmp_path.glob(".restore-upload-*"))

    runtime, client = restart(runtime, client)
    assert client.get("/api/auth/status").json() == {"configured": True, "authenticated": True}  # session kept
    assert runtime.config.tariff.feed_in_ct == 8.1  # from the backup
    assert runtime.config.cloud.api_key == "current-key-5678"  # secrets are not in a backup: the current ones stay
    assert len(runtime.storage.samples(0, 2e9)) == 10
    anonymous = TestClient(create_app(runtime))
    anonymous.headers["x-openampere"] = "1"
    assert anonymous.post("/api/auth/login", json={"password": PASSWORD}).status_code == 401  # the old password
    assert anonymous.post("/api/auth/login", json={"password": NEW_PASSWORD}).status_code == 200

    restored = client.get("/api/status").json()["database"]["restored"]
    kept = restored["kept"].rsplit("/", 1)[-1]
    assert kept.startswith("openampere.db.before-restore-")
    previous = sqlite3.connect(tmp_path / kept)
    assert previous.execute("SELECT COUNT(*) FROM samples").fetchone()[0] == 20  # the replaced database is kept
    previous.close()
    assert not (tmp_path / f"openampere.db{RESTORE_SUFFIX}").exists()
    runtime.storage.close()


def test_restore_after_a_damaged_database_clears_the_notice(tmp_path):
    path = tmp_path / "openampere.db"
    runtime, client = start(path)
    login(client)
    fill(runtime.storage, 5)
    backup = backup_of(client, tmp_path)
    runtime.storage.close()
    path.write_bytes(b"\x00" * 4096)

    runtime, client = start(path)
    assert client.get("/api/status").json()["database"]["damaged"]
    client.cookies.clear()
    login(client)  # a new password on the empty database
    assert client.post("/api/backup/restore", content=backup).json() == {"restarting": False}  # no server here
    runtime, client = restart(runtime, client)
    assert client.get("/api/status").json()["database"]["damaged"] is None
    assert len(runtime.storage.samples(0, 2e9)) == 5
    assert client.get("/api/auth/status").json()["authenticated"] is True
    runtime.storage.close()


def test_restore_needs_a_login(tmp_path):
    runtime, client = start(tmp_path / "openampere.db")
    login(client)
    backup = backup_of(client, tmp_path)
    anonymous = TestClient(create_app(runtime))
    anonymous.headers["x-openampere"] = "1"
    assert anonymous.post("/api/backup/restore", content=backup).status_code == 401
    assert not (tmp_path / f"openampere.db{RESTORE_SUFFIX}").exists()
    runtime.storage.close()


def test_restore_rejects_files_that_are_no_intact_openampere_backup(tmp_path):
    runtime, client = start(tmp_path / "openampere.db")
    login(client)
    other = tmp_path / "other.db"
    db = sqlite3.connect(other)
    db.execute("CREATE TABLE photos (name TEXT)")
    db.commit()
    db.close()
    damaged = tmp_path / "damaged.db"
    big = Storage(damaged)
    fill(big, 3000)
    big.close()
    damage(damaged)

    for content in (b"PK\x03\x04 a zip file", other.read_bytes(), damaged.read_bytes()):
        response = client.post("/api/backup/restore", content=content)
        assert response.status_code == 400, response.text
        assert "Sicherung" in response.json()["detail"]
    assert not (tmp_path / f"openampere.db{RESTORE_SUFFIX}").exists()
    assert not list(tmp_path.glob(".restore-upload-*"))
    runtime.storage.close()


def test_check_backup_accepts_a_backup(tmp_path):
    storage = Storage(tmp_path / "t.db")
    storage.backup(tmp_path / "b.db")
    check_backup(tmp_path / "b.db")
    with pytest.raises(InvalidBackup):
        check_backup(tmp_path / "t.db.lock")
    storage.close()


# a crafted backup must not change what the app cannot change, nor send the kept secrets somewhere else
CRAFTED = {"tariff.feed_in_ct": 9.5, "server.allowed_hosts": ["*"], "cloud.base_url": "https://attacker.example",
           "evcc.url": "http://attacker.example:7070", "notify.ntfy_url": "https://ntfy.sh/same-topic",
           "inverter.port": 99999, "evcc.password": "from-backup", "unknown.key": 1}


def craft(backup: bytes, tmp_path, settings: dict) -> bytes:
    path = tmp_path / "crafted.db"
    path.write_bytes(backup)
    db = sqlite3.connect(path)
    with db:
        db.execute("UPDATE meta SET value=? WHERE key='settings'", (json.dumps(settings),))
    db.close()
    return path.read_bytes()


def test_restore_takes_only_checked_settings_and_keeps_secrets_where_they_go(tmp_path):
    path = tmp_path / "openampere.db"
    runtime, client = start(path)
    login(client)
    client.put("/api/settings", json={"cloud.api_key": "cloud-key-1234", "evcc.url": "http://evcc.local:7070",
                                      "evcc.password": "evcc-pass", "notify.ntfy_url": "https://ntfy.sh/same-topic",
                                      "notify.ntfy_token": "tk_secret"})
    backup = craft(backup_of(client, tmp_path), tmp_path, CRAFTED)
    runtime.restart_hook = lambda: None
    assert client.post("/api/backup/restore", content=backup).status_code == 200

    runtime, client = restart(runtime, client)
    config = runtime.config
    assert config.tariff.feed_in_ct == 9.5  # a setting of the app: restored
    assert config.server.allowed_hosts == [] and config.cloud.base_url != CRAFTED["cloud.base_url"]  # not in the app
    assert config.inverter.port == 502  # outside the limits of the app: left out
    assert config.evcc.url == CRAFTED["evcc.url"] and config.evcc.password == ""  # another address: entered again
    assert config.notify.ntfy_token == "tk_secret"  # the same address: kept
    assert config.cloud.api_key == "cloud-key-1234"
    assert set(runtime.storage.get_settings()) <= set(EDITABLE)
    runtime.storage.close()


def test_settings_the_app_cannot_change_are_ignored_at_the_start(tmp_path):
    """Restored by an older version: the next start does not apply them."""
    storage = Storage(tmp_path / "t.db")
    storage.save_settings({"server.allowed_hosts": ["*"], "cloud.base_url": "https://attacker.example",
                           "tariff.feed_in_ct": 9.5})
    runtime = Runtime({}, storage)
    assert runtime.config.server.allowed_hosts == [] and runtime.config.cloud.base_url != "https://attacker.example"
    assert runtime.config.tariff.feed_in_ct == 9.5
    storage.close()


@pytest.mark.parametrize(("backup", "kept"), [
    ({"meter.provider": "netze_bw", "meter.username": "owner"}, True),
    ({"meter.provider": "netze_bw", "meter.username": "someone-else"}, False),
    ({"meter.provider": "none", "meter.username": "owner"}, False),
    ({}, False),  # the backup has no login: the password has nothing to go with
])
def test_a_kept_secret_goes_only_where_it_went_before(backup, kept):
    current = {"meter.provider": "netze_bw", "meter.username": "owner", "meter.password": "enc:pw",
               "cloud.api_key": "enc:key"}
    restored = restore_settings(backup, current)
    assert ("meter.password" in restored) is kept
    assert restored["cloud.api_key"] == "enc:key"  # the cloud address cannot be changed in the app
