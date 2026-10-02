import asyncio
from datetime import datetime

import pytest

from openampere.drivers.base import WorkMode
from openampere.drivers.foxess.driver import FoxessDriver
from openampere.drivers.foxess.registers import H3_LEGACY, H3_NEW, Kind, Reg, decode, encode
from openampere.simulator import SimulatedInverter


def test_decode_encode_roundtrip():
    reg = Reg(1, Kind.I32)
    assert decode(reg, encode(reg, -3300)) == -3300
    assert decode(Reg(1, Kind.I16, 0.1), [0xFFF6]) == pytest.approx(-1.0)
    assert decode(Reg(1, Kind.U32, 10), [0x0001, 0x0000]) == 655360


async def start_sim(register_map, model, *, strict=False):
    sim = SimulatedInverter(register_map, model, "SN123", strict_function=strict, max_connections=2)
    sim.energy.step(datetime(2026, 6, 21, 13, 0), 3600)  # midday: PV, counters > 0
    sim.update_registers()
    server = await asyncio.start_server(sim.serve_client, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return sim, server, port


@pytest.mark.parametrize("register_map, model, strict, expected_fc", [
    (H3_NEW, "H3-10.0-Smart", False, 4),
    (H3_NEW, "H3-10.0-Smart", True, 4),
    (H3_NEW, "UNKNOWN-MODEL", False, 4),  # unknown model string -> probing
    (H3_LEGACY, " H3-10.0-E", True, 3),
])
async def test_detect_and_read(register_map, model, strict, expected_fc):
    sim, server, port = await start_sim(register_map, model, strict=strict)
    async with server:
        driver = FoxessDriver("127.0.0.1", port, 247)
        info = await driver.connect()
        assert info.register_map == register_map.name
        assert driver.read_function == expected_fc
        snap = await driver.read()
        await driver.close()
    e = sim.energy
    assert snap.pv_power == pytest.approx(e.pv_w, abs=2)
    assert snap.house_power == pytest.approx(e.house_w, abs=2)
    assert snap.grid_power == pytest.approx(e.grid_w, abs=3)
    assert snap.battery_soc == round(e.soc)
    assert snap.totals.pv == pytest.approx(e.totals["pv"], abs=100)
    assert snap.today.grid_import == pytest.approx(e.today["grid_import"], abs=100)
    # energy balance: pv + grid + battery == house
    assert snap.pv_power + snap.grid_power + snap.battery_power == pytest.approx(snap.house_power, abs=5)


@pytest.mark.parametrize("register_map, model", [(H3_NEW, "H3-10.0-Smart"), (H3_LEGACY, " H3-10.0-E")])
async def test_write_settings_and_remote_control(register_map, model):
    sim, server, port = await start_sim(register_map, model)
    async with server:
        driver = FoxessDriver("127.0.0.1", port, 247)
        await driver.connect()
        await driver.write_work_mode(WorkMode.BACKUP)
        await driver.write_soc_limits(min_soc_on_grid=25, max_soc=95)
        settings = await driver.read_settings()
        assert settings.work_mode is WorkMode.BACKUP
        assert settings.min_soc_on_grid == 25 and settings.max_soc == 95
        assert sim.energy.work_mode is WorkMode.BACKUP

        await driver.set_remote_power(-3000, timeout_s=30)
        assert sim.energy.remote_enabled and sim.energy.remote_power_w == -3000
        assert await driver.remote_active()
        await driver.release_remote_power()
        assert not sim.energy.remote_enabled

        with pytest.raises(ValueError):
            await driver.write_soc_limits(min_soc=5)
        await driver.close()


async def test_connection_limit():
    sim, server, port = await start_sim(H3_NEW, "H3-10.0-Smart")
    sim.max_connections = 1
    async with server:
        first = FoxessDriver("127.0.0.1", port, 247)
        await first.connect()
        second = FoxessDriver("127.0.0.1", port, 247, timeout=1)
        with pytest.raises((ConnectionError, Exception)):
            await second.connect()
        await first.close()
        await second.close()
