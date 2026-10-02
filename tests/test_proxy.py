"""Operation behind a Modbus TCP proxy: forwarding, flaky upstream, slow answers, read-only proxies."""

import asyncio
from datetime import datetime

import pytest

from openampere.collector import Collector
from openampere.drivers.foxess.driver import FoxessDriver, ModbusIllegalError, ModbusTransientError
from openampere.drivers.foxess.registers import H3_NEW
from openampere.simulator import SimulatedInverter
from openampere.storage import Storage


async def start_sim(**kwargs):
    sim = SimulatedInverter(H3_NEW, "H3-10.0-Smart", "SN1", strict_function=False, max_connections=5, **kwargs)
    sim.energy.step(datetime(2026, 6, 21, 13, 0), 600)
    sim.update_registers()
    server = await asyncio.start_server(sim.serve_client, "127.0.0.1", 0)
    return sim, server, server.sockets[0].getsockname()[1]


async def start_proxy(upstream_port: int):
    """Minimal transparent TCP proxy, like a Modbus TCP proxy in front of the inverter."""
    async def handle(reader, writer):
        up_reader, up_writer = await asyncio.open_connection("127.0.0.1", upstream_port)

        async def pipe(src, dst):
            try:
                while data := await src.read(4096):
                    dst.write(data)
                    await dst.drain()
            finally:
                dst.close()

        await asyncio.gather(pipe(reader, up_writer), pipe(up_reader, writer), return_exceptions=True)

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


async def test_reads_through_proxy():
    sim, server, port = await start_sim()
    proxy, proxy_port = await start_proxy(port)
    async with server, proxy:
        driver = FoxessDriver("127.0.0.1", proxy_port, 247)
        info = await driver.connect()
        snap = await driver.read()
        await driver.close()
    assert info.model == "H3-10.0-Smart"
    assert snap.pv_power == pytest.approx(sim.energy.pv_w, abs=2)


async def test_flaky_upstream_does_not_blacklist_registers():
    sim, server, port = await start_sim()
    async with server:
        driver = FoxessDriver("127.0.0.1", port, 247)
        await driver.connect()
        sim.fault_rate = 1.0  # proxy cannot reach the inverter
        with pytest.raises(ModbusTransientError):
            await driver.read()
        assert driver._bad_addresses == set()
        sim.fault_rate = 0.0  # inverter reachable again: everything is read normally
        snap = await driver.read()
        await driver.close()
    assert snap.battery_soc is not None and snap.totals.pv is not None


async def test_detection_is_not_fooled_by_flaky_proxy():
    sim, server, port = await start_sim(fault_rate=1.0)
    async with server:
        driver = FoxessDriver("127.0.0.1", port, 247)
        try:
            with pytest.raises(ConnectionError):
                await driver.connect()  # must not guess a register map from timeouts
            await asyncio.sleep(0.2)
            assert sim.connections == 0  # failed detection released its connection
            sim.fault_rate = 0.0
            info = await driver.connect()
        finally:
            await driver.close()
    assert info.register_map == "foxess_h3_new" and driver.read_function == 4


async def test_slow_proxy_with_longer_timeout():
    sim, server, port = await start_sim(latency_s=1.5)
    async with server:
        fast = FoxessDriver("127.0.0.1", port, 247, timeout=0.5)
        patient = FoxessDriver("127.0.0.1", port, 247, timeout=4)
        try:
            with pytest.raises(ConnectionError):
                await fast.connect()
            assert (await patient.connect()).model == "H3-10.0-Smart"
        finally:
            await fast.close()
            await patient.close()


async def test_collector_rides_out_single_hiccups(tmp_path):
    sim, server, port = await start_sim()
    async with server:
        collector = Collector(FoxessDriver("127.0.0.1", port, 247), Storage(tmp_path / "t.db"), 0.2, 30)
        collector.start()
        for _ in range(50):
            if collector.latest:
                break
            await asyncio.sleep(0.1)
        sim.fault_rate = 1.0
        await asyncio.sleep(0.45)  # ~2 failed polls: below the tolerance
        assert collector.connected
        sim.fault_rate = 0.0
        await asyncio.sleep(0.6)
        assert collector.connected and collector.last_error is None
        await collector.stop()


async def test_read_only_proxy_rejects_writes():
    sim, server, port = await start_sim(read_only=True)
    async with server:
        driver = FoxessDriver("127.0.0.1", port, 247)
        await driver.connect()
        with pytest.raises(ModbusIllegalError):
            await driver.write_soc_limits(min_soc_on_grid=30)
        await driver.close()
    assert sim.energy.min_soc_on_grid == 10


async def test_stop_is_not_blocked_by_retries(tmp_path):
    sim, server, port = await start_sim(latency_s=0.3)
    async with server:
        collector = Collector(FoxessDriver("127.0.0.1", port, 247), Storage(tmp_path / "t.db"), 0.2, 30)
        collector.start()
        for _ in range(60):
            if collector.latest:
                break
            await asyncio.sleep(0.1)
        sim.fault_rate = 1.0  # collector is now busy retrying
        await asyncio.sleep(0.5)
        loop = asyncio.get_running_loop()
        started = loop.time()
        await collector.stop()
        assert loop.time() - started < 3
