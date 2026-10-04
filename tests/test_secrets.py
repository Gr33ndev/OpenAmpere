"""Secrets are encrypted in the database and never readable from the database file alone."""

import os
import stat

from openampere.runtime import Runtime
from openampere.secretbox import PREFIX, SecretBox
from openampere.storage import Storage

PASSWORD = "Portal-Passwort-4711"


def db_bytes(path) -> bytes:
    return b"".join(open(f"{path}{s}", "rb").read() for s in ("", "-wal") if os.path.exists(f"{path}{s}"))


def test_box_round_trip_and_key_file(tmp_path):
    box = SecretBox(tmp_path / "secret.key")
    sealed = box.encrypt("meter.password", PASSWORD)
    assert sealed.startswith(PREFIX) and PASSWORD not in sealed
    assert sealed != box.encrypt("meter.password", PASSWORD)  # random nonce
    assert box.decrypt("meter.password", sealed) == PASSWORD
    assert box.decrypt("evcc.password", sealed) == ""  # bound to the setting's name
    assert box.encrypt("x", "") == "" and box.decrypt("x", "alt") == "alt"
    assert stat.S_IMODE(os.stat(tmp_path / "secret.key").st_mode) == 0o600
    # another key, e.g. the database was copied without secret.key: the secret is gone, nothing crashes
    assert SecretBox(tmp_path / "other.key").decrypt("meter.password", sealed) == ""


async def test_settings_store_secrets_encrypted(tmp_path):
    db = tmp_path / "data" / "t.db"
    rt = Runtime({}, Storage(db))
    view = await rt.update_settings({"meter.password": PASSWORD, "evcc.url": "http://evcc.local:7070"})
    assert rt.config.meter.password == PASSWORD
    assert view["secrets"]["meter.password"] == {"set": True, "hint": None}  # no part of a password is shown
    stored = rt.storage.get_settings()
    assert stored["meter.password"].startswith(PREFIX) and stored["evcc.url"] == "http://evcc.local:7070"
    assert PASSWORD.encode() not in db_bytes(db)
    assert (tmp_path / "data" / "secret.key").exists()

    rt.storage.close()
    again = Runtime({}, Storage(db))  # after a restart the app can read it without anyone logging in
    assert again.config.meter.password == PASSWORD
    again.storage.close()


def test_plain_secrets_of_older_versions_get_encrypted(tmp_path):
    db = tmp_path / "t.db"
    storage = Storage(db)
    storage.save_settings({"evcc.password": PASSWORD, "cloud.api_key": "", "evcc.url": "http://evcc.local:7070"})
    rt = Runtime({}, storage)
    assert rt.config.evcc.password == PASSWORD
    assert storage.get_settings()["evcc.password"].startswith(PREFIX)
    assert storage.get_settings()["cloud.api_key"] == ""
    assert PASSWORD.encode() not in db_bytes(db)
    storage.close()
