# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from openampere.api import create_app
from openampere.drivers.base import EnergyCounters, Snapshot
from openampere.evcc import Evcc, normalize_mode, wants_surplus
from openampere.runtime import Runtime
from openampere.storage import Storage

STATE = {
    "version": "0.316.1",
    "vehicles": {"ev1": {"title": "e-Golf", "capacity": 35.8, "minSoc": 20, "limitSoc": 80}},
    "loadpoints": [
        {"title": "Carport", "mode": "smart", "alwaysCharge": "off", "connected": True, "charging": True,
         "enabled": True, "chargePower": 7400, "sessionEnergy": 5200, "vehicleName": "ev1", "vehicleTitle": "e-Golf",
         "vehicleSoc": 61, "effectiveLimitSoc": 80, "phasesActive": 3, "effectiveMinCurrent": 6},
        {"title": "Wärmepumpe", "mode": "pv", "chargerFeatureHeating": True, "connected": True, "charging": False,
         "vehicleSoc": 48, "limitSoc": 55},
    ],
}


class FakeEvcc(BaseHTTPRequestHandler):
    calls: list = []
    password: str | None = None

    def log_message(self, *args):
        pass

    def _authorized(self) -> bool:
        return self.password is None or self.headers.get("Cookie") == "auth=ok"

    def _send(self, code: int, body=None, cookie: str | None = None):
        data = json.dumps(body).encode() if body is not None else b""
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        if cookie:
            self.send_header("Set-Cookie", f"{cookie}; Path=/; HttpOnly")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/api/state":
            self._send(200, STATE)
        elif self.path == "/api/sessions":
            self._send(200, [{"created": "2026-10-01T18:00:00+02:00", "finished": "2026-10-01T21:00:00+02:00",
                              "loadpoint": "Carport", "vehicle": "e-Golf", "chargedEnergy": 21.5,
                              "chargeDuration": 10_800_000_000_000, "solarPercentage": 40.0, "price": 4.1,
                              "odometer": 23456, "socStart": 22, "socEnd": 80, "addedRange": 140}])
        else:
            self._send(404)

    def do_POST(self):
        if self.path == "/api/auth/login":
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            ok = body.get("password") == self.password
            return self._send(200 if ok else 401, None, "auth=ok" if ok else None)
        if not self._authorized():
            return self._send(401)
        FakeEvcc.calls.append(("POST", self.path))
        self._send(200, "ok")

    def do_DELETE(self):
        FakeEvcc.calls.append(("DELETE", self.path))
        self._send(200, None)


@pytest.fixture
def evcc_server():
    FakeEvcc.calls, FakeEvcc.password = [], None
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeEvcc)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_mode_names_of_old_and_new_evcc():
    assert normalize_mode("smart", "off") == "pv"
    assert normalize_mode("smart", "once") == "minpv"
    assert normalize_mode("minpv", None) == "minpv"
    assert normalize_mode("something", None) is None


async def test_reads_state_and_sends_commands(tmp_path, evcc_server):
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    await runtime.update_settings({"evcc.url": evcc_server + "/"})
    evcc = Evcc(runtime)
    state = await evcc.refresh()
    from openampere.evcc import EvccError
    with pytest.raises(EvccError, match="ausgeschaltet"):
        await evcc.command(1, "mode", "now")  # "Nur ansehen"
    await runtime.update_settings({"control.enabled": True})
    await evcc.command(1, "mode", "now")  # test mode: only logged
    assert FakeEvcc.calls == [] and runtime.storage.control_log()[0]["dry_run"]
    await runtime.update_settings({"control.dry_run": False})
    car, heat_pump = state["loadpoints"]
    assert (car["mode"], car["power_w"], car["soc"], car["limit_soc"], car["phases"]) == ("pv", 7400, 61, 80, 3)
    assert (car["min_soc"], car["vehicle_capacity_kwh"]) == (20, 35.8)
    assert heat_pump["min_soc"] == 0 and heat_pump["vehicle_capacity_kwh"] is None
    assert heat_pump["heating"] and not wants_surplus(heat_pump)
    assert wants_surplus(car)

    await evcc.command(1, "mode", "now")
    await evcc.command(1, "limit_soc", 90)
    await evcc.command(1, "plan", {"soc": 80, "time": 4_000_000_000})
    await evcc.command(1, "plan_delete")
    await evcc.command(1, "min_soc", 30)
    assert FakeEvcc.calls == [("POST", "/api/loadpoints/1/mode/now"), ("POST", "/api/loadpoints/1/limitsoc/90"),
                              ("POST", "/api/vehicles/ev1/plan/soc/80/2096-10-02T07:06:40Z"),
                              ("DELETE", "/api/vehicles/ev1/plan/soc"), ("POST", "/api/vehicles/ev1/minsoc/30")]
    with pytest.raises(ValueError):
        await evcc.command(1, "mode", "turbo")
    with pytest.raises(ValueError):
        await evcc.command(1, "min_soc", 95)
    with pytest.raises(EvccError, match="Fahrzeug"):
        await evcc.command(2, "min_soc", 20)  # the heat pump has no vehicle
    assert runtime.storage.control_log()[0]["action"] == "evcc"

    sessions = await evcc.sessions()
    session = sessions[0]
    assert session["energy_kwh"] == 21.5 and session["duration_s"] == 10_800
    assert (session["odometer_km"], session["soc_start"], session["soc_end"], session["added_range_km"]) == (23456, 22, 80, 140)
    # default tariff 35 ct from the grid, 8 ct feed-in: 40 % solar at 8 ct, 60 % grid at 35 ct
    assert session["cost_eur"] == round(21.5 * (0.4 * 8 + 0.6 * 35) / 100, 2)
    assert session["grid_cost_eur"] == round(21.5 * 35 / 100, 2)


async def test_login_when_evcc_asks_for_it(tmp_path, evcc_server):
    FakeEvcc.password = "geheim"
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    await runtime.update_settings({"evcc.url": evcc_server, "control.enabled": True, "control.dry_run": False})
    evcc = Evcc(runtime)
    await evcc.refresh()
    from openampere.evcc import EvccError
    with pytest.raises(EvccError, match="Admin-Passwort"):
        await evcc.command(1, "mode", "off")
    await runtime.update_settings({"evcc.password": "geheim"})
    await evcc.command(1, "mode", "off")
    assert FakeEvcc.calls[-1] == ("POST", "/api/loadpoints/1/mode/off")


async def test_unreachable_evcc_is_reported(tmp_path):
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    await runtime.update_settings({"evcc.url": "http://127.0.0.1:9"})
    evcc = Evcc(runtime)
    await evcc.refresh()
    assert "nicht erreichbar" in evcc.view()["error"]


def test_site_meters_for_evcc(tmp_path, monkeypatch):
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    client = TestClient(create_app(runtime))
    assert client.get("/api/evcc/site").status_code == 503  # no readings yet: evcc must not use old values
    runtime.collector.latest = Snapshot(timestamp=1, grid_power=-1200, pv_power=5000, battery_power=-2000,
                                        battery_soc=55, totals=EnergyCounters(grid_import=1_000_000, pv=9_000_000))
    monkeypatch.setattr(type(runtime.collector), "stale", property(lambda self: False))
    site = client.get("/api/evcc/site", headers={"host": "openampere:8080"}).json()  # docker service name
    assert (site["grid_power"], site["pv_power"], site["battery_power"], site["battery_soc"]) == (-1200, 5000, -2000, 55)
    assert site["grid_import_kwh"] == 1000 and site["pv_kwh"] == 9000


async def test_battery_priority_respects_the_control_switch_and_test_mode(tmp_path, evcc_server):
    """#219: the battery priority is a write to evcc like the other commands."""
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    await runtime.update_settings({"evcc.url": evcc_server + "/"})
    evcc = Evcc(runtime)
    assert await evcc.set_priority_soc(60) is False  # "Nur ansehen": nothing sent, nothing logged
    assert FakeEvcc.calls == [] and runtime.storage.control_log() == []
    await runtime.update_settings({"control.enabled": True})
    assert await evcc.set_priority_soc(60) is False  # test mode: only logged
    assert FakeEvcc.calls == [] and runtime.storage.control_log()[0]["dry_run"]
    await runtime.update_settings({"control.dry_run": False})
    assert await evcc.set_priority_soc(60) is True
    assert FakeEvcc.calls == [("POST", "/api/prioritysoc/60")]
    entry = runtime.storage.control_log()[0]
    assert entry["action"] == "evcc" and entry["result"] == "ok" and not entry["dry_run"]
