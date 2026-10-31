# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Solarmax MAX.STORAGE, sold by EKD as "AMPERE.Storage" (#519): read only, from Solarmax's register list."""

import asyncio
from datetime import datetime

import pytest

from openampere import discovery
from openampere.control import BatteryControl
from openampere.diagnostics import Diagnostics
from openampere.drivers import registry
from openampere.drivers.modbus import DetectionFailed, DeviceRefused
from openampere.drivers.regs import Kind, Reg, decode, encode
from openampere.drivers.solarmax.driver import to_snapshot
from openampere.runtime import Runtime
from openampere.simulator import SajSimulatedInverter, SolarmaxSimulatedInverter
from openampere.storage import Storage


async def serve(sim):
    server = await asyncio.start_server(sim.serve_client, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


def sunny(sim: SolarmaxSimulatedInverter) -> SolarmaxSimulatedInverter:
    """Midday: PV, charging and export, so every sign matters."""
    sim.energy.step(datetime(2026, 6, 1, 13, 0), 60)
    sim.update_registers()
    return sim


async def read_once(sim, **kwargs):
    server, port = await serve(sim)
    async with server:
        info = await registry.detect("127.0.0.1", port, **kwargs)
        driver = registry.create(info.driver, "127.0.0.1", port, info.unit, timeout=3)
        try:
            await driver.connect()
            snap = (await driver.read()).sanitize()
        finally:
            await driver.close()
    return info, driver, snap


def test_a_32_bit_value_with_the_low_word_first():
    reg = Reg(118, Kind.I32, low_word_first=True)
    assert decode(reg, [0xFC18, 0xFFFF]) == -1000  # low word 0xFC18, high word 0xFFFF
    assert encode(reg, -1000) == [0xFC18, 0xFFFF]
    assert decode(Reg(118, Kind.I32), [0xFFFF, 0xFC18]) == -1000  # the other drivers: high word first, unchanged


async def test_detected_automatically_and_read_only():
    sim = sunny(SolarmaxSimulatedInverter(max_connections=3))
    info, _driver, snap = await read_once(sim)
    assert (info.driver, info.unit, info.manufacturer, info.model) == ("solarmax", 1, "Solarmax", "MAX.STORAGE")
    assert (info.firmware, info.serial, info.rated_power_w) == ("3.4.4", "12345678", 10000)
    assert not info.supports_control and info.energy_step_wh == 1000  # counters in whole kWh
    e = sim.energy
    assert e.pv_w > 1000 and e.battery_w < 0 and e.grid_w < 0  # charging and exporting
    assert snap.pv_power == pytest.approx(e.pv_w, abs=1)
    assert snap.battery_power == pytest.approx(e.battery_w, abs=1)  # + = discharging (Solarmax: + = charging)
    assert snap.grid_power == pytest.approx(e.grid_w, abs=1)  # + = import (Solarmax: + = export)
    assert snap.house_power == pytest.approx(e.house_w, abs=1)
    assert snap.battery_soc == round(e.soc)
    assert snap.alarms == [] and snap.off_grid is None
    assert snap.totals.pv == (sim.counter_base["pv"] + e.totals["pv"]) // 1000 * 1000
    assert snap.totals.battery_charge is not None and snap.totals.load is None  # no load counter: the balance


@pytest.mark.parametrize("connectivity", ["rejected", "zeros"])
async def test_without_connectivity_plus_there_are_no_counters_but_live_values(connectivity):
    """The counters (150–159) need "Connectivity+"; zeros are not taken for counters that start at 0."""
    sim = sunny(SolarmaxSimulatedInverter(max_connections=3, connectivity=connectivity))
    _info, driver, snap = await read_once(sim, only="solarmax")
    assert snap.totals.pv is None and snap.totals.grid_import is None
    assert snap.battery_soc is not None and snap.grid_power == pytest.approx(sim.energy.grid_w, abs=1)
    assert driver.has_counters == (connectivity == "zeros")


async def test_counters_that_never_answer_do_not_stop_the_live_values():
    """Without "Connectivity+" a device might also stay silent on 150–159: the live values go on, and after a few reads
    the counters are no longer asked for."""
    sim = sunny(SolarmaxSimulatedInverter(max_connections=3, connectivity="silent"))
    server, port = await serve(sim)
    async with server:
        driver = registry.create("solarmax", "127.0.0.1", port, 1, timeout=1)
        try:
            await driver.connect()
            for _ in range(3):
                snap = await driver.read()
                assert snap.battery_soc is not None and snap.totals.pv is None
        finally:
            await driver.close()
    assert driver.has_counters is False


async def test_without_a_second_inverter_there_is_none_to_count():
    """No hint about a second inverter in the setup for a plant that has none (112 = 110)."""
    _info, _driver, snap = await read_once(sunny(SolarmaxSimulatedInverter(max_connections=3)), only="solarmax")
    assert snap.pv_external_power is None


async def test_a_counted_second_inverter_is_there_at_night_after_a_restart(tmp_path):
    """The owner counts a second inverter; OpenAmpere restarts at night (112 = 110 = 0): it is shown with 0 W, not as
    unknown until the sun comes back."""
    sim = SolarmaxSimulatedInverter(max_connections=3, connectivity="rejected")  # not stepped: no sun
    server, port = await serve(sim)
    async with server:
        runtime = Runtime({}, Storage(tmp_path / "t.db"))
        try:
            await runtime.update_settings({"inverter.host": "127.0.0.1", "inverter.port": port, "inverter.driver": "solarmax",
                                           "inverter.poll_interval": 2, "pv.external": True})
            for _ in range(150):
                if runtime.collector.latest:
                    break
                await asyncio.sleep(0.1)
            assert runtime.collector.external_pv_w == 0.0
        finally:
            await runtime.collector.stop()


async def test_another_inverter_on_the_max_storage_is_external_pv():
    """Without the counters of "Connectivity+" the other inverter is shown on its own and the owner may count it."""
    sim = SolarmaxSimulatedInverter(max_connections=3, connectivity="rejected")
    sim.extra_pv_w = 1500
    sunny(sim)
    _info, _driver, snap = await read_once(sim, only="solarmax")
    assert snap.pv_external_power == pytest.approx(1500, abs=1)
    assert snap.pv_power == pytest.approx(sim.energy.pv_w, abs=1)


async def test_with_counters_another_inverter_is_part_of_the_pv_power():
    """The PV counter (150) includes the inverters connected to the MAX.STORAGE: with it, the PV power is the whole
    plant's, and there is no second inverter to count on top (it would be counted twice)."""
    sim = SolarmaxSimulatedInverter(max_connections=3)
    sim.extra_pv_w = 1500
    sunny(sim)
    _info, _driver, snap = await read_once(sim, only="solarmax")
    assert snap.totals.pv is not None and snap.pv_external_power is None
    assert snap.pv_power == pytest.approx(sim.energy.pv_w + 1500, abs=1)
    raw = {"pv_power": 4000.0, "pv_plant_power": 5500.0, "pv_total": 9e6, "grid_import_total": 1e6,
           "grid_export_total": 2e6, "battery_charge_total": 5e5, "battery_discharge_total": 4e5}
    counted = to_snapshot(raw, 0, external=True)  # also when the owner counts one
    assert (counted.pv_power, counted.pv_external_power) == (5500.0, None)


async def test_one_missed_counter_read_does_not_bring_the_second_inverter_back():
    """While the counters are read, PV stays the whole plant's even when one read of them fails (else the second
    inverter would be counted on top of counter 150 for that reading)."""
    sim = SolarmaxSimulatedInverter(max_connections=3)
    sim.extra_pv_w = 1500
    sunny(sim)
    server, port = await serve(sim)
    async with server:
        driver = registry.create("solarmax", "127.0.0.1", port, 1, timeout=1)
        try:
            await driver.connect()
            driver.assume_external()  # the owner counts a second inverter
            first = await driver.read()
            sim.connectivity = "silent"  # one read of 150–159 gets no answer
            missed = await driver.read()
        finally:
            await driver.close()
    assert first.pv_external_power is None and missed.pv_external_power is None
    assert missed.pv_power == pytest.approx(sim.energy.pv_w + 1500, abs=1) and missed.totals.pv is None
    assert driver.has_counters


def test_an_error_state_is_an_alarm():
    assert to_snapshot({"device_state": 3}, 0).alarms == [3]
    assert to_snapshot({"device_state": 2}, 0).alarms == []  # a warning is no fault


async def test_not_claimed_for_an_saj_and_not_claiming_a_solarmax_as_saj():
    saj = SajSimulatedInverter(max_connections=3)
    server, port = await serve(saj)
    async with server:
        with pytest.raises(DetectionFailed):
            await registry.detect("127.0.0.1", port, only="solarmax")
        assert (await registry.detect("127.0.0.1", port)).driver == "saj"
    solarmax = SolarmaxSimulatedInverter(max_connections=3)
    server, port = await serve(solarmax)
    async with server:
        with pytest.raises(DetectionFailed):
            await registry.detect("127.0.0.1", port, only="saj")


@pytest.mark.parametrize("changes", [{"firmware": 0}, {"device_state": 7}, {"rated_power": 200}, {"battery_soc": 140}])
async def test_implausible_values_are_not_a_solarmax(changes):
    sim = SolarmaxSimulatedInverter(max_connections=3)
    for name, value in changes.items():
        sim._put(name, value)
    sim.update_registers = lambda: None  # keep the implausible value
    server, port = await serve(sim)
    async with server:
        with pytest.raises(DetectionFailed):
            await registry.detect("127.0.0.1", port, only="solarmax")


async def test_the_driver_never_writes():
    sim = SolarmaxSimulatedInverter(max_connections=3)
    server, port = await serve(sim)
    async with server:
        driver = registry.create("solarmax", "127.0.0.1", port, 1, timeout=3)
        try:
            await driver.connect()
            for write in (lambda: driver.write_soc_limits(min_soc=20), lambda: driver.set_remote_power(-1000, 180),
                          lambda: driver.write_export_limit(5000)):
                with pytest.raises(DeviceRefused, match="nur die Werte an"):
                    await write()
            assert await driver.release_remote_power() is False and await driver.remote_active() is False
            assert (await driver.read_export_limit()).supported is False
        finally:
            await driver.close()


async def test_the_app_keeps_control_off_and_the_report_shows_the_raw_signs(tmp_path):
    sim = sunny(SolarmaxSimulatedInverter(max_connections=3))
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
            assert runtime.collector.device.driver == "solarmax" and not runtime.collector.device.supports_control
            result = await discovery.test_connection(runtime, "127.0.0.1", port, 0)
            assert result["ok"] and result["device"]["driver"] == "solarmax"
            assert registry.LABELS["solarmax"] == "Solarmax MAX.STORAGE (nur Anzeige)"
            await runtime.update_settings({"control.enabled": True, "control.dry_run": False})
            with pytest.raises(ValueError, match="noch nicht freigegeben"):
                await BatteryControl(runtime).write({"min_soc_on_grid": 30})
            report = await Diagnostics(runtime).run()
        finally:
            await runtime.collector.stop()
    check = {c["id"]: c for c in report["checks"]}["solarmax_registers"]
    details = check["details"]
    # Solarmax's own signs, rounded: charging and export are positive there
    assert details["powers_rounded_w"]["battery_power"] == round(-sim.energy.battery_w, -2) > 0
    assert details["powers_rounded_w"]["grid_power"] > 0 and details["counters"] == "filled"
    assert "12345678" not in str(report["checks"])  # no serial number in the raw registers
