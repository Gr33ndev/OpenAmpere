# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""M-TEC Energy-Butler GEN3 (#520): read only, plain Modbus TCP or RTU frames over TCP, unit 252, 255 or 247."""

import asyncio
from datetime import datetime

import pytest

from openampere.drivers import registry
from openampere.drivers.modbus import DetectionFailed, DeviceRefused
from openampere.drivers.mtec.driver import to_snapshot
from openampere.simulator import MtecSimulatedInverter, SajSimulatedInverter, crc16


async def serve(sim):
    server = await asyncio.start_server(sim.serve_client, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


def sunny(sim):
    sim.energy.step(datetime(2026, 6, 1, 13, 0), 60)
    sim.update_registers()
    return sim


def test_the_rtu_checksum():
    assert crc16(bytes.fromhex("FC0327100008")) == 0x905A  # sent as 5A 90


@pytest.mark.parametrize(("rtu", "unit"), [(False, 252), (False, 255), (True, 252)])
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
