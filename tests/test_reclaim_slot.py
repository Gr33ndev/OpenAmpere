# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Connecting again from the local port of a lost connection, so the inverter drops the half-open session that still
takes one of its few slots (#644)."""

import asyncio
import socket

from openampere.collector import Collector
from openampere.config import Config
from openampere.diagnostics import report_markdown
from openampere.drivers import registry
from openampere.runtime import make_driver
from openampere.simulator import SajSimulatedInverter
from openampere.storage import Storage

from test_detection import serve


async def serve_recording(sim):
    """The simulator, noting the client port of every connection."""
    ports: list[int] = []
    handler = sim.serve_client

    async def recording(reader, writer):
        ports.append(writer.get_extra_info("peername")[1])
        await handler(reader, writer)
    server = await asyncio.start_server(recording, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1], ports


async def driver_on(port, *, reclaim=True):
    driver = registry.create("saj", "127.0.0.1", port, 1, timeout=3)
    driver.reclaim_slot = reclaim
    await driver.connect()
    return driver


async def test_connects_again_from_the_port_of_the_lost_connection():
    sim = SajSimulatedInverter(max_connections=3)
    server, port, ports = await serve_recording(sim)
    async with server:
        driver = await driver_on(port)
        try:
            await driver.read()
            await driver.close_lost()
            await driver.read()  # connects again
            assert len(ports) == 2 and ports[0] == ports[1]
            await driver.read()  # the device answered: nothing kept for a later connect
            assert driver._lost_address is None
        finally:
            await driver.close()


async def test_off_by_default_the_system_picks_the_port():
    sim = SajSimulatedInverter(max_connections=3)
    server, port, ports = await serve_recording(sim)
    async with server:
        driver = await driver_on(port, reclaim=False)
        try:
            await driver.read()
            await driver.close_lost()
            assert driver._lost_address is None
            await driver.read()
            assert driver._client.comm_params.source_address is None
        finally:
            await driver.close()


async def test_a_taken_port_falls_back_to_a_free_one():
    sim = SajSimulatedInverter(max_connections=3)
    server, port, ports = await serve_recording(sim)
    async with server:
        driver = await driver_on(port)
        try:
            await driver.read()
            await driver.close_lost()
            with socket.socket() as squatter:  # another program took the port meanwhile
                squatter.bind(driver._lost_address)
                await driver.read()
            assert len(ports) == 2 and ports[1] != ports[0]
            assert driver._lost_address is None
        finally:
            await driver.close()


async def test_nothing_is_kept_from_a_connection_the_device_never_answered_on():
    sim = SajSimulatedInverter(max_connections=3)
    server, port, _ = await serve_recording(sim)
    async with server:
        driver = await driver_on(port)
        try:
            await driver.close()
            await driver._ensure_connected()  # connected, no request yet
            await driver.close_lost()
            assert driver._lost_address is None
        finally:
            await driver.close()


async def test_a_request_in_flight_is_never_cut_off():
    sim = SajSimulatedInverter(max_connections=3)
    server, port, _ = await serve_recording(sim)
    async with server:
        driver = await driver_on(port)
        try:
            await driver.read()
            await driver._lock.acquire()  # stands for a write that is still running
            closing = asyncio.ensure_future(driver.close_lost())
            await asyncio.sleep(0.2)
            assert not closing.done() and driver._client is not None
            driver._lock.release()
            await closing
            assert driver._client is None and driver._lost_address is not None
        finally:
            await driver.close()


async def test_the_automatic_driver_hands_the_setting_on():
    sim = SajSimulatedInverter(max_connections=3)
    server, port = await serve(sim)
    async with server:
        auto = registry.AutoDriver("127.0.0.1", port)
        auto.reclaim_slot = True
        try:
            await auto.connect()
            assert auto._driver.reclaim_slot
            await auto.read()
            await auto.close_lost()
            assert auto._driver._lost_address is not None
        finally:
            await auto.close()


def test_the_setting_reaches_the_driver():
    config = Config()
    config.inverter.host = "192.0.2.5"
    assert make_driver(config).reclaim_slot is False
    config.inverter.reclaim_slot = True
    assert make_driver(config).reclaim_slot is True
    config.inverter.driver = "saj"
    assert make_driver(config).reclaim_slot is True
    config.inverter.driver = "senec"  # no Modbus: nothing to reclaim
    assert not hasattr(make_driver(config), "reclaim_slot")


class LosingDriver:
    """Connects, then every reading fails."""

    def __init__(self):
        self.calls: list[str] = []

    async def connect(self):
        raise ConnectionError("gone")

    async def close(self):
        self.calls.append("close")

    async def close_lost(self):
        self.calls.append("close_lost")


class PlainDriver(LosingDriver):
    close_lost = None  # a driver without Modbus


async def test_the_collector_closes_a_lost_connection_through_the_driver(tmp_path):
    for cls, expected in ((LosingDriver, "close_lost"), (PlainDriver, "close")):
        driver = cls()
        collector = Collector(driver, Storage(tmp_path / f"{cls.__name__}.db"), 10, 30)
        collector.start()
        try:
            for _ in range(50):
                if driver.calls:
                    break
                await asyncio.sleep(0.01)
            assert driver.calls[0] == expected
        finally:
            await collector.stop()


def test_the_report_says_when_the_port_is_reused():
    report = {"device": {}, "connection": {"mode": "persistent", "timeout_s": 3, "reclaim_slot": True}, "checks": []}
    assert "Port der verlorenen Verbindung wiederverwenden" in report_markdown(report)
    report["connection"]["reclaim_slot"] = False
    assert "wiederverwenden" not in report_markdown(report)
    del report["connection"]["reclaim_slot"]  # a report stored before #644
    assert "wiederverwenden" not in report_markdown(report)
