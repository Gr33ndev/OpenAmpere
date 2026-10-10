# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
import asyncio
import sqlite3

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


# #217: a relay that OpenAmpere switched on must not stay on when it should be off
PUMP_ON, PUMP_OFF = f"http://{PUMP}/relay/0?turn=on", f"http://{PUMP}/relay/0?turn=off"
PUMP_CONFIG = {"name": "Pumpe", "kind": "shelly1", "host": PUMP, "power_w": 1000, "min_on_min": 0, "min_off_min": 0}


async def pump_on(control, runtime, calls):
    reading(runtime, grid=-2000)
    await control.tick(now=1000)
    await control.tick(now=1030)
    assert calls == [PUMP_ON]


@pytest.mark.parametrize("change", ["disabled", "removed"])
async def test_a_device_switched_on_is_switched_off_when_disabled_or_removed(tmp_path, monkeypatch, change):
    calls = []
    runtime = runtime_with(tmp_path, monkeypatch, calls)
    control = SurplusControl(runtime)
    control.save([PUMP_CONFIG])
    await pump_on(control, runtime, calls)
    control.save([{**PUMP_CONFIG, "enabled": False}] if change == "disabled" else [])
    reading(runtime, grid=2000)
    await control.tick(now=1060)
    assert calls == [PUMP_ON, PUMP_OFF]
    await control.tick(now=1090)
    assert calls == [PUMP_ON, PUMP_OFF]  # once, then forgotten


async def test_a_device_that_was_not_reachable_is_switched_off_on_the_next_tick(tmp_path, monkeypatch):
    calls = []
    runtime = runtime_with(tmp_path, monkeypatch, calls)
    control = SurplusControl(runtime)
    control.save([PUMP_CONFIG])
    await pump_on(control, runtime, calls)
    control.save([])

    def offline(url):
        raise OSError("timed out")
    monkeypatch.setattr(consumers_module, "_call", offline)
    await control.tick(now=1060)
    monkeypatch.setattr(consumers_module, "_call", calls.append)
    await control.tick(now=1090)
    assert calls == [PUMP_ON, PUMP_OFF]


async def test_test_mode_still_switches_off_what_openampere_switched_on(tmp_path, monkeypatch):
    calls = []
    runtime = runtime_with(tmp_path, monkeypatch, calls)
    control = SurplusControl(runtime)
    control.save([PUMP_CONFIG])
    await pump_on(control, runtime, calls)
    runtime.config.control.dry_run = True
    reading(runtime, grid=2000)  # no surplus any more
    for now in (1060, 1090, 1120):
        await control.tick(now=now)
    assert calls == [PUMP_ON, PUMP_OFF]
    entry = runtime.storage.control_log()[0]
    assert not entry["dry_run"] and "trotz Testmodus" in entry["result"]
    # in test mode nothing is switched on, and nothing switched on only "on paper" is switched off for real
    reading(runtime, grid=-2000)
    for now in (1150, 1180, 1210):
        await control.tick(now=now)
    reading(runtime, grid=2000)
    for now in (1240, 1270, 1300):
        await control.tick(now=now)
    assert calls == [PUMP_ON, PUMP_OFF]


@pytest.mark.parametrize("situation", ["no surplus", "control off", "no readings", "switched off by hand"])
async def test_after_a_restart_a_device_switched_on_is_still_switched_off(tmp_path, monkeypatch, situation):
    calls = []
    runtime = runtime_with(tmp_path, monkeypatch, calls)
    control = SurplusControl(runtime)
    control.save([PUMP_CONFIG])
    await pump_on(control, runtime, calls)

    restarted = SurplusControl(runtime)  # same database, nothing in memory
    reading(runtime, grid=2000)
    if situation == "control off":
        runtime.config.control.enabled = False
    elif situation == "no readings":
        runtime.collector.latest = None
    elif situation == "switched off by hand":
        reading(runtime, grid=-2000)  # surplus, but the owner wants it off
        restarted.set_override(restarted.consumers[0].id, "off", now=2000)
    for now in (2000, 2030, 2060):
        await restarted.tick(now=now)
    assert calls == [PUMP_ON, PUMP_OFF]


async def test_a_device_never_switched_on_by_openampere_is_left_alone_after_a_restart(tmp_path, monkeypatch):
    calls = []
    runtime = runtime_with(tmp_path, monkeypatch, calls)
    SurplusControl(runtime).save([PUMP_CONFIG])
    restarted = SurplusControl(runtime)
    runtime.config.control.enabled = False
    await restarted.tick(now=2000)
    assert calls == []


# #243: further ways a device could stay on
ROD_CONFIG = {"name": "Heizstab", "kind": "mypv", "host": ROD, "power_w": 3000, "min_power_w": 500, "min_on_min": 0,
              "min_off_min": 0}


class FakeRod:
    def __init__(self, calls):
        self.calls = calls

    async def set_power(self, watts):
        self.calls.append(("rod", watts))

    async def read(self):
        raise ConnectionError("no reading")

    def close(self):
        pass


def offline(url):
    raise OSError("timed out")


@pytest.mark.parametrize("change", [{"host": ROD}, {"channel": 1}, {"kind": "mypv", "min_power_w": 500}])
async def test_the_old_relay_is_switched_off_when_the_device_is_changed(tmp_path, monkeypatch, change):
    calls = []
    runtime = runtime_with(tmp_path, monkeypatch, calls)
    monkeypatch.setattr(consumers_module, "make_heating_rod", lambda c: FakeRod(calls))
    control = SurplusControl(runtime)
    control.save([PUMP_CONFIG])
    await pump_on(control, runtime, calls)
    control.save([{**PUMP_CONFIG, "id": control.consumers[0].id, **change}])
    runtime.config.control.enabled = False
    await control.tick(now=1060)
    await control.tick(now=1090)
    assert calls[1:] == [PUMP_OFF]  # the old relay, once; the new one was never switched on
    assert runtime.storage.get_meta(consumers_module.SWITCHED_ON) == {}


async def test_a_changed_device_whose_old_relay_is_offline_is_switched_off_after_a_restart(tmp_path, monkeypatch):
    calls = []
    runtime = runtime_with(tmp_path, monkeypatch, calls)
    control = SurplusControl(runtime)
    control.save([PUMP_CONFIG])
    await pump_on(control, runtime, calls)
    control.save([{**PUMP_CONFIG, "id": control.consumers[0].id, "host": ROD}])
    monkeypatch.setattr(consumers_module, "_call", offline)
    runtime.config.control.enabled = False
    await control.tick(now=1060)

    monkeypatch.setattr(consumers_module, "_call", calls.append)
    restarted = SurplusControl(runtime)
    await restarted.tick(now=2000)
    assert calls == [PUMP_ON, PUMP_OFF]


async def test_a_backup_restore_keeps_which_relays_openampere_switched_on(tmp_path, monkeypatch):
    calls = []
    runtime = runtime_with(tmp_path, monkeypatch, calls)
    backup = tmp_path / "backup.db"
    Storage(backup).close()  # an older backup without this device
    control = SurplusControl(runtime)
    control.save([PUMP_CONFIG])
    await pump_on(control, runtime, calls)
    runtime.storage.stage_restore(backup)
    runtime.storage.close()

    runtime = runtime_with(tmp_path, monkeypatch, calls)
    restarted = SurplusControl(runtime)
    assert restarted.consumers == []  # the device is not in the backup ...
    await restarted.tick(now=2000)
    assert calls == [PUMP_ON, PUMP_OFF]  # ... but its relay is switched off with the configuration it was switched on with


async def test_a_relay_remembered_by_an_older_version_is_still_switched_off(tmp_path, monkeypatch):
    calls = []
    runtime = runtime_with(tmp_path, monkeypatch, calls)
    control = SurplusControl(runtime)
    control.save([{**PUMP_CONFIG, "enabled": False}])
    runtime.storage.set_meta(consumers_module.SWITCHED_ON, {control.consumers[0].id: 1000.0})  # before #243: a number
    await SurplusControl(runtime).tick(now=2000)
    assert calls == [PUMP_OFF]


async def test_after_a_power_cut_a_relay_is_switched_on_again(tmp_path, monkeypatch):
    calls = []
    runtime = runtime_with(tmp_path, monkeypatch, calls)
    control = SurplusControl(runtime)
    control.save([PUMP_CONFIG])
    await pump_on(control, runtime, calls)
    restarted = SurplusControl(runtime)  # the relay may have come back off
    reading(runtime, grid=-3000)
    for now in range(5000, 5300, 30):
        await restarted.tick(now=now)
    assert calls == [PUMP_ON, PUMP_ON]  # sent once more, then not on every tick


async def test_an_unreachable_device_is_tried_less_often_logged_once_and_given_up_after_a_day(tmp_path, monkeypatch):
    calls = []
    runtime = runtime_with(tmp_path, monkeypatch, calls)
    control = SurplusControl(runtime)
    control.save([PUMP_CONFIG])
    await pump_on(control, runtime, calls)
    control.save([{**PUMP_CONFIG, "id": control.consumers[0].id, "enabled": False}])
    tries = []
    monkeypatch.setattr(consumers_module, "_call", tries.append)

    def failing(url):
        tries.append(url)
        raise OSError("timed out")
    monkeypatch.setattr(consumers_module, "_call", failing)
    for now in range(2000, 2000 + 3600, 10):  # an hour of fast ticks
        await control.tick(now=now)
    assert len(tries) < 20  # not every 10 seconds
    restarted = SurplusControl(runtime)
    for now in range(10_000, 10_000 + 26 * 3600, 600):  # still offline after a restart, for more than a day
        await restarted.tick(now=now)
    results = [e["result"] for e in runtime.storage.control_log(0)]
    assert sum(r.startswith("Fehler") for r in results) == 2  # once before and once after the restart
    assert sum("nicht erreichbar" in r for r in results) == 1
    assert runtime.storage.get_meta(consumers_module.SWITCHED_ON) == {}
    count = len(tries)
    await restarted.tick(now=200_000)
    assert len(tries) == count  # given up


async def test_enabling_a_device_again_while_it_is_switched_off_does_not_fail(tmp_path, monkeypatch):
    calls = []
    runtime = runtime_with(tmp_path, monkeypatch, calls)
    control = SurplusControl(runtime)
    control.save([PUMP_CONFIG])
    await pump_on(control, runtime, calls)
    c_id = control.consumers[0].id
    control.save([{**PUMP_CONFIG, "id": c_id, "enabled": False}])

    def enabled_meanwhile(url):  # the owner enables it again while the "off" is on its way
        calls.append(url)
        control.save([{**PUMP_CONFIG, "id": c_id}])
    monkeypatch.setattr(consumers_module, "_call", enabled_meanwhile)
    await control.tick(now=2000)
    assert calls == [PUMP_ON, PUMP_OFF]


async def test_two_ticks_at_once_switch_once(tmp_path, monkeypatch):
    calls = []
    runtime = runtime_with(tmp_path, monkeypatch, calls)
    control = SurplusControl(runtime)
    control.save([PUMP_CONFIG])
    reading(runtime, grid=-2000)
    await control.tick(now=1000)
    await asyncio.gather(control.tick(now=1030), control.tick(now=1031))
    assert calls == [PUMP_ON]


async def test_a_heating_rod_gets_0_w_when_test_mode_is_switched_on(tmp_path, monkeypatch):
    calls = []
    runtime = runtime_with(tmp_path, monkeypatch, calls)
    monkeypatch.setattr(consumers_module, "make_heating_rod", lambda c: FakeRod(calls))
    control = SurplusControl(runtime)
    control.save([ROD_CONFIG])
    reading(runtime, grid=-3500)
    for now in (1000, 1030, 1060):
        await control.tick(now=now)
    assert calls[-1][1] > 0
    runtime.config.control.dry_run = True
    runtime.config.control.enabled = False
    await control.tick(now=1090)
    assert calls[-1] == ("rod", 0)
    assert "trotz Testmodus" in runtime.storage.control_log()[0]["result"]
    count = len(calls)
    await control.stop()
    assert len(calls) == count  # already at 0 W: nothing more to send in test mode


async def test_a_relay_is_switched_off_although_nothing_can_be_written_to_the_database(tmp_path, monkeypatch):
    calls = []
    runtime = runtime_with(tmp_path, monkeypatch, calls)
    control = SurplusControl(runtime)
    control.save([PUMP_CONFIG])

    def full(*args, **kwargs):
        raise sqlite3.OperationalError("database or disk is full")
    monkeypatch.setattr(runtime.storage, "log_control", full)
    monkeypatch.setattr(runtime.storage, "set_meta", full)
    await pump_on(control, runtime, calls)
    runtime.config.control.enabled = False
    await control.tick(now=1060)
    assert calls == [PUMP_ON, PUMP_OFF]


async def test_shutdown_stops_grid_charging_even_when_another_step_fails(tmp_path, monkeypatch):
    from openampere import charging as charging_module
    from openampere.api import create_app
    stopped = []

    async def failing(self):
        raise sqlite3.OperationalError("database or disk is full")

    async def record(self, reason, *args, **kwargs):
        stopped.append(reason)
    monkeypatch.setattr(consumers_module.SurplusControl, "stop", failing)
    monkeypatch.setattr(charging_module.GridCharging, "stop", record)
    app = create_app(Runtime({}, Storage(tmp_path / "t.db")))
    async with app.router.lifespan_context(app):
        pass
    assert stopped == ["OpenAmpere wird beendet"]
