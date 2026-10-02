import pytest

from openampere import consumers as consumers_module
from openampere.consumers import SurplusControl, switch_url, validate
from openampere.drivers.base import Snapshot
from openampere.runtime import Runtime
from openampere.storage import Storage

ROD = "rod.local"
PUMP = "pump.local"


def runtime_with(tmp_path, monkeypatch, calls):
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    runtime.config.control.enabled = True
    runtime.config.control.dry_run = False
    monkeypatch.setattr(consumers_module, "_call", calls.append)
    monkeypatch.setattr(type(runtime.collector), "stale", property(lambda self: False))
    return runtime


def reading(runtime, grid, battery=0.0, soc=90.0):
    runtime.collector.latest = Snapshot(timestamp=0, grid_power=grid, battery_power=battery, battery_soc=soc)


async def test_surplus_switches_by_priority_with_hysteresis(tmp_path, monkeypatch):
    calls = []
    runtime = runtime_with(tmp_path, monkeypatch, calls)
    control = SurplusControl(runtime)
    control.save([{"name": "Heizstab", "kind": "shelly2", "host": ROD, "power_w": 2000, "min_on_min": 10},
                  {"name": "Wärmepumpe", "kind": "shelly1", "host": PUMP, "power_w": 1000, "min_on_min": 0}])

    reading(runtime, grid=-2500)  # 2.5 kW export
    await control.tick(now=1000)
    assert calls == []  # needs two checks in a row (clouds)
    await control.tick(now=1030)
    assert calls == [f"http://{ROD}/rpc/Switch.Set?id=0&on=true"]

    reading(runtime, grid=-1300)  # heating rod runs, still 1.3 kW export -> heat pump too
    await control.tick(now=1060)
    await control.tick(now=1090)
    assert calls[-1] == f"http://{PUMP}/relay/0?turn=on"

    reading(runtime, grid=800)  # clouds: import
    await control.tick(now=1120)
    await control.tick(now=1150)
    assert calls[-1] == f"http://{PUMP}/relay/0?turn=off"  # lowest priority first
    await control.tick(now=1180)
    await control.tick(now=1210)
    assert len(calls) == 3  # heating rod keeps its minimum on time of 10 minutes
    await control.tick(now=1000 + 30 + 600)
    await control.tick(now=1000 + 60 + 600)
    assert calls[-1].endswith("on=false")


async def test_battery_first_and_test_mode(tmp_path, monkeypatch):
    calls = []
    runtime = runtime_with(tmp_path, monkeypatch, calls)
    control = SurplusControl(runtime)
    control.save([{"name": "Heizstab", "kind": "http", "url_on": f"http://{ROD}/on", "url_off": f"http://{ROD}/off",
                   "power_w": 2000, "battery_min_soc": 80}])
    reading(runtime, grid=0, battery=-3000, soc=50)  # battery takes everything, below 80 %
    await control.tick(now=0)
    await control.tick(now=30)
    assert calls == []
    reading(runtime, grid=0, battery=-3000, soc=85)  # battery full enough: its charging power may be used
    runtime.config.control.dry_run = True
    await control.tick(now=60)
    await control.tick(now=90)
    assert calls == [] and runtime.storage.control_log()[0]["dry_run"]


def test_validation():
    with pytest.raises(ValueError):
        validate([{"name": "x", "kind": "http", "url_on": "file:///etc/passwd", "url_off": "http://a/"}])
    with pytest.raises(ValueError):
        validate([{"name": "", "host": PUMP}])
    c = validate([{"name": "Pumpe", "kind": "shelly1", "host": PUMP, "channel": 1}])[0]
    assert switch_url(c, False) == f"http://{PUMP}/relay/1?turn=off"
