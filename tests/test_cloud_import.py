# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
import asyncio
import io
import json
import threading
import zipfile
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from openampere import cloud_import
from openampere.api import create_app
from openampere.runtime import Runtime
from openampere.storage import Storage

KEY = "test-key-123456"
UUID = "11111111-2222-3333-4444-555555555555"
FIRST_DAY = date.today() - timedelta(days=3)


def day_slots(day: date):
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    for i in range(96):
        yield (start + timedelta(minutes=15 * i)).isoformat().replace("+00:00", "Z"), \
              (start + timedelta(minutes=15 * (i + 1))).isoformat().replace("+00:00", "Z")


def work_body(day: date) -> dict:
    timeline = lambda v: {"total": v * 96, "timeline": [  # noqa: E731
        {"value": v, "fromTimestamp": a, "toTimestamp": b} for a, b in day_slots(day)]}
    return {"generation": timeline(100), "consumption": timeline(60), "gridDraw": timeline(5),
            "gridFeed": timeline(30), "batteryFeed": timeline(20), "batteryDraw": timeline(5)}


class FakeCloud(BaseHTTPRequestHandler):
    requests: list[str] = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        url = urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        FakeCloud.requests.append(url.path)
        if self.headers.get("x-client-api-key") != KEY:
            return self._send(401, {"error": "unauthorized"})
        if url.path == "/api/v1/customer/installation":
            return self._send(200, [{"uuid": UUID}])
        if url.path.endswith("/total/common/work"):
            d = date.fromisoformat(query["date"])
            has = (d.year == FIRST_DAY.year) if query["period"] == "year" else (d.year, d.month) >= (FIRST_DAY.year, FIRST_DAY.month)
            return self._send(200, {"generation": 5.0 if has else 0, "consumption": 1.0 if has else 0})
        day = date.fromisoformat(query["date"])
        if day < FIRST_DAY:
            return self._send(200, {k: {"total": 0, "timeline": []} for k in cloud_import.WORK_FIELDS})
        if url.path.endswith("/history/common/work"):
            return self._send(200, work_body(day))
        if url.path.endswith("/history/stateOfCharge"):
            return self._send(200, {"timeline": [{"value": 55, "fromTimestamp": a, "toTimestamp": b}
                                                 for a, b in day_slots(day)]})
        return self._send(404, {})

    def _send(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def fake_cloud(monkeypatch):
    monkeypatch.setattr(cloud_import, "MIN_INTERVAL_S", 0)
    monkeypatch.setattr(cloud_import, "BACKOFF_S", 0)
    FakeCloud.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeCloud)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


async def wait_for(job, *statuses, seconds=20):
    for _ in range(seconds * 20):
        if job.state.get("status") in statuses:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(job.state)


async def test_full_import(tmp_path, fake_cloud):
    storage = Storage(tmp_path / "t.db")
    runtime = Runtime({"cloud": {"base_url": fake_cloud}}, storage)
    # a quarter hour measured locally must survive the import
    local_ts = int(datetime(FIRST_DAY.year, FIRST_DAY.month, FIRST_DAY.day, tzinfo=timezone.utc).timestamp())
    storage.import_energy([{"ts": local_ts, "pv": 1.0}], "local")

    with pytest.raises(ValueError):
        runtime.cloud_import.start()  # no key yet
    await runtime.update_settings({"cloud.api_key": KEY})
    runtime.cloud_import.start()
    await wait_for(runtime.cloud_import, "done", "error")

    state = runtime.cloud_import.view()
    assert state["status"] == "done", state
    days = (date.today() - timedelta(days=1) - FIRST_DAY.replace(day=1)).days + 1  # start is found per month
    assert state["work_done"] == days and state["soc_done"] >= days
    assert "installation" not in state
    rows = storage.energy(local_ts, local_ts + 86400)
    assert len(rows) == 96
    assert rows[0]["pv"] == 1.0 and rows[0]["source"] == "local"
    assert rows[1] == {**rows[1], "pv": 100, "load": 60, "grid_import": 5, "grid_export": 30,
                       "battery_charge": 20, "battery_discharge": 5, "soc": 55, "source": "cloud"}


async def test_bad_key_and_resume(tmp_path, fake_cloud):
    storage = Storage(tmp_path / "t.db")
    runtime = Runtime({"cloud": {"base_url": fake_cloud}}, storage)
    await runtime.update_settings({"cloud.api_key": "wrong-key-0000"})
    runtime.cloud_import.start()
    await wait_for(runtime.cloud_import, "error")
    assert "abgelehnt" in runtime.cloud_import.state["error"]

    await runtime.update_settings({"cloud.api_key": KEY})  # new key resets progress
    assert runtime.cloud_import.state["status"] == "idle"
    runtime.cloud_import.start()
    for _ in range(400):
        if runtime.cloud_import.state.get("work_done", 0) >= 1:
            break
        await asyncio.sleep(0.01)
    await runtime.cloud_import.stop()
    paused = dict(runtime.cloud_import.state)
    assert paused["status"] == "paused"

    # a new process continues where the old one stopped
    runtime2 = Runtime({"cloud": {"base_url": fake_cloud}, "storage": {"path": "unused"}}, storage)
    runtime2.config.cloud.api_key = KEY
    assert runtime2.cloud_import.state["work_cursor"] == paused["work_cursor"]
    runtime2.cloud_import.start()
    await wait_for(runtime2.cloud_import, "done")


def test_zip_import_and_secret_handling(tmp_path, authed):
    storage = Storage(tmp_path / "t.db")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(f"cloud-export/{UUID}/work/{FIRST_DAY}.json", json.dumps({"status": 200, "body": work_body(FIRST_DAY)}))
        archive.writestr(f"cloud-export/{UUID}/stateOfCharge/{FIRST_DAY}.json", json.dumps({"status": 200, "body": {
            "timeline": [{"value": 70, "fromTimestamp": a, "toTimestamp": b} for a, b in day_slots(FIRST_DAY)]}}))
        archive.writestr(f"cloud-export/{UUID}/installations.json", "[]")
    runtime = Runtime({}, storage)
    client = authed(TestClient(create_app(runtime)))

    result = client.post("/api/import/cloud/file", content=buffer.getvalue()).json()
    assert result == {"days": 1, "quarters": 96, "inserted": 96}
    assert storage.energy_sum(0, 2**40)["pv"] == 9600
    assert client.post("/api/import/cloud/file", content=b"not a zip").status_code == 400

    client.put("/api/settings", json={"cloud.api_key": KEY})
    view = client.get("/api/settings").json()
    assert KEY not in json.dumps(view)
    assert view["secrets"]["cloud.api_key"] == {"set": True, "hint": "…3456"}
    assert client.get("/api/import/cloud").json()["key_set"] is True


def test_start_and_stop_through_api(tmp_path, fake_cloud, monkeypatch, authed):
    monkeypatch.setattr(cloud_import, "MIN_INTERVAL_S", 0.2)  # slow enough to observe "running"
    runtime = Runtime({"cloud": {"base_url": fake_cloud}}, Storage(tmp_path / "t.db"))
    with TestClient(create_app(runtime)) as client:  # runs the app's event loop like uvicorn does
        authed(client)
        assert client.post("/api/import/cloud/start").status_code == 400  # no key yet
        client.put("/api/settings", json={"cloud.api_key": KEY})
        started = client.post("/api/import/cloud/start").json()
        assert started["status"] == "running"
        for _ in range(100):
            if client.get("/api/import/cloud").json().get("phase") in ("work", "soc"):
                break
            import time
            time.sleep(0.05)
        assert client.get("/api/import/cloud").json()["phase"] in ("work", "soc")
        assert client.post("/api/import/cloud/stop").json()["status"] == "paused"
