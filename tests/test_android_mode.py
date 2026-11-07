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
    with pytest.raises(RuntimeError, match="GitHub"):  # an app without its updater: download it by hand
        updates.request()
    assert not updates.request_file.exists()  # nobody would ever pick it up


def test_the_app_has_no_updater(tmp_path, tablet):
    """Play Protect blocks apps that may install packages (#610): a sign of life an older app's updater left behind
    must not bring back the update button or the updates at night."""
    import asyncio
    import time

    runtime = Runtime({"storage": {"path": str(tmp_path / "t.db")}, "updates": {"auto": True}}, Storage(tmp_path / "t.db"))
    updates = Updates(runtime, "1.0.0")
    updates.latest = {"version": "1.1.0"}
    updates.checked = time.time()  # no lookup on GitHub
    updates.folder.mkdir(parents=True)
    (updates.folder / "updater-alive").write_text(str(time.time()))
    assert updates.view()["updater"] is False
    with pytest.raises(RuntimeError, match="GitHub"):
        updates.request()
    night = time.mktime((2026, 11, 8, 3, 0, 0, 0, 0, -1))
    updates.checked = night
    asyncio.run(updates.tick(night))
    assert not updates.request_file.exists()


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


@pytest.mark.parametrize("on_tablet", [False, True])
def test_a_restart_runs_the_command_again_except_in_the_app(tmp_path, monkeypatch, on_tablet):
    """After a restored backup the server starts itself again. In the app, os.execv would replace the app's process:
    there main() returns and the app starts the server again in a new process."""
    import openampere.__main__ as entry
    from openampere import api, runtime as runtime_module

    monkeypatch.setenv("OPENAMPERE_STORAGE_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("OPENAMPERE_CONFIG", str(tmp_path / "config.yaml"))
    if on_tablet:
        monkeypatch.setenv(android.ENV, "1")
    else:
        monkeypatch.delenv(android.ENV, raising=False)
    monkeypatch.setattr("sys.argv", ["openampere"])
    monkeypatch.setattr(entry.logs, "setup", lambda: None)
    monkeypatch.setattr(api, "create_app", lambda runtime: None)

    def serve(coroutine):
        coroutine.close()
        started.restart_requested = True

    started = runtime_module.Runtime.from_files(None)
    monkeypatch.setattr(runtime_module.Runtime, "from_files", classmethod(lambda cls, path: started))
    monkeypatch.setattr(entry.asyncio, "run", serve)
    calls = []
    monkeypatch.setattr(entry.os, "execv", lambda *args: calls.append(args))
    monkeypatch.setattr(entry.os, "umask", lambda mask: 0)
    entry.main()
    assert bool(calls) is not on_tablet  # the same command again only outside the app


def test_another_app_on_the_port_is_said_before_the_server_starts():
    import socket

    port = _taken_port()
    with pytest.raises(SystemExit, match=f"Port {port} wird schon von einer anderen App"):
        android.check_port(port)
    with socket.socket() as free:
        free.bind(("0.0.0.0", 0))
        port = free.getsockname()[1]
    android.check_port(port)  # free again: no error


def test_why_the_server_stopped_for_the_owner():
    assert android.stop_reason(SystemExit()) is None  # as asked, e.g. a restart after a restored backup
    assert android.stop_reason(SystemExit(0)) is None
    assert android.stop_reason(SystemExit("Kein Schreibzugriff auf …")) == "Kein Schreibzugriff auf …"
    assert "Fehlercode 1" in android.stop_reason(SystemExit(1))  # uvicorn could not open its port


def test_the_reason_follows_the_language_of_the_tablet():
    assert android.stop_reason(SystemExit(1), "en") == ("OpenAmpere stopped with error code 1. Please send a "
                                                         "diagnostics report.")
    with pytest.raises(SystemExit) as stop:
        android.check_port(_taken_port())
    assert android.stop_reason(stop.value, "en").startswith("Port ")
    assert "is already used by another app" in android.stop_reason(stop.value, "en")
    assert "wird schon von einer anderen App" in android.stop_reason(stop.value, "de")


_held = []


def _taken_port() -> int:
    import socket

    other = socket.socket()
    other.bind(("0.0.0.0", 0))  # as another app on the tablet: on every interface, like the server
    other.listen()
    _held.append(other)  # kept open until the end of the test run
    return other.getsockname()[1]
