# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
import asyncio
import time
from datetime import datetime

import pytest

from openampere import discovery
from openampere.control import BatteryControl
from openampere.drivers import registry
from openampere.drivers.foxess.registers import H3_NEW
from openampere.drivers.modbus import DeviceUnreachable
from openampere.runtime import Runtime
from openampere.simulator import SajSimulatedInverter, SimulatedInverter
from openampere.storage import Storage


async def serve(sim):
    sim.energy.step(datetime(2026, 6, 21, 13, 0), 600)
    sim.update_registers()
    server = await asyncio.start_server(sim.serve_client, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


async def test_detects_foxess():
    sim = SimulatedInverter(H3_NEW, "H3-10.0-Smart", "SN1", strict_function=False, max_connections=3)
    server, port = await serve(sim)
    async with server:
        info = await registry.detect("127.0.0.1", port)
    assert (info.driver, info.unit, info.manufacturer) == ("foxess", 247, "FoxESS")
    assert info.supports_control and info.rated_power_w == 10000


@pytest.mark.parametrize("unit", [1, 2])
async def test_detects_saj_on_either_unit(unit):
    sim = SajSimulatedInverter(unit=unit, max_connections=3)
    server, port = await serve(sim)
    async with server:
        info = await registry.detect("127.0.0.1", port)
        driver = registry.create(info.driver, "127.0.0.1", port, info.unit, timeout=3)
        await driver.connect()
        snap = await driver.read()
        await driver.close()
    assert (info.driver, info.unit, info.manufacturer) == ("saj", unit, "SAJ")
    assert info.model == "H2 5-10K S3" and info.serial == "HST2103SIM00001" and info.supports_control
    assert info.energy_step_wh == 10  # counters in 0.01 kWh (#194)
    e = sim.energy
    assert snap.pv_power == pytest.approx(e.pv_w, abs=2)
    assert snap.grid_power == pytest.approx(e.grid_w, abs=2)  # + = import
    assert snap.battery_power == pytest.approx(e.battery_w, abs=2)  # + = discharge
    assert snap.battery_soc == pytest.approx(e.soc, abs=0.1)
    assert len(snap.pv_inputs) == 4 and snap.pv_inputs[0].power == pytest.approx(e.pv_inputs_w[0], abs=2)
    assert snap.totals.grid_import == pytest.approx(e.totals["grid_import"], abs=20)
    assert {"inverter", "ambient", "battery"} <= set(snap.temperatures)


async def test_unreachable_fails_fast():
    started = time.monotonic()
    with pytest.raises(DeviceUnreachable):
        await registry.detect("127.0.0.1", 1)
    assert time.monotonic() - started < 3


async def test_saj_0x005e_has_a_name_and_control(caplog):
    """#339: the three-phase type sold as "Ampere.StoragePro" is no unknown code any more."""
    sim = SajSimulatedInverter(max_connections=3, device_type=0x005E)
    server, port = await serve(sim)
    async with server:
        driver = registry.create("saj", "127.0.0.1", port, 1, timeout=3)
        info = await driver.connect()
        await driver.close()
    assert info.model == "3P-X E2" and info.supports_control
    assert "unknown SAJ device type" not in caplog.text


async def test_runtime_auto_driver_with_an_saj_as2_stays_read_only(tmp_path):
    """Control is enabled for H2/HS2 (#377), not for AS2 and other types of the family."""
    sim = SajSimulatedInverter(unit=2, max_connections=3, device_type=0x0055)
    server, port = await serve(sim)
    async with server:
        runtime = Runtime({}, Storage(tmp_path / "t.db"))
        try:
            await runtime.update_settings({"inverter.host": "127.0.0.1", "inverter.port": port,
                                           "inverter.poll_interval": 2})
            for _ in range(150):
                if runtime.collector.latest:
                    break
                await asyncio.sleep(0.1)
            assert runtime.collector.device.driver == "saj" and not runtime.collector.device.supports_control
            result = await discovery.test_connection(runtime, "127.0.0.1", port, 0)
            assert result["ok"] and result["device"]["driver"] == "saj"
            await runtime.update_settings({"control.enabled": True, "control.dry_run": False})
            with pytest.raises(ValueError, match="noch nicht freigegeben"):
                await BatteryControl(runtime).write({"min_soc_on_grid": 30})
        finally:
            await runtime.collector.stop()


async def test_test_connection_reports_label_and_choice(tmp_path):
    sim = SimulatedInverter(H3_NEW, "H3-10.0-Smart", "SN1", strict_function=False, max_connections=3)
    server, port = await serve(sim)
    async with server:
        runtime = Runtime({}, Storage(tmp_path / "t.db"))
        ok = await discovery.test_connection(runtime, "127.0.0.1", port, 0)
        assert ok["ok"] and ok["label"].startswith("FoxESS") and ok["sample"]["battery_soc"] is not None
        wrong = await discovery.test_connection(runtime, "127.0.0.1", port, 0, driver="saj")
        assert not wrong["ok"]


@pytest.mark.parametrize("depth_registers", [True, False])
async def test_saj_reads_on_grid_and_off_grid_depth(depth_registers):
    sim = SajSimulatedInverter(max_connections=3, depth_registers=depth_registers)
    server, port = await serve(sim)
    async with server:
        driver = registry.create("saj", "127.0.0.1", port, 1, timeout=3)
        await driver.connect()
        settings = await driver.read_settings()
        snap = await driver.read()
        await driver.close()
    # the backup reserve is the on-grid depth, not the off-grid one (#334); without the settings registers
    # (some firmware) the on-grid limit comes from 0x402A, the off-grid one stays unknown, live values still work
    off_grid = 5 if depth_registers else None
    assert (settings.min_soc_on_grid, settings.min_soc, settings.max_soc) == (20, off_grid, 100)
    assert driver.has_depth_registers == depth_registers
    assert snap.battery_soc == pytest.approx(sim.energy.soc, abs=0.1)


async def test_saj_diagnostics_shows_both_sets_of_limits(tmp_path):
    from openampere.diagnostics import Diagnostics
    sim = SajSimulatedInverter(max_connections=3)
    server, port = await serve(sim)
    async with server:
        runtime = Runtime({}, Storage(tmp_path / "t.db"))
        try:
            await runtime.update_settings({"inverter.host": "127.0.0.1", "inverter.port": port,
                                           "inverter.poll_interval": 2})
            for _ in range(150):
                if runtime.collector.latest:
                    break
                await asyncio.sleep(0.1)
            checks = {c["id"]: c for c in (await Diagnostics(runtime).run())["checks"]}
        finally:
            await runtime.collector.stop()
    assert checks["saj_limits"]["summary"] == (
        "Netzbetrieb 20 %, Notstrombetrieb 5 %, Ladegrenze 100 %, Betriebsart 0 (0x3644–0x3647); "
        "Reserve im Backup-Modus 10 % (0x3651); Rücklese-Register 0x4029 100 %, 0x402A 20 %, 0x402B 0 %, 0x402C 10 %")


def test_saj_settings_prefer_the_depth_registers():
    from openampere.drivers.saj.driver import to_settings
    raw = {"app_mode": 0, "max_soc": 100, "min_soc_on_grid": 25, "reserve_soc": 0}
    # without the settings registers only the on-grid limit is known; 0x402C is not taken for off grid (#334)
    assert (to_settings(raw).min_soc_on_grid, to_settings(raw).min_soc) == (25, None)
    settings = to_settings(raw, [30, 10, 90, 2])  # 0x3644–0x3647, the registers OpenAmpere writes (#377)
    assert (settings.min_soc_on_grid, settings.min_soc, settings.max_soc, settings.work_mode.value) == (30, 10, 90, "backup")


def test_saj_grid_counter_source_does_not_switch():
    from openampere.drivers.saj.driver import to_snapshot
    raw = {"grid_import_sum_total": 5000.0, "grid_import_l1_total": 1700.0}
    assert to_snapshot(raw, 0, "sum").totals.grid_import == 5000.0
    # the summed register is missing once: no fallback to L1 (would look like -3300 kWh)
    assert to_snapshot({"grid_import_l1_total": 1700.0}, 0, "sum").totals.grid_import is None
