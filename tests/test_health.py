import time

from fastapi.testclient import TestClient

from openampere import health
from openampere.api import create_app
from openampere.drivers.base import EnergyCounters, Snapshot
from openampere.notify import battery_problem
from openampere.runtime import Runtime
from openampere.storage import Storage


def snap(ts, cell_max=24.0, cell_min=22.0, inverter=40.0):
    return Snapshot(timestamp=ts, pv_power=0, house_power=500, grid_power=500, battery_power=0, battery_soc=50,
                    battery_soh=97, temperatures={"battery_cell_max": cell_max, "battery_cell_min": cell_min,
                                                  "inverter": inverter, "battery": 23.0},
                    totals=EnergyCounters(battery_charge=2_000_000, battery_discharge=1_840_000))


def test_firmware_changes_are_remembered(tmp_path):
    storage = Storage(tmp_path / "t.db")
    assert storage.note_firmware("SN1", "1.50 / 1.20", now=100) is None  # first time: nothing to compare
    assert storage.note_firmware("SN1", "1.50 / 1.20", now=200) is None
    change = storage.note_firmware("SN1", "1.62 / 1.20", now=300)
    assert change == {"ts": 300, "old": "1.50 / 1.20", "new": "1.62 / 1.20"}
    assert storage.note_firmware("SN2", "2.00", now=400) is None  # another device is no update
    assert storage.get_meta("firmware_history") == [change]
    assert storage.note_firmware("SN2", None) is None


def test_battery_health(tmp_path):
    storage = Storage(tmp_path / "t.db")
    now = time.time()
    storage.add_snapshot(snap(now - 7200, cell_max=31.0, cell_min=27.5, inverter=58.0))
    storage.add_snapshot(snap(now - 3600))
    result = health.battery(storage, snap(now), 10.0, now)
    assert result["cycles"] == 184  # 1840 kWh discharged / 10 kWh
    assert round(result["efficiency_pct"]) == 92
    assert result["spread_now_c"] == 2.0 and result["soh_pct"] == 97
    extremes = result["extremes"]
    assert extremes["cell_max"]["value"] == 31.0 and extremes["spread"]["value"] == 3.5
    assert extremes["inverter"]["value"] == 58.0 and extremes["cell_min"]["value"] == 22.0
    assert result["warning"] is None
    assert health.battery(storage, None, 0)["cycles"] is None


def test_battery_warnings():
    assert battery_problem({"battery_cell_max": 47.0, "battery_cell_min": 45.0}).startswith("Die wärmste")
    assert "5.5 °C" in battery_problem({"battery_cell_max": 30.5, "battery_cell_min": 25.0})
    assert battery_problem({"battery_cell_max": 30.0, "battery_cell_min": 27.0}) is None
    assert battery_problem({}) is None


def test_endpoints(tmp_path):
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    client = TestClient(create_app(runtime))
    assert client.get("/api/battery/health").json()["cycles"] is None
    body = client.get("/api/billing").json()
    assert body["status"] == {"import": None, "export": None}
    assert body["settings"]["export"] == {"start_month": 1, "payments": []}
    assert client.get("/api/status").json()["firmware"] == {"history": []}
