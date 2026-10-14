# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""The app explains a setting another device (e.g. the old smartbox) wrote back after a change by OpenAmpere."""

import time

from fastapi.testclient import TestClient

from openampere.api import create_app
from openampere.runtime import Runtime
from openampere.storage import Storage

from conftest import login

OVERWRITTEN = {"from": {"min_soc_on_grid": 40, "work_mode": "self_use"},
               "to": {"min_soc_on_grid": 15, "work_mode": "feed_in"}}


def test_last_overwrite_names_the_settings(tmp_path):
    storage = Storage(tmp_path / "t.db")
    assert storage.last_overwrite(0) is None
    storage.log_control("battery_settings", {"min_soc_on_grid": 40}, False, "ok")
    storage.log_control("battery_settings_check", OVERWRITTEN, False, "von einem anderen Gerät überschrieben")
    found = storage.last_overwrite(0)
    assert found["settings"] == ["min_soc_on_grid", "work_mode"]
    assert found["ts"] <= time.time()
    assert storage.last_overwrite(time.time() + 1) is None  # older than the time asked for


def test_a_new_write_of_the_setting_ends_the_overwrite(tmp_path):
    storage = Storage(tmp_path / "t.db")
    storage.log_control("export_limit_check", {"from": {"export_limit_w": 4000}, "to": {"export_limit_w": 0}},
                        False, "von einem anderen Gerät überschrieben")
    time.sleep(0.01)
    storage.log_control("export_limit", {"limit_w": 4000}, True, "nicht ausgeführt (Testmodus)")
    storage.log_control("battery_settings", {"min_soc": 10}, False, "ok")
    assert storage.last_overwrite(0)["settings"] == ["export_limit_w"]  # test mode and other settings do not count
    time.sleep(0.01)
    storage.log_control("export_limit", {"limit_w": 4000}, False, "ok")
    assert storage.last_overwrite(0) is None


def test_status_says_which_settings_without_their_values(tmp_path):
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    anonymous = TestClient(create_app(runtime))
    login(TestClient(create_app(runtime)))  # a password is set: the anonymous client is not logged in
    assert anonymous.get("/api/status").json()["overwritten"] is None
    runtime.storage.log_control("battery_settings_check", OVERWRITTEN, False, "von einem anderen Gerät überschrieben")
    overwritten = anonymous.get("/api/status").json()["overwritten"]
    assert overwritten["settings"] == ["min_soc_on_grid", "work_mode"]
    assert "15" not in str(overwritten) and "feed_in" not in str(overwritten)
