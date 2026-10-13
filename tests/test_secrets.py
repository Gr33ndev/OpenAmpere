# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
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


# GHSA-pgj6-8cf2-3qxp: the app never shows a stored secret again, so whoever has a session must not get it sent to
# their own server by changing the address it belongs to
async def test_a_stored_secret_is_not_sent_to_a_new_address(tmp_path):
    rt = Runtime({}, Storage(tmp_path / "t.db"))
    await rt.update_settings({"evcc.url": "http://evcc.local:7070", "evcc.password": "evcc-pass",
                              "notify.ntfy_url": "https://ntfy.sh/topic", "notify.ntfy_token": "tk_secret",
                              "meter.provider": "netze_bw", "meter.username": "owner", "meter.password": PASSWORD})
    await rt.update_settings({"tariff.feed_in_ct": 8.0})  # other settings: kept
    assert (rt.config.evcc.password, rt.config.notify.ntfy_token) == ("evcc-pass", "tk_secret")

    await rt.update_settings({"evcc.url": "http://attacker.example:7070", "notify.ntfy_url": "https://attacker.example/t",
                              "meter.username": "someone-else"})
    assert rt.config.evcc.password == "" and rt.config.notify.ntfy_token == "" and rt.config.meter.password == ""
    assert {"evcc.password", "notify.ntfy_token", "meter.password"}.isdisjoint(rt.storage.get_settings())

    await rt.update_settings({"evcc.url": "http://evcc2.local:7070", "evcc.password": "new-pass"})  # both at once
    assert rt.config.evcc.password == "new-pass"
    rt.storage.close()


def test_the_evcc_login_cookie_goes_only_to_the_address_it_came_from(tmp_path, monkeypatch):
    from openampere.evcc import Evcc
    rt = Runtime({}, Storage(tmp_path / "t.db"))
    evcc = Evcc(rt)
    sent = []
    headers = {"Set-Cookie": "auth=abc; Path=/"}

    def read_within(request, **kwargs):
        sent.append((request.full_url, request.get_header("Cookie")))
        return headers, b"{}"
    monkeypatch.setattr("openampere.evcc.read_within", read_within)
    monkeypatch.setattr(rt.config.evcc, "url", "http://evcc.local:7070")
    evcc._http("GET", "/api/state")
    evcc._http("GET", "/api/state")
    monkeypatch.setattr(rt.config.evcc, "url", "http://attacker.example:7070")
    headers = {}
    evcc._http("GET", "/api/state")
    assert sent == [("http://evcc.local:7070/api/state", None), ("http://evcc.local:7070/api/state", "auth=abc"),
                    ("http://attacker.example:7070/api/state", None)]
    rt.storage.close()
