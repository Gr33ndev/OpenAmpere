# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""The Android app on a tablet (#567): no helper containers, and a tablet that is not always running."""
import pytest

from openampere import android
from openampere.outages import Outages
from openampere.remote import Remote
from openampere.runtime import Runtime
from openampere.storage import Storage
from openampere.updates import Updates

from test_outages import reading


@pytest.fixture
def tablet(monkeypatch):
    monkeypatch.setenv(android.ENV, "1")


def test_only_the_app_sets_android_mode(monkeypatch):
    monkeypatch.delenv(android.ENV, raising=False)
    assert not android.active()
    monkeypatch.setenv(android.ENV, "0")
    assert not android.active()
    monkeypatch.setenv(android.ENV, "1")
    assert android.active()


def test_updates_come_as_a_new_version_of_the_app(tmp_path, tablet):
    updates = Updates(Runtime({}, Storage(tmp_path / "t.db")), "1.0.0")
    view = updates.view()
    assert view["android"] is True and "home_assistant" not in view
    with pytest.raises(RuntimeError, match="GitHub"):
        updates.request()
    assert not updates.request_file.exists()  # no update helper on the tablet would ever pick it up


def test_remote_access_through_the_tailscale_app(tmp_path, tablet):
    remote = Remote(Runtime({}, Storage(tmp_path / "t.db")))
    assert remote.view()["android"] is True
    with pytest.raises(RuntimeError, match="Tailscale für Android"):
        remote.request("login")


def test_docker_installations_are_unchanged(tmp_path, monkeypatch):
    monkeypatch.delenv(android.ENV, raising=False)
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    assert "android" not in Updates(runtime, "1.0.0").view()
    assert "android" not in Remote(runtime).view()
    with pytest.raises(RuntimeError, match="Install-Script"):
        Remote(runtime).request("login")


def test_a_gap_on_the_tablet_is_missing_data_not_an_empty_battery(tmp_path, tablet):
    """The tablet runs on its own battery and is switched off or taken away: that OpenAmpere did not run says
    nothing about the battery of the house."""
    outages = Outages(Storage(tmp_path / "t.db"))
    outages.observe(reading(0, True, 12))
    outages.observe(reading(10, True, 10))
    done = outages.observe(reading(4000, False, 11))  # the tablet was off for an hour
    assert done["dark_since"] == 10 and done["gap_reason"] == "no_data"


def test_a_gap_while_the_tablet_kept_asking_needs_a_charged_battery_for_the_inverter(tmp_path, tablet):
    """The tablet runs on while the router is dark as well: asking in vain does not prove that the house had power."""
    outages = Outages(Storage(tmp_path / "t.db"))
    outages.observe(reading(0, True, 12))
    outages.observe(reading(10, True, 10))
    outages.unreachable(600)
    assert outages.observe(reading(4000, False, 11))["gap_reason"] == "battery_empty"

    outages.observe(reading(5000, True, 80))
    outages.observe(reading(5010, True, 80))
    outages.unreachable(5600)
    assert outages.observe(reading(9000, False, 78))["gap_reason"] == "inverter_off"


def test_status_says_android(tmp_path, monkeypatch):
    from test_security import app_client
    monkeypatch.delenv(android.ENV, raising=False)
    _, client = app_client(tmp_path)
    assert client.get("/api/status").json()["android"] is False
    monkeypatch.setenv(android.ENV, "1")
    assert client.get("/api/status").json()["android"] is True
