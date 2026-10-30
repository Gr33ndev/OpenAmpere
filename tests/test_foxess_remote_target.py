# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""#509: the controlled target of the FoxESS remote control (46001 bits 3:2). 1 = the inverter's AC side, where PV is
curtailed once the battery takes no more; 5 = the battery. OpenAmpere writes 5 only where 46007 is readable."""

import asyncio
import random
from datetime import datetime

import pytest

from openampere.drivers.foxess import driver as foxess_driver
from openampere.drivers.foxess.driver import FoxessDriver
from openampere.drivers.foxess.registers import H3_LEGACY, H3_NEW, remote_target
from openampere.drivers.modbus import ModbusTransientError
from openampere.simulator import EnergyModel, SimulatedInverter

NOON = datetime(2026, 6, 21, 13, 0)


def model(target: str, power_w: float, soc: float) -> EnergyModel:
    random.seed(509)
    e = EnergyModel(soc=soc, cloud=1.0)
    e.remote_enabled, e.remote_target, e.remote_power_w = True, target, power_w
    e.remote_deadline = float("inf")
    e.step(NOON, 1)
    return e


def test_remote_target_bits():
    assert [remote_target(v) for v in (1, 5, 9, 13)] == ["ac", "battery", "grid", "ac_grid_first"]
    assert remote_target(3) == "ac"  # bit 1 is the sign, not the target


def test_daytime_charging_with_target_ac_curtails_pv_and_imports():
    """3000 W from the grid on top of PV: the battery takes its maximum, the rest of PV is curtailed."""
    e = model("ac", -3000, 50)
    assert e.battery_w == -e.max_battery_w
    assert e.pv_w == pytest.approx(e.max_battery_w - 3000)
    assert e.grid_w == pytest.approx(e.house_w + 3000)


def test_daytime_charging_with_target_battery_keeps_pv():
    e = model("battery", -3000, 50)
    assert e.battery_w == -3000
    assert e.pv_w > 5000  # not curtailed: PV supplies the house and feeds in
    assert e.grid_w == pytest.approx(e.house_w - e.pv_w + 3000)


def test_leftover_with_0_w():
    """#141/#143: a command left on with 0 W. Target AC with a full battery: PV is curtailed completely and the house
    runs from the grid (evcc). Target battery: the battery only rests."""
    e = model("ac", 0, 100)
    assert e.pv_w == 0 and e.battery_w == 0 and e.grid_w == pytest.approx(e.house_w)
    e = model("ac", 0, 50)  # not full yet: PV only charges the battery, nothing reaches the house
    assert e.battery_w == pytest.approx(-e.pv_w) and e.grid_w == pytest.approx(e.house_w)
    e = model("battery", 0, 100)
    assert e.battery_w == 0 and e.pv_w > 5000 and e.grid_w == pytest.approx(e.house_w - e.pv_w)


async def start(register_map=H3_NEW, model_name="H3-10.0-Smart"):
    sim = SimulatedInverter(register_map, model_name, "SN1", strict_function=False, max_connections=2)
    sim.energy.step(NOON, 600)
    sim.update_registers()
    server = await asyncio.start_server(sim.serve_client, "127.0.0.1", 0)
    driver = FoxessDriver("127.0.0.1", server.sockets[0].getsockname()[1], 247)
    return sim, server, driver


async def test_target_battery_where_46007_is_readable():
    sim, server, driver = await start()
    async with server:
        try:
            await driver.connect()
            await driver.set_remote_power(-3000, timeout_s=180)
            assert sim._get_setting("remote_enable") == 5 and sim.energy.remote_target == "battery"
            assert driver.remote_target == "battery" and driver.remote_fallback is None
            assert sim.energy.remote_power_w == -3000
            sim.update_registers()
            assert 170 <= sim._get_setting("remote_countdown") <= 180  # the watchdog counts down
            assert await driver.release_remote_power()
            assert sim._get_setting("remote_enable") == 0
        finally:
            await driver.close()


async def test_target_ac_without_46007():
    """Firmware without the countdown may not know bits 3:2 (changed in V1.05.01): 1 as before, not stopping."""
    sim, server, driver = await start()
    async with server:
        try:
            del sim.regs[46007]
            await driver.connect()
            await driver.set_remote_power(-3000, timeout_s=180)
            assert sim._get_setting("remote_enable") == 1 and sim.energy.remote_target == "ac"
            assert driver.remote_target == "ac" and driver.remote_fallback == "no_countdown"
            assert sim.energy.remote_enabled and sim.energy.remote_power_w == -3000
        finally:
            await driver.close()


async def test_target_ac_for_this_command_when_46007_does_not_answer():
    sim, server, driver = await start()
    async with server:
        try:
            await driver.connect()
            read_reg = driver._read_reg

            async def no_answer(reg, function=3):
                if reg.address == 46007:
                    raise ModbusTransientError("timeout")
                return await read_reg(reg, function)
            driver._read_reg = no_answer
            await driver.set_remote_power(-3000, timeout_s=180)
            assert sim._get_setting("remote_enable") == 1 and driver.remote_fallback == "no_countdown"
            driver._read_reg = read_reg  # answers again: tried again with the next command
            await driver.set_remote_power(-3000, timeout_s=180)
            assert sim._get_setting("remote_enable") == 5 and driver.remote_fallback is None
        finally:
            await driver.close()


async def test_target_ac_when_5_does_not_read_back(monkeypatch):
    """A firmware that keeps 1 when 5 is written (bits 3:2 ignored): target AC from then on."""
    monkeypatch.setattr(foxess_driver, "READ_BACK_WAITS_S", (0, 0))
    sim, server, driver = await start()
    on_write = sim.on_write

    def ignores_target(address):
        if sim.regs[46001] == 5:
            sim.regs[46001] = 1
        on_write(address)
    sim.on_write = ignores_target
    async with server:
        try:
            await driver.connect()
            await driver.set_remote_power(-3000, timeout_s=180)
            assert sim._get_setting("remote_enable") == 1
            assert driver.remote_target == "ac" and driver.remote_fallback == "readback"
            writes = []
            write = driver._write

            async def logged(reg, value):
                writes.append((reg.address, value))
                await write(reg, value)
            driver._write = logged
            await driver.set_remote_power(-3000, timeout_s=180)  # not tried again on this connection
            assert (46001, 5) not in writes and (46001, 1) in writes
        finally:
            await driver.close()


async def test_legacy_map_stays_at_1():
    sim, server, driver = await start(H3_LEGACY, " H3-10.0-E")
    async with server:
        try:
            await driver.connect()
            await driver.set_remote_power(-3000, timeout_s=180)
            assert sim._get_setting("remote_enable") == 1
            assert driver.remote_target == "ac" and driver.remote_fallback == "legacy"
        finally:
            await driver.close()


@pytest.mark.parametrize("enable, timeout, ours", [(1, 180, True), (5, 180, True), (5, 30, False), (3, 180, False)])
async def test_release_only_openampere_values(enable, timeout, ours):
    """1 or 5 with OpenAmpere's timeout are its own (1 left over by older versions); the smartbox's 5 / 30 s never."""
    sim, server, driver = await start()
    async with server:
        try:
            await driver.connect()
            settings = sim.map.settings
            sim._put("remote_timeout", timeout, settings)
            sim._put("remote_enable", enable, settings)
            assert await driver.release_remote_power(180) is ours
            assert sim._get_setting("remote_enable") == (0 if ours else enable)
        finally:
            await driver.close()
