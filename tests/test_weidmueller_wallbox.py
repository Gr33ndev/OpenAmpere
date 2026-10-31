# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Weidmüller AC SMART, sold by EKD as "AMPERE.Charge" (#523): the read-only check and the live view, never written."""

import asyncio

import pytest

from openampere import probe
from openampere.probe import Probe
from openampere.runtime import Runtime
from openampere.simulator import SimulatedWeidmueller
from openampere.storage import Storage
from openampere.wallbox import WallboxCheck
from openampere.wallbox import modbus as wallbox_modbus
from openampere.wallbox.native import MODELS, NativeWallboxes
from openampere.wallbox.weidmueller import WeidmuellerAcSmartLive


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(probe, "REQUEST_GAP_S", 0)
    monkeypatch.setattr(probe, "CONNECT_SETTLE_S", 0)
    monkeypatch.setattr(wallbox_modbus, "REQUEST_GAP_S", 0)


@pytest.fixture
def runtime(tmp_path):
    rt = Runtime({}, Storage(tmp_path / "t.db"))
    yield rt
    rt.storage.close()


async def serve(wallbox):
    server = await asyncio.start_server(wallbox.serve_client, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


def test_the_values_with_the_low_word_first():
    idle = WeidmuellerAcSmartLive.interpret(SimulatedWeidmueller().regs)
    assert (idle.state, idle.connected, idle.charging, idle.power_w) == ("idle", False, False, 0.0)
    assert idle.voltage_v == [230.0, 231.0, 232.0] and idle.installed_phases == 3
    assert (idle.max_current_a, idle.steps_a[0], idle.steps_a[-1]) == (16, 6, 16)
    assert idle.firmware == "01.05.00" and idle.session_kwh is None
    charging = WeidmuellerAcSmartLive.interpret(SimulatedWeidmueller(charging=True).regs)
    assert (charging.state, charging.connected, charging.charging) == ("charging", True, True)
    assert charging.power_w == 11040 and charging.current_a == [16.0, 16.0, 16.0] and charging.active_phases == 3
    assert (charging.session_kwh, charging.session_s, charging.set_current_a) == (2.5, 1800, 16)


@pytest.mark.parametrize(("car", "evse", "state"), [("B", 3, "waiting"), ("B", 5, "plugged_in"), ("C", 12, "paused"),
                                                    ("B", 15, "fault"), ("E", 0, "fault"), ("Z", 0, "unknown")])
def test_states(car, evse, state):
    regs = {**SimulatedWeidmueller().regs, 301: ord(car), 302: evse}
    assert WeidmuellerAcSmartLive.interpret(regs).state == state


def test_values_in_the_other_word_order_are_left_out():
    """If the word order were the other way round, 230 V would read as thousands of volts, 11 kW as about 1.95 MW and
    16 A as about a million amperes: none of them is shown, and a charge with such a power is not taken as one."""
    regs = SimulatedWeidmueller(charging=True).regs
    swapped = {**regs, **{a: regs[a + 1] for a in (400, 406, 418)}, **{a + 1: regs[a] for a in (400, 406, 418)}}
    reading = WeidmuellerAcSmartLive.interpret(swapped)
    assert reading.voltage_v[0] is None and reading.current_a[0] is None
    assert (reading.state, reading.charging, reading.power_w) == ("unknown", False, 0.0)


async def test_the_check_reads_only_and_keeps_the_energy_counter_out(runtime):
    wallbox = SimulatedWeidmueller(charging=True)
    server, port = await serve(wallbox)
    async with server:
        result = await WallboxCheck(Probe(runtime)).run("weidmueller_ac_smart", "127.0.0.1", port, None)
    assert set(wallbox.functions) == {3}
    assert (result["outcome"], result["unit"]) == ("readable", 255)
    f = result["findings"]
    assert (f["firmware"], f["car_state"], f["evse_state"], f["power_kw"]) == ("01.05.00", "C", 11, 11.04)
    assert (f["user_limit_a"], f["fallback_current_a"], f["satellite"], f["energy_plausible"]) == (16, 0, False, True)
    assert {b["start"] for b in result["blocks"]} >= {300, 317, 400, 700, 704}  # no undocumented address in a block
    energy = next(b for b in result["blocks"] if b["start"] == 457)
    assert energy["words"] == [None] * 4  # private: only whether it is plausible


async def test_shown_as_a_wallbox_and_never_written(tmp_path, monkeypatch):
    wallbox = SimulatedWeidmueller(charging=True)
    server, port = await serve(wallbox)
    async with server:
        runtime = Runtime({}, Storage(tmp_path / "t.db"))
        runtime.config.control.enabled, runtime.config.control.dry_run = True, False  # writing allowed: still none
        monkeypatch.setattr(type(runtime.collector), "stale", property(lambda self: False))
        wallboxes = NativeWallboxes(runtime)
        wallboxes.save([{"name": "Carport", "model": "weidmueller_ac_smart", "host": "127.0.0.1", "port": port}])
        try:
            with pytest.raises(ValueError):
                wallboxes.set_mode(wallboxes.wallboxes[0].id, "pv", source="test")
            wallboxes.refresh()
            await wallboxes.wait()
            view = wallboxes.view()
        finally:
            runtime.storage.close()
    item = view["wallboxes"][0]
    assert item["read_only"] and item["modes"] == ["manual"] and item["unit"] == 255
    assert item["reading"]["charging"] and item["reading"]["power_w"] == 11040
    assert set(wallbox.functions) == {3}
    assert MODELS["weidmueller_ac_smart"].read_only and not getattr(MODELS["weidmueller_ac_smart"], "controllable", False)
