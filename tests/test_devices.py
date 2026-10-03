from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from openampere.api import create_app
from openampere.consumers import State, SurplusControl
from openampere.devices import Devices
from openampere.drivers.base import Snapshot
from openampere.runtime import Runtime
from openampere.storage import Storage

from test_heating_rod import rod, setup  # noqa: F401  (fixture and helper)

TZ = ZoneInfo("Europe/Berlin")


class FakeEvcc:
    configured, error = True, None

    def __init__(self, power):
        self.power = power

    def fresh(self):
        return {"loadpoints": [{"id": 1, "title": "Carport", "heating": False, "charging": self.power > 0,
                                "power_w": self.power, "soc": 50, "limit_soc": 80}]}


def test_device_energy_and_power_history(tmp_path):
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    surplus = SurplusControl(runtime)
    surplus.save([{"name": "Heizstab", "kind": "shelly2", "host": "rod.local", "power_w": 2000}])
    car = FakeEvcc(7400)
    devices = Devices(runtime, surplus, car)
    rod_key = f"c:{surplus.consumers[0].id}"
    surplus.states[surplus.consumers[0].id] = State(on=True)

    base = datetime(2026, 6, 1, 12, 0, tzinfo=TZ).timestamp()
    for i in range(0, 1800 + 1, 60):  # 30 minutes, one reading per minute
        devices.record(base + i)
    energy = devices.energy(base, base + 3600, "60m")
    assert [d["name"] for d in energy["devices"]] == ["Carport", "Heizstab"]  # wallbox first
    car_wh, rod_wh = energy["totals_wh"]
    assert round(car_wh) == 3700 and round(rod_wh) == 1000  # 7.4 kW and 2 kW for half an hour
    power = devices.power(base, base + 3600, 300)
    assert power["entries"][0]["values"] == [7400, 2000]

    # the name stays in the history after the device was renamed
    surplus.save([{"name": "Boiler", "kind": "shelly2", "host": "rod.local", "power_w": 2000, "id": rod_key[2:]}])
    devices.record(base + 1900)
    assert devices.energy(base, base + 3600, "60m")["devices"][1]["name"] == "Boiler"


async def test_manual_boost_and_off(tmp_path, monkeypatch, rod):  # noqa: F811
    sim, port = rod
    runtime, control = setup(tmp_path, monkeypatch, port)
    try:
        cid = control.consumers[0].id
        runtime.collector.latest = Snapshot(timestamp=0, grid_power=1500, battery_power=0, battery_soc=50)  # no sun
        control.set_override(cid, "boost", hours=1, now=0)
        await control.tick(now=10)
        assert sim.setpoint == 3000  # hot water now, whatever the sun does
        control.set_override(cid, "off", now=20)
        runtime.collector.latest = Snapshot(timestamp=0, grid_power=-3000, battery_power=0, battery_soc=90)
        await control.tick(now=30)
        await control.tick(now=40)
        assert sim.setpoint == 0  # stays off despite surplus
        control.set_override(cid, "boost", hours=1, now=50)
        assert control.override(cid, now=50 + 3700) is None  # expires
        control.set_override(cid, "auto", now=60)
        assert control.override(cid, now=60) is None
        modes = [e["details"]["to"]["mode"] for e in runtime.storage.control_log() if e["action"] == "consumer_mode"]
        assert modes == ["auto", "boost", "off", "boost"]
    finally:
        await control.stop()


def test_devices_api(tmp_path, authed):
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    client = authed(TestClient(create_app(runtime)))
    client.put("/api/consumers", json={"consumers": [{"name": "Heizstab", "kind": "mypv", "host": "rod.local",
                                                      "power_w": 3000, "min_power_w": 500}]})
    devices = client.get("/api/devices").json()["devices"]
    assert devices[0]["kind"] == "heating_rod" and devices[0]["name"] == "Heizstab"
    cid = devices[0]["id"]
    assert client.post(f"/api/consumers/{cid}/mode", json={"mode": "boost", "hours": 2}).status_code == 200
    assert client.get("/api/devices").json()["devices"][0]["override"]["mode"] == "boost"
    assert client.post(f"/api/consumers/{cid}/mode", json={"mode": "turbo"}).status_code == 400
    energy = client.get("/api/devices/energy", params={"period": "day", "date": "2026-06-01"}).json()
    assert energy["devices"] == [] and energy["entries"] == []
