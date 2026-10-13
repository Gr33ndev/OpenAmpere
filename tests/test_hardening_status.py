# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Hardening of the public status, the evcc address and stored secrets (#353)."""

import asyncio
import shutil
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from openampere import control as control_module
from openampere.api import create_app
from openampere.config import Config, validate
from openampere.control import BatteryControl, ControlDisabled, ExportLimitControl
from openampere.diagnostics import report_markdown
from openampere.drivers.base import BatterySettings, ExportLimit, WorkMode
from openampere.runtime import Runtime
from openampere.secretbox import PREFIX
from openampere.storage import RESTORE_SUFFIX, Storage

from conftest import login

DAMAGED = {"ts": 1_750_000_000, "file": "/srv/owner/openampere/data/openampere.db.damaged-20260101-120000"}
RESTORED = {"ts": 1_750_000_100, "kept": "/srv/owner/openampere/data/openampere.db.before-restore-20260101-120000"}
ROLLBACK = {"ts": 1_750_000_200, "version": 3, "kept": "/srv/owner/openampere/data/openampere.db.newer-3-20260101"}


# ---- /api/status: what happened to the database -----------------------------------------------------------------

def _runtime_with_database_notices(tmp_path) -> Runtime:
    runtime = Runtime({}, Storage(tmp_path / "openampere.db"))
    # as older versions stored them: with the whole path
    runtime.storage.set_meta("database_damaged", DAMAGED)
    runtime.storage.set_meta("database_restored", RESTORED)
    runtime.storage.set_meta("database_rollback", ROLLBACK)
    return runtime


def test_status_without_login_says_only_whether_something_happened(tmp_path):
    runtime = _runtime_with_database_notices(tmp_path)
    try:
        anonymous = TestClient(create_app(runtime))
        login(TestClient(create_app(runtime)))  # a password is set: the anonymous client is not logged in
        database = anonymous.get("/api/status").json()["database"]
        assert database == {"damaged": True, "restored": True, "rollback": True}
        runtime.storage.delete_meta("database_damaged")
        assert anonymous.get("/api/status").json()["database"]["damaged"] is False
    finally:
        runtime.storage.close()


def test_status_with_login_shows_only_file_names(tmp_path):
    runtime = _runtime_with_database_notices(tmp_path)
    try:
        client = login(TestClient(create_app(runtime)))
        database = client.get("/api/status").json()["database"]
        assert database == {
            "damaged": {"ts": DAMAGED["ts"], "file": "openampere.db.damaged-20260101-120000"},
            "restored": {"ts": RESTORED["ts"], "kept": "openampere.db.before-restore-20260101-120000"},
            "rollback": {"ts": ROLLBACK["ts"], "version": 3, "kept": "openampere.db.newer-3-20260101"},
        }
    finally:
        runtime.storage.close()


def test_storage_keeps_only_the_file_name_of_a_damaged_database(tmp_path):
    path = tmp_path / "openampere.db"
    path.write_bytes(b"this is not a database" * 500)
    storage = Storage(path)
    try:
        damaged = storage.get_meta("database_damaged")
        assert damaged["file"].startswith("openampere.db.damaged-") and "/" not in damaged["file"]
        assert (tmp_path / damaged["file"]).is_file()
    finally:
        storage.close()


def test_storage_keeps_only_the_file_name_of_the_database_before_a_restore(tmp_path):
    backup = Storage(tmp_path / "backup.db")
    backup.close()
    Storage(tmp_path / "openampere.db").close()
    shutil.copy(tmp_path / "backup.db", tmp_path / f"openampere.db{RESTORE_SUFFIX}")
    storage = Storage(tmp_path / "openampere.db")
    try:
        kept = storage.get_meta("database_restored")["kept"]
        assert kept.startswith("openampere.db.before-restore-") and "/" not in kept
        assert (tmp_path / kept).is_file()
    finally:
        storage.close()


# ---- reading the battery settings and the export limit for the app ----------------------------------------------

class CountingDriver:
    def __init__(self) -> None:
        self.reads = 0
        self.limit_w = 6000
        self.max_soc = 90

    async def read_settings(self) -> BatterySettings:
        self.reads += 1
        await asyncio.sleep(0.05)  # a Modbus read takes a while: requests at the same time meet here
        return BatterySettings(work_mode=WorkMode.SELF_USE, min_soc=10, max_soc=self.max_soc, min_soc_on_grid=15)

    async def read_export_limit(self) -> ExportLimit:
        self.reads += 1
        await asyncio.sleep(0.05)
        return ExportLimit(supported=True, limit_w=self.limit_w, rated_power_w=10000)


def _runtime(driver) -> SimpleNamespace:
    config = Config()
    config.control.enabled, config.control.dry_run = True, True
    return SimpleNamespace(collector=SimpleNamespace(driver=driver, connected=True, device=None), config=config,
                           storage=SimpleNamespace(log_control=lambda *args, **kwargs: None))


async def test_requests_at_the_same_time_share_one_battery_read():
    driver = CountingDriver()
    control = BatteryControl(_runtime(driver))
    results = await asyncio.gather(*(control.read(shared=True) for _ in range(20)))
    assert driver.reads == 1
    assert all(r["max_soc"] == 90 and r["unreadable"] == [] for r in results)
    results[0]["max_soc"] = 0  # every request gets its own copy
    assert (await control.read(shared=True))["max_soc"] == 90
    assert driver.reads == 1  # still the shared result


async def test_shared_battery_read_expires_and_a_write_makes_it_out_of_date(monkeypatch):
    driver = CountingDriver()
    runtime = _runtime(driver)
    control = BatteryControl(runtime)
    await control.read(shared=True)
    driver.max_soc = 80
    runtime.config.control.enabled = False
    with pytest.raises(ControlDisabled):
        await control.write({"max_soc": 80})
    assert (await control.read(shared=True))["max_soc"] == 80 and driver.reads == 2
    monkeypatch.setattr(control_module, "SHARED_READ_S", 0.0)
    await control.read(shared=True)
    assert driver.reads == 3
    assert (await control.read())["max_soc"] == 80 and driver.reads == 4  # not shared: always a new read


async def test_export_limit_shares_reads_but_a_write_checks_the_current_value():
    driver = CountingDriver()
    control = ExportLimitControl(_runtime(driver))
    results = await asyncio.gather(*(control.read(shared=True) for _ in range(10)))
    assert driver.reads == 1 and all(r["limit_w"] == 6000 for r in results)
    driver.limit_w = 5000  # changed by another device
    result = await control.write(4000)  # test mode: checks against what the inverter holds now
    assert driver.reads == 2 and result["limit_w"] == 5000 and result["dry_run"] is True
    assert (await control.read(shared=True))["limit_w"] == 5000 and driver.reads == 3


async def test_a_failed_shared_read_reaches_every_request_and_is_not_kept():
    class Failing(CountingDriver):
        async def read_export_limit(self):
            self.reads += 1
            await asyncio.sleep(0.05)
            raise TimeoutError("no answer")

    driver = Failing()
    control = ExportLimitControl(_runtime(driver))
    results = await asyncio.gather(*(control.read(shared=True) for _ in range(5)), return_exceptions=True)
    assert driver.reads == 1 and all(isinstance(r, TimeoutError) for r in results)
    with pytest.raises(TimeoutError):
        await control.read(shared=True)
    assert driver.reads == 2


async def test_a_request_given_up_does_not_cancel_the_shared_read():
    driver = CountingDriver()
    control = BatteryControl(_runtime(driver))
    first = asyncio.create_task(control.read(shared=True))
    second = asyncio.create_task(control.read(shared=True))
    await asyncio.sleep(0.01)
    first.cancel()
    assert (await second)["max_soc"] == 90 and driver.reads == 1


# ---- addresses and secrets in the settings -----------------------------------------------------------------------

@pytest.mark.parametrize("url", ["http://admin:geheim@192.0.2.10:7070", "https://admin@evcc.example:7070/",
                                 "http://:geheim@192.0.2.10"])
def test_evcc_address_with_user_name_or_password_is_refused(url):
    with pytest.raises(ValueError, match="Benutzername und Passwort gehören nicht in die evcc-Adresse"):
        validate({"evcc.url": url})


def test_evcc_address_without_credentials_is_accepted():
    assert validate({"evcc.url": " http://192.0.2.10:7070 "}) == {"evcc.url": "http://192.0.2.10:7070"}
    assert validate({"evcc.url": "http://192.0.2.10:7070/api?x=a@b"}) == {"evcc.url": "http://192.0.2.10:7070/api?x=a@b"}
    assert validate({"evcc.url": ""}) == {"evcc.url": ""}


def test_ntfy_address_keeps_its_own_message():
    with pytest.raises(ValueError, match="Zugangs-Token"):
        validate({"notify.ntfy_url": "https://user:pass@ntfy.example/topic"})


@pytest.mark.parametrize(("key", "label"), [("evcc.password", "Admin-Passwort von evcc"),
                                            ("notify.ntfy_token", "ntfy-Zugangs-Token"),
                                            ("meter.password", "Passwort für das Kundenportal"),
                                            ("cloud.api_key", "API-Schlüssel der EKD-Cloud")])
def test_secret_that_looks_encrypted_is_refused(key, label):
    with pytest.raises(ValueError, match=f"^{label}: Der Wert beginnt mit „{PREFIX}“"):
        validate({key: f"{PREFIX}abcdef"})
    assert validate({key: f"x{PREFIX}abcdef"}) == {key: f"x{PREFIX}abcdef"}


def test_api_refuses_an_evcc_address_with_credentials_and_keeps_the_old_one(tmp_path):
    runtime = Runtime({}, Storage(tmp_path / "openampere.db"))
    try:
        client = login(TestClient(create_app(runtime)))
        assert client.put("/api/settings", json={"evcc.url": "http://192.0.2.10:7070"}).status_code == 200
        response = client.put("/api/settings", json={"evcc.url": "http://admin:geheim@192.0.2.10:7070"})
        assert response.status_code == 400 and "geheim" not in response.text
        response = client.put("/api/settings", json={"evcc.password": f"{PREFIX}abc"})
        assert response.status_code == 400
        assert runtime.config.evcc.url == "http://192.0.2.10:7070" and runtime.config.evcc.password == ""
    finally:
        runtime.storage.close()


# ---- diagnostics report ------------------------------------------------------------------------------------------

def test_checks_in_the_report_cannot_close_the_code_block():
    report = {"version": "1.0", "device": {}, "connection": {"mode": "persistent", "timeout_s": 3},
              "checks": [{"title": "Gerät", "status": "ok", "summary": "erkannt", "details": {"name": "x ```\n## y"}}]}
    text = report_markdown(report)
    assert text.count("```") == 2  # the opening and closing fence of the block, nothing in between
    assert "` ` `" in text.split("```json\n", 1)[1]
