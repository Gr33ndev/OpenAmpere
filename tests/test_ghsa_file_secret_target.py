# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""GHSA-8mjv-f2x6-2v3h: a secret from config.yaml or an environment variable must not follow a new address.

The app never shows the evcc password, the ntfy token or the meter portal password. A secret saved in the app is
dropped when its address changes (GHSA-pgj6-8cf2-3qxp), but one from config.yaml or the environment cannot be dropped
by the app: changing its address in the app (or with a restored backup) would send it to whoever has a session."""

import pytest
from fastapi.testclient import TestClient

from openampere.api import create_app
from openampere.config import get_value, restore_settings
from openampere.runtime import Runtime
from openampere.storage import Storage

from conftest import login

FILE = {"evcc": {"url": "http://evcc.local:7070", "password": "file-evcc-pass"},
        "notify": {"ntfy_url": "https://ntfy.sh/owner-topic", "ntfy_token": "tk_file_token"},
        "meter": {"provider": "netze_bw", "username": "owner", "password": "file-meter-pass"}}
ENV = {"OPENAMPERE_EVCC_PASSWORD": "env-evcc-pass", "OPENAMPERE_NOTIFY_NTFY_TOKEN": "tk_env_token",
       "OPENAMPERE_METER_PASSWORD": "env-meter-pass"}
# a change of the address (or login) each secret belongs to
MOVES = [
    ("evcc.password", {"evcc.url": "http://attacker.example:7070"}),
    ("notify.ntfy_token", {"notify.ntfy_url": "https://attacker.example/topic"}),
    ("meter.password", {"meter.username": "someone-else"}),
]


def targets_only(file_values: dict) -> dict:
    """The addresses of FILE without the secrets (for the environment cases)."""
    return {section: {k: v for k, v in values.items() if k not in ("password", "ntfy_token")}
            for section, values in file_values.items()}


@pytest.mark.parametrize(("secret", "change"), MOVES)
async def test_a_secret_from_config_yaml_does_not_follow_a_new_address(tmp_path, secret, change):
    rt = Runtime(FILE, Storage(tmp_path / "t.db"))
    try:
        with pytest.raises(PermissionError, match="config.yaml"):
            await rt.update_settings(change)
        assert all(get_value(rt.config, key) != value for key, value in change.items())
        assert not set(change) & set(rt.storage.get_settings())
        # entered again together with the new address: the owner knows it, it goes there
        await rt.update_settings({**change, secret: "new-secret-1234"})
        assert get_value(rt.config, secret) == "new-secret-1234"
    finally:
        rt.storage.close()


@pytest.mark.parametrize(("secret", "change"), MOVES)
async def test_a_secret_from_the_environment_does_not_follow_a_new_address(tmp_path, monkeypatch, secret, change):
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    rt = Runtime(targets_only(FILE), Storage(tmp_path / "t.db"))
    try:
        with pytest.raises(PermissionError, match=r"Umgebungsvariable.*\(OPENAMPERE_(EVCC|NOTIFY|METER)_"):
            await rt.update_settings(change)
        assert not set(change) & set(rt.storage.get_settings())
        assert get_value(rt.config, secret) == ENV[f"OPENAMPERE_{secret.replace('.', '_').upper()}"]
    finally:
        rt.storage.close()


async def test_other_changes_and_clearing_the_address_still_work(tmp_path):
    rt = Runtime(FILE, Storage(tmp_path / "t.db"))
    try:
        await rt.update_settings({"tariff.feed_in_ct": 8.0, "evcc.url": FILE["evcc"]["url"]})  # same address
        assert rt.config.tariff.feed_in_ct == 8.0 and rt.config.evcc.password == "file-evcc-pass"
        await rt.update_settings({"evcc.url": "", "notify.ntfy_url": ""})  # no address: sent nowhere
        assert rt.config.evcc.url == "" and rt.config.notify.ntfy_url == ""
    finally:
        rt.storage.close()


def test_the_message_tells_the_owner_where_to_change_the_address(tmp_path):
    rt = Runtime(FILE, Storage(tmp_path / "t.db"))
    try:
        client = login(TestClient(create_app(rt)))
        response = client.put("/api/settings", json={"evcc.url": "http://attacker.example:7070"})
        assert response.status_code == 409
        detail = response.json()["detail"]
        assert detail == ("Die Zugangsdaten „Admin-Passwort von evcc“ stehen in der Datei config.yaml. Damit sie "
                          "nicht an eine andere Adresse gehen, bitte „evcc-Adresse“ (evcc.url) auch in der config.yaml "
                          "ändern oder die Zugangsdaten hier zusammen mit der neuen Einstellung noch einmal eingeben.")
        english = client.put("/api/settings", json={"evcc.url": "http://attacker.example:7070"},
                             headers={"x-openampere-lang": "en"}).json()["detail"]
        assert english != detail and "config.yaml" in english  # translated
    finally:
        rt.storage.close()


@pytest.mark.parametrize(("secret", "change"), MOVES)
def test_a_restored_backup_does_not_move_a_secret_from_outside_the_app(secret, change):
    current = {"tariff.feed_in_ct": 8.0}
    backup = {**change, "tariff.feed_in_ct": 9.5}
    restored = restore_settings(backup, current, outside={secret})
    assert restored == {"tariff.feed_in_ct": 9.5}  # the address stays where config.yaml or the environment set it
    assert restore_settings(backup, current) == backup  # without such a secret the backup's address is restored


def test_restore_through_the_app_keeps_the_address_of_a_secret_from_config_yaml(tmp_path):
    other = Runtime({}, Storage(tmp_path / "other" / "openampere.db"))
    try:
        other_client = login(TestClient(create_app(other)))
        assert other_client.put("/api/settings", json={"evcc.url": "http://attacker.example:7070"}).status_code == 200
        backup = other_client.get("/api/backup").content
    finally:
        other.storage.close()

    path = tmp_path / "openampere.db"
    rt = Runtime(FILE, Storage(path))
    try:
        client = login(TestClient(create_app(rt)))
        rt.restart_hook = lambda: None
        assert client.post("/api/backup/restore", content=backup).status_code == 200
    finally:
        rt.storage.close()
    rt = Runtime(FILE, Storage(path))  # the next start puts the backup in place
    try:
        assert rt.config.evcc.url == FILE["evcc"]["url"] and rt.config.evcc.password == "file-evcc-pass"
    finally:
        rt.storage.close()
