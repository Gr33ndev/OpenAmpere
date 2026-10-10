# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
import asyncio

import pytest

from openampere import consumers as consumers_module
from openampere.consumers import SurplusControl
from openampere.drivers.base import Snapshot
from openampere.runtime import Runtime
from openampere.simulator import SimulatedHeatingRod
from openampere.storage import Storage


@pytest.fixture
async def rod():
    sim = SimulatedHeatingRod()
    server = await asyncio.start_server(sim.serve_client, "127.0.0.1", 0)
    async with server:
        yield sim, server.sockets[0].getsockname()[1]


def setup(tmp_path, monkeypatch, port, **extra):
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    runtime.config.control.enabled = True
    runtime.config.control.dry_run = False
    monkeypatch.setattr(type(runtime.collector), "stale", property(lambda self: False))
    control = SurplusControl(runtime, extra.pop("evcc", None))
    control.save([{"name": "Heizstab", "kind": "mypv", "host": "127.0.0.1", "port": port, "power_w": 3000,
                   "min_power_w": 500, "min_on_min": 0, "min_off_min": 0, **extra}])
    return runtime, control


def reading(runtime, grid, battery=0.0, soc=90.0):
    runtime.collector.latest = Snapshot(timestamp=0, grid_power=grid, battery_power=battery, battery_soc=soc)


async def test_rod_follows_the_surplus(tmp_path, monkeypatch, rod):
    sim, port = rod
    runtime, control = setup(tmp_path, monkeypatch, port)
    try:
        reading(runtime, grid=-2500)  # 2.5 kW export
        await control.tick(now=0)
        assert sim.setpoint == 0  # confirm first (clouds)
        await control.tick(now=10)
        assert sim.setpoint == 1700  # ramps up: 70 % of the way to 2400 W
        reading(runtime, grid=-800)  # the rod now takes 1.7 kW, 0.8 kW are still exported
        await control.tick(now=20)
        assert sim.setpoint == 2200
        reading(runtime, grid=500)  # a cloud: 0.5 kW import -> down at once
        await control.tick(now=30)
        assert sim.setpoint == 1600
        reading(runtime, grid=2000)  # no sun: off
        await control.tick(now=40)
        assert sim.setpoint == 0
        log = [e["result"] for e in runtime.storage.control_log() if e["action"] == "consumer"]
        assert log[0].startswith("kein Überschuss") and log[1].startswith("Überschuss")  # start and stop only
    finally:
        await control.stop()


async def test_battery_first_and_hot_water(tmp_path, monkeypatch, rod):
    sim, port = rod
    runtime, control = setup(tmp_path, monkeypatch, port, battery_min_soc=80)
    try:
        reading(runtime, grid=0, battery=-3000, soc=50)  # battery below 80 % takes everything
        await control.tick(now=0)
        await control.tick(now=10)
        assert sim.setpoint == 0
        reading(runtime, grid=0, battery=-3000, soc=85)  # now the charging power goes to the rod
        await control.tick(now=20)
        await control.tick(now=30)
        assert sim.setpoint == 2050  # 70 % of 2900 W, in 50 W steps
        sim.temperature = 61  # water is hot: the rod takes nothing although the setpoint stays
        await control.tick(now=40)
        state = control.view()["consumers"][0]["state"]
        assert state["status"] == "Wasser hat Zieltemperatur" and state["actual_w"] == 0
    finally:
        await control.stop()


class FakeEvcc:
    def __init__(self, loadpoints):
        self.state = {"loadpoints": loadpoints}

    def fresh(self):
        return self.state


async def test_waiting_car_gets_room_first(tmp_path, monkeypatch, rod):
    sim, port = rod
    car = {"heating": False, "connected": True, "charging": False, "mode": "pv", "soc": 40, "limit_soc": 80,
           "min_current_a": 6, "phases": 1}
    runtime, control = setup(tmp_path, monkeypatch, port, evcc=FakeEvcc([car]))
    try:
        reading(runtime, grid=-2500)
        await control.tick(now=0)
        await control.tick(now=10)
        assert sim.setpoint == 700  # 2500 - 1380 W for the car to start - 100 W margin, ramped (70 %)
        runtime.config.evcc.priority = "devices_first"
        await control.tick(now=20)
        assert sim.setpoint == 2300  # without the car first, the rod may take everything (ramping up)
    finally:
        await control.stop()


async def test_cheap_grid_power_and_test_mode(tmp_path, monkeypatch, rod):
    sim, port = rod
    runtime, control = setup(tmp_path, monkeypatch, port, price_limit_ct=15)
    try:
        monkeypatch.setattr(control, "_price_now", lambda now: 12.0)
        reading(runtime, grid=1000)  # no sun at all
        await control.tick(now=0)
        await control.tick(now=10)
        assert sim.setpoint == 2100  # heats with cheap grid power (ramping up)

        await control.set_power(control.consumers[0], 0, "test", now=15)
        control.states.clear()
        runtime.config.control.dry_run = True
        monkeypatch.setattr(control, "_price_now", lambda now: 30.0)
        reading(runtime, grid=-3000)
        await control.tick(now=20)
        await control.tick(now=30)
        assert sim.setpoint == 0 and runtime.storage.control_log()[0]["dry_run"]
    finally:
        await control.stop()


async def test_rod_off_without_fresh_readings(tmp_path, monkeypatch, rod):
    sim, port = rod
    runtime, control = setup(tmp_path, monkeypatch, port)
    try:
        reading(runtime, grid=-3000)
        await control.tick(now=0)
        await control.tick(now=10)
        assert sim.setpoint > 0
        monkeypatch.setattr(type(runtime.collector), "stale", property(lambda self: True))
        await control.tick(now=20)
        assert sim.setpoint == 0
    finally:
        await control.stop()


def test_heating_rod_validation():
    with pytest.raises(ValueError, match="Mindestüberschuss"):
        consumers_module.validate([{"name": "Heizstab", "kind": "mypv", "host": "rod.local", "power_w": 3000,
                                    "min_power_w": 4000}])
