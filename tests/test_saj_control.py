# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Control of SAJ H2/HS2 (#377): battery settings, export limit and grid charging through a charge slot that ends
by itself."""

import asyncio
from datetime import datetime

import pytest

from openampere.charging import REMOTE_COMMAND, GridCharging
from openampere.control import BatteryControl, ExportLimitControl
from openampere.drivers import registry
from openampere.drivers.saj import registers as saj
from openampere.drivers.saj.driver import NotSupported, charge_slot, minutes_left
from openampere.runtime import Runtime
from openampere.simulator import SajSimulatedInverter
from openampere.storage import Storage

from test_detection import serve

MONDAY_10 = datetime(2026, 10, 12, 10, 0)  # a Monday
SLOT = saj.CHARGE_SLOT_ADDRESS
BIT = 1 << (saj.CHARGE_SLOT - 1)


def hhmm(hour: int, minute: int) -> int:
    return hour << 8 | minute


async def driver_for(sim):
    server, port = await serve(sim)
    driver = registry.create("saj", "127.0.0.1", port, 1, timeout=3)
    await driver.connect()
    return server, driver


async def connected_runtime(tmp_path, sim):
    server, port = await serve(sim)
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    await runtime.update_settings({"inverter.host": "127.0.0.1", "inverter.port": port, "inverter.poll_interval": 2,
                                   "control.enabled": True, "control.dry_run": False})
    for _ in range(150):
        if runtime.collector.latest:
            break
        await asyncio.sleep(0.1)
    return server, runtime


def slot_words(sim) -> list[int]:
    return [sim.regs[SLOT + i] for i in range(3)]


def test_the_slot_is_short_today_only_and_never_past_midnight():
    assert charge_slot(MONDAY_10, 30) == [hhmm(10, 0), hhmm(10, 10), 0x01 << 8 | 30]
    late_sunday = datetime(2026, 10, 18, 23, 55)
    assert charge_slot(late_sunday, 100) == [hhmm(23, 55), hhmm(23, 59), 0x40 << 8 | 100]
    slot = charge_slot(MONDAY_10, 30)
    assert minutes_left(MONDAY_10.replace(minute=6), slot) == 4
    assert minutes_left(datetime(2026, 10, 13, 10, 5), slot) == 0  # another day


async def test_battery_settings_are_written_and_read_back(tmp_path):
    sim = SajSimulatedInverter(max_connections=3)
    server, runtime = await connected_runtime(tmp_path, sim)
    async with server:
        try:
            battery = BatteryControl(runtime)
            result = await battery.write({"min_soc_on_grid": 30, "max_soc": 90, "work_mode": "backup"})
            assert result["result"] == "ok"
            assert [sim.regs[0x3644 + i] for i in range(4)] == [30, 5, 90, 2]
            assert runtime.collector.device.work_modes == ("self_use", "backup")
            with pytest.raises(ValueError, match="gibt es bei SAJ-Geräten nicht"):
                await battery.write({"work_mode": "feed_in_first"})
            assert sim.regs[saj.APP_MODE_ADDRESS] == 2
        finally:
            await runtime.collector.stop()


async def test_export_limit_in_per_mille_of_the_rated_power(tmp_path):
    sim = SajSimulatedInverter(max_connections=3)
    sim.regs[saj.EXPORT_MODE_ADDRESS] = 0  # switched off: no limit, up to the rated power
    server, runtime = await connected_runtime(tmp_path, sim)
    async with server:
        try:
            control = ExportLimitControl(runtime)
            current = await control.read()
            assert current["supported"] and current["limit_w"] == 10000 and current["step_w"] == 10
            # lowering needs no confirmation; rounded down to the step, and the mode switched on so it applies
            result = await control.write(4321)
            assert result["result"] == "ok" and result["limit_w"] == 4320
            assert sim.regs[saj.EXPORT_LIMIT_ADDRESS] == 432 and sim.regs[saj.EXPORT_MODE_ADDRESS] == 1
            assert sim.energy.export_limit_w == 4320
            sim.regs[saj.EXPORT_MODE_ADDRESS] = 2  # by phase current: not a power OpenAmpere can show or set
            assert not (await control.read())["supported"]
        finally:
            control._verify_task and control._verify_task.cancel()
            await runtime.collector.stop()


async def test_charging_through_a_slot_that_ends_by_itself():
    sim = SajSimulatedInverter(max_connections=3)
    sim.clock = lambda: MONDAY_10
    sim.regs[saj.TOU_OUTSIDE_ADDRESS] = 0
    owner_slot = [hhmm(2, 0), hhmm(5, 0), 0x7F << 8 | 50]  # the owner's slot 7, switched off
    for i, word in enumerate(owner_slot):
        sim.regs[SLOT + i] = word
    sim.regs[saj.CHARGE_ENABLE_ADDRESS] = 0b1  # the owner's own slot 1 stays as it is
    server, driver = await driver_for(sim)
    async with server:
        try:
            restore = await driver.set_remote_power(-3000, 180)
            assert slot_words(sim) == [hhmm(10, 0), hhmm(10, 10), 0x01 << 8 | 30]
            assert sim.regs[saj.CHARGE_ENABLE_ADDRESS] == 0b1 | BIT
            assert sim.regs[saj.APP_MODE_ADDRESS] == saj.APP_MODE_TIME and sim.regs[saj.TOU_OUTSIDE_ADDRESS] == 1
            assert sim.energy.remote_enabled and sim.energy.remote_power_w == -3000
            assert restore["app_mode"] == 0 and restore["outside"] == 0 and restore["slot"] == owner_slot
            # the owner's mode while OpenAmpere charges, not "time-of-use"
            assert (await driver.read_settings()).work_mode.value == "self_use"

            sim.clock = lambda: MONDAY_10.replace(minute=3)
            await driver.set_remote_power(-3000, 180)
            assert slot_words(sim)[1] == hhmm(10, 10)  # enough time left: nothing written
            sim.clock = lambda: MONDAY_10.replace(minute=6)
            await driver.set_remote_power(-3000, 180)
            assert slot_words(sim)[:2] == [hhmm(10, 0), hhmm(10, 16)]  # extended, the start stays

            # OpenAmpere stops writing: the inverter stops charging at the end of the slot by itself
            sim.clock = lambda: MONDAY_10.replace(minute=16)
            await driver.read()
            assert not sim.energy.remote_enabled

            assert await driver.release_remote_power(180)
            assert sim.regs[saj.CHARGE_ENABLE_ADDRESS] == 0b1 and slot_words(sim) == owner_slot
            assert sim.regs[saj.APP_MODE_ADDRESS] == 0 and sim.regs[saj.TOU_OUTSIDE_ADDRESS] == 0
            assert not await driver.release_remote_power(180)  # nothing of OpenAmpere's left
        finally:
            await driver.close()


async def test_a_new_day_gets_a_new_slot():
    sim = SajSimulatedInverter(max_connections=3)
    sim.clock = lambda: datetime(2026, 10, 12, 23, 56)
    server, driver = await driver_for(sim)
    async with server:
        try:
            await driver.set_remote_power(-3000, 180)
            assert slot_words(sim)[:2] == [hhmm(23, 56), hhmm(23, 59)]
            sim.clock = lambda: datetime(2026, 10, 13, 0, 0)  # Tuesday
            await driver.set_remote_power(-3000, 180)
            assert slot_words(sim) == [hhmm(0, 0), hhmm(0, 10), 0x02 << 8 | 30]
            assert sim.energy.remote_enabled
        finally:
            await driver.close()


async def test_never_takes_over_a_slot_in_use_and_never_releases_someone_elses():
    sim = SajSimulatedInverter(max_connections=3)
    sim.clock = lambda: MONDAY_10
    sim.regs[saj.CHARGE_ENABLE_ADDRESS] = BIT  # the owner uses slot 7
    server, driver = await driver_for(sim)
    async with server:
        try:
            with pytest.raises(NotSupported, match="schon belegt"):
                await driver.set_remote_power(-3000, 180)
            assert sim.regs[saj.APP_MODE_ADDRESS] == 0

            sim.regs[saj.CHARGE_ENABLE_ADDRESS] = 0
            restore = await driver.set_remote_power(-3000, 180)
            sim.regs[SLOT + 1] = hhmm(12, 0)  # someone else changed the slot
            assert not await driver.release_remote_power(180, restore=restore)
            assert sim.regs[saj.CHARGE_ENABLE_ADDRESS] == BIT and sim.regs[saj.APP_MODE_ADDRESS] == 1
        finally:
            await driver.close()


async def test_released_after_a_restart_with_what_was_kept():
    sim = SajSimulatedInverter(max_connections=3)
    sim.clock = lambda: MONDAY_10
    sim.regs[saj.APP_MODE_ADDRESS] = 2
    server, driver = await driver_for(sim)
    async with server:
        try:
            restore = await driver.set_remote_power(-2000, 180)
        finally:
            await driver.close()
    second, restarted = await driver_for(sim)  # a new driver object knows nothing
    async with second:
        try:
            assert not await restarted.release_remote_power(180)  # without what was kept: left alone
            assert sim.regs[saj.CHARGE_ENABLE_ADDRESS] == BIT
            # the kept values carry on charging, then put the owner's backup mode back
            assert await restarted.set_remote_power(-2000, 180, restore=restore) == restore
            assert await restarted.release_remote_power(180, restore=restore)
            assert sim.regs[saj.CHARGE_ENABLE_ADDRESS] == 0 and sim.regs[saj.APP_MODE_ADDRESS] == 2
        finally:
            await restarted.close()


async def test_grid_charging_keeps_what_to_put_back(tmp_path):
    sim = SajSimulatedInverter(max_connections=3)
    sim.clock = lambda: MONDAY_10
    server, runtime = await connected_runtime(tmp_path, sim)
    async with server:
        try:
            assert runtime.collector.device.charging_continues_min == 10
            charging = GridCharging(runtime)
            charging.save({"enabled": True, "legal_confirmed": True, "mode": "window", "window_start": 0,
                           "window_end": 0, "target_soc": 100, "power_w": 3000})
            await charging.tick()
            assert charging.active and sim.energy.remote_enabled
            kept = runtime.storage.get_meta(REMOTE_COMMAND)["restore"]
            assert kept["slot_ours"] == slot_words(sim) and kept["app_mode"] == 0
            charging.save({**charging.view()["settings"], "enabled": False})
            await charging.tick()
            assert not charging.active and not sim.energy.remote_enabled
            assert sim.regs[saj.CHARGE_ENABLE_ADDRESS] == 0 and sim.regs[saj.APP_MODE_ADDRESS] == 0
        finally:
            await runtime.collector.stop()


async def test_the_reserve_of_the_backup_mode(tmp_path):
    """#380: in backup mode an SAJ keeps its own reserve (0x3651), not the one of normal operation."""
    sim = SajSimulatedInverter(max_connections=3)
    server, runtime = await connected_runtime(tmp_path, sim)
    async with server:
        try:
            assert runtime.collector.device.backup_reserve
            battery = BatteryControl(runtime)
            state = await battery.read()
            assert state["backup_soc"] == 10 and "backup_soc" not in state["unreadable"]
            result = await battery.write({"work_mode": "backup", "backup_soc": 60})
            assert result["result"] == "ok" and sim.regs[saj.BACKUP_SOC_ADDRESS] == 60
            assert sim.energy.work_mode.value == "backup" and sim.energy.backup_soc == 60
            with pytest.raises(ValueError, match="zwischen 10 und 100"):
                await battery.write({"backup_soc": 5})
            with pytest.raises(ValueError, match="über der Ladegrenze"):
                await battery.write({"max_soc": 90, "backup_soc": 95})
            assert sim.regs[saj.BACKUP_SOC_ADDRESS] == 60
        finally:
            battery._verify_task and battery._verify_task.cancel()
            await runtime.collector.stop()
