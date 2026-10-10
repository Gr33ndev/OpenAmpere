# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from openampere import updates as updates_module
from openampere.api import create_app
from openampere.runtime import Runtime
from openampere.storage import Storage
from openampere.updates import Updates, parse_version

from conftest import login

RELEASE = {"version": "0.3.0", "url": "https://github.com/Gr33ndev/OpenAmpere/releases/tag/v0.3.0", "notes": "### Neu",
           "published": "2026-10-04T08:00:00Z"}


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    rt = Runtime({}, Storage(tmp_path / "data" / "t.db"))
    monkeypatch.setattr(rt.config.storage, "path", str(tmp_path / "data" / "t.db"))
    monkeypatch.setattr(updates_module, "fetch_latest", lambda: RELEASE)
    return rt


def alive(upd: Updates, ts: float | None = None) -> None:
    upd.folder.mkdir(parents=True, exist_ok=True)
    (upd.folder / "updater-alive").write_text(str(int(ts if ts is not None else time.time())))


def test_versions():
    assert parse_version("v0.10.2") == (0, 10, 2) > parse_version("0.9.9")
    assert parse_version("dev") is None and parse_version(None) is None


async def test_finds_and_requests_an_update(runtime):
    upd = Updates(runtime, "0.2.0")
    await upd.check()
    assert upd.available and upd.latest["version"] == "0.3.0"
    assert not upd.view()["updater"]
    with pytest.raises(RuntimeError, match="Install-Script"):
        upd.request()  # no updater container: the app explains what to do
    alive(upd)
    upd.request()
    request = json.loads((upd.folder / "request").read_text())
    assert (request["from"], request["to"]) == ("0.2.0", "0.3.0")
    # objects, not plain strings: the app listed a string character by character (#153)
    assert runtime.storage.control_log()[0]["details"] == {"from": {"version": "0.2.0"}, "to": {"version": "0.3.0"},
                                                           "by": "manual"}
    (upd.folder / "status.json").write_text('{"ts": 1, "state": "pulling", "message": "lädt"}')
    view = upd.view()
    assert view["requested"] and view["updater"] and view["status"]["state"] == "pulling"
    assert not Updates(runtime, "0.3.0").available  # already up to date
    assert not Updates(runtime, "dev").available  # running from source


async def test_check_can_be_switched_off_and_is_throttled(runtime, monkeypatch):
    calls = []
    monkeypatch.setattr(updates_module, "fetch_latest", lambda: calls.append(1) or RELEASE)
    upd = Updates(runtime, "0.2.0")
    await upd.check(now=1000)
    await upd.check(now=2000)
    assert len(calls) == 1
    await upd.check(force=True, now=2100)
    assert len(calls) == 2
    await runtime.update_settings({"updates.check": False})
    await upd.check(force=True, now=99_999)
    assert len(calls) == 2


async def test_automatic_update_at_night_once_per_version(runtime):
    await runtime.update_settings({"updates.auto": True})
    upd = Updates(runtime, "0.2.0")
    tz = ZoneInfo("Europe/Berlin")
    day = datetime(2026, 10, 4, 14, tzinfo=tz).timestamp()
    night = datetime(2026, 10, 5, 3, tzinfo=tz).timestamp()
    alive(upd, day)
    await upd.tick(now=day)
    assert not (upd.folder / "request").exists()  # not during the day
    alive(upd, night)
    await upd.tick(now=night)
    assert (upd.folder / "request").exists()
    assert runtime.storage.control_log()[0]["details"]["by"] == "auto"
    (upd.folder / "request").unlink()
    await upd.tick(now=night + 600)
    assert not (upd.folder / "request").exists()  # not again for the same version, even if it failed


def test_endpoint(runtime):
    client = TestClient(create_app(runtime))
    assert client.get("/api/update").json()["current"]
    assert client.post("/api/update", json={"action": "check"}).status_code == 403  # only for the app, with login
    login(client)
    assert client.post("/api/update", json={"action": "nonsense"}).status_code == 400
    assert client.post("/api/update", json={"action": "check"}).json()["latest"]["version"] == "0.3.0"
    assert client.post("/api/update", json={"action": "install"}).status_code == 409  # no updater container
