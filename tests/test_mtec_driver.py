# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""M-TEC Energy-Butler GEN3 (#520): read only, plain Modbus TCP or RTU frames over TCP, unit 252, 255 or 247."""

import asyncio
import time
from datetime import datetime

import pytest

from openampere.drivers import registry
from openampere.drivers.modbus import DetectionFailed, DeviceRefused
from openampere.drivers.mtec import driver as mtec_driver
from openampere.drivers.mtec.driver import to_snapshot
from openampere.runtime import Runtime
from openampere.simulator import MtecSimulatedInverter, SajSimulatedInverter, crc16
from openampere.storage import Storage


async def serve(sim):
    server = await asyncio.start_server(sim.serve_client, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


def sunny(sim):
    sim.energy.step(datetime(2026, 6, 1, 13, 0), 60)
    sim.update_registers()
    return sim


def test_the_rtu_checksum():
    assert crc16(bytes.fromhex("FC0327100008")) == 0x905A  # sent as 5A 90


@pytest.mark.parametrize(("rtu", "unit"), [(False, 252), (False, 255), (True, 252), (True, 255), (True, 247)])
async def test_read_in_either_framing_and_on_its_unit_ids(rtu, unit):
    sim = sunny(MtecSimulatedInverter(rtu=rtu, unit=unit, max_connections=3))
    server, port = await serve(sim)
    async with server:
        info, driver = await registry.detect_driver("127.0.0.1", port, only="mtec", timeout=0.5)
        try:
            snap = (await driver.read()).sanitize()
        finally:
            await driver.close()
    assert (info.driver, info.manufacturer, info.unit, info.supports_control) == ("mtec", "M-TEC", unit, False)
    assert info.register_map == f"mtec_gen3 ({'rtu' if rtu else 'socket'})" and info.serial == "MTEC10K3G25SIM"
    assert info.energy_step_wh == 100  # counters in 0.1 kWh
    e = sim.energy
    assert e.grid_w < 0 and e.battery_w < 0  # exporting and charging: every sign matters
    assert snap.pv_power == pytest.approx(e.pv_w, abs=1)
    assert snap.grid_power == pytest.approx(e.grid_w, abs=1)  # + = import (M-TEC: + = export)
    assert snap.battery_power == pytest.approx(e.battery_w, abs=1)  # + = discharging
    assert snap.house_power == pytest.approx(e.house_w, abs=2)  # inverter AC − grid
    assert snap.battery_soc == pytest.approx(e.soc, abs=0.01)
    assert snap.totals.pv == pytest.approx(sim.counter_base["pv"] + e.totals["pv"], abs=100)
    assert len(snap.pv_inputs) == 2 and snap.off_grid is False


@pytest.mark.parametrize("rtu", [False, True])
async def test_found_by_automatic_detection_and_by_no_other_driver(rtu):
    """The setup's path: FoxESS (247), SAJ (1, 2) and Solarmax (1) are asked first and must not claim it."""
    mtec_driver._silent.clear()
    sim = sunny(MtecSimulatedInverter(rtu=rtu, max_connections=3))
    server, port = await serve(sim)
    async with server:
        info = await registry.detect("127.0.0.1", port)
    assert (info.driver, info.unit) == ("mtec", 252)


async def test_a_silent_host_does_not_hold_up_detection(monkeypatch):
    """In the automatic detection, a Modbus device that answers nothing gets one try per framing on the first unit,
    then plain Modbus TCP only (the other drivers are left out here to time M-TEC alone)."""
    mtec_driver._silent.clear()
    monkeypatch.setattr(registry, "DRIVERS", {"mtec": mtec_driver.MtecDriver})
    connected = []

    async def silent(reader, writer):
        connected.append(True)
        await reader.read()  # never answers
        writer.close()
    server = await asyncio.start_server(silent, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        started = time.monotonic()
        with pytest.raises(DetectionFailed):
            await registry.detect("127.0.0.1", port)
        elapsed = time.monotonic() - started
    # unit 252 in both framings, 255 and 247 in plain Modbus TCP only: four timeouts of 2.5 s and their connects,
    # not twelve timeouts (more than 30 s)
    assert elapsed < 20, elapsed


async def test_the_firmware_is_optional():
    """A slow module that does not answer the firmware still counts as identified (and keeps its framing)."""
    mtec_driver._silent.clear()
    sim = MtecSimulatedInverter(max_connections=3)
    for address in range(10011, 10015):
        del sim.regs[address]  # refused: as optional as not answering
    server, port = await serve(sim)
    async with server:
        info = await registry.detect("127.0.0.1", port, only="mtec")
    assert info.driver == "mtec" and info.firmware is None and info.register_map == "mtec_gen3 (socket)"


async def test_the_report_shows_the_raw_signs_and_the_backup_output(tmp_path):
    from openampere.diagnostics import Diagnostics
    mtec_driver._silent.clear()
    sim = sunny(MtecSimulatedInverter(max_connections=3))
    server, port = await serve(sim)
    async with server:
        runtime = Runtime({}, Storage(tmp_path / "t.db"))
        try:
            await runtime.update_settings({"inverter.host": "127.0.0.1", "inverter.port": port,
                                           "inverter.driver": "mtec", "inverter.poll_interval": 2})
            for _ in range(150):
                if runtime.collector.latest:
                    break
                await asyncio.sleep(0.1)
            report = await Diagnostics(runtime).run()
        finally:
            await runtime.collector.stop()
    check = {c["id"]: c for c in report["checks"]}["mtec_values"]
    powers = check["details"]["powers_rounded_w"]
    assert powers["grid_power"] == round(-sim.energy.grid_w, -2) and "backup_power" in powers
    assert check["details"]["framing"] == "socket" and "MTEC10K3G25SIM" not in str(report["checks"])


def test_off_grid_and_faults():
    assert to_snapshot({"running_state": 5}, 0).off_grid is True
    assert to_snapshot({"running_state": 3, "fault1": 0x10, "fault2": 0}, 0).alarms == [0x10, 0]
    assert to_snapshot({"running_state": 2, "fault1": 0x10}, 0).alarms == []


async def test_not_claimed_for_an_saj():
    saj = SajSimulatedInverter(max_connections=3)
    server, port = await serve(saj)
    async with server:
        with pytest.raises(DetectionFailed):
            await registry.detect("127.0.0.1", port, only="mtec", timeout=0.5)


async def test_never_written():
    sim = MtecSimulatedInverter(max_connections=3)
    server, port = await serve(sim)
    async with server:
        driver = registry.create("mtec", "127.0.0.1", port, 252, timeout=1)
        try:
            await driver.connect()
            for write in (lambda: driver.write_soc_limits(min_soc=20), lambda: driver.set_remote_power(-1000, 180),
                          lambda: driver.write_export_limit(5000)):
                with pytest.raises(DeviceRefused, match="nur die Werte an"):
                    await write()
            assert await driver.release_remote_power() is False and await driver.remote_active() is False
        finally:
            await driver.close()
