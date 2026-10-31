# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Senec.Home V3 / V2.1 over lala.cgi (#521): display only, chosen in the setup, never written."""

import asyncio
import json
from datetime import datetime

import pytest

from openampere.diagnostics import Diagnostics
from openampere.drivers import registry
from openampere.drivers.modbus import DetectionFailed, DeviceRefused, DeviceUnreachable
from openampere.drivers.senec.driver import LIVE, MAX_REQUEST_CHARS, SenecDriver, parse_value
from openampere.runtime import Runtime
from openampere.simulator import SimulatedSenec
from openampere.storage import Storage


async def serve(sim):
    server = await asyncio.start_server(sim.serve_client, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


def sunny(sim: SimulatedSenec) -> SimulatedSenec:
    sim.energy.step(datetime(2026, 6, 1, 13, 0), 60)
    return sim


@pytest.mark.parametrize(("raw", "value"), [("fl_42C80000", 100.0), ("fl_C2C80000", -100.0), ("u8_13", 19),
                                            ("u1_0001", 1), ("i3_FFFFFFFF", -1), ("i1_7FFF", 32767),
                                            ("st_V3", "V3"), ("VARIABLE_NOT_FOUND", None), ("fl_XYZ", None)])
def test_typed_hex_values(raw, value):
    assert parse_value(raw) == value


def test_a_request_stays_below_the_size_the_device_takes():
    assert len(json.dumps(LIVE, separators=(",", ":"))) < MAX_REQUEST_CHARS


async def test_read_with_its_signs_and_the_session_cookie():
    sim = sunny(SimulatedSenec(system_type=20))
    server, port = await serve(sim)
    async with server:
        info = await registry.detect("127.0.0.1", port, only="senec")
        driver = registry.create("senec", "127.0.0.1", port, 0, timeout=3)
        try:
            await driver.connect()
            snap = (await driver.read()).sanitize()
        finally:
            await driver.close()
    assert (info.driver, info.manufacturer, info.model, info.supports_control) == ("senec", "Senec", "Home V3 hybrid LFP", False)
    e = sim.energy
    assert e.battery_w < 0 and e.grid_w < 0  # charging and exporting: every sign matters
    assert snap.pv_power == pytest.approx(e.pv_w, abs=0.1)
    assert snap.battery_power == pytest.approx(e.battery_w, abs=0.1)  # + = discharging (Senec: + = charging)
    assert snap.grid_power == pytest.approx(e.grid_w, abs=0.1)
    assert snap.battery_soc == pytest.approx(e.soc, abs=0.1) and snap.inverter_state == 14
    assert snap.totals.pv is None  # no energy counters
    # the first request asks for FACTORY and gets the cookie, every later one sends it back
    assert "FACTORY" in sim.requests[0][1]
    assert all(cookie for _path, form, cookie in sim.requests[1:] if "ENERGY" in form)


async def test_an_unknown_system_type_still_reads(caplog):
    sim = sunny(SimulatedSenec(system_type=99))
    server, port = await serve(sim)
    async with server:
        info = await registry.detect("127.0.0.1", port, only="senec")
    assert info.model == "Typ 99" and "unknown Senec system type" in caplog.text


async def test_not_tried_by_automatic_detection(monkeypatch):
    """Only when the owner chooses it: it does not answer on the Modbus port."""
    monkeypatch.setattr(registry, "DRIVERS", {"senec": SenecDriver})
    with pytest.raises(DetectionFailed):
        await registry.detect("127.0.0.1", 9, only=None)


async def test_another_web_server_is_no_senec():
    async def not_found(reader, writer):
        await reader.read(1024)
        writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        await writer.drain()
        writer.close()
    server = await asyncio.start_server(not_found, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        with pytest.raises(DetectionFailed):
            await registry.detect("127.0.0.1", port, only="senec")


async def test_nothing_listening_is_unreachable():
    server = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    server.close()
    await server.wait_closed()
    with pytest.raises(DeviceUnreachable):
        await registry.detect("127.0.0.1", port, only="senec")


def test_the_modbus_default_port_stands_for_its_own_ports():
    assert SenecDriver("192.0.2.1", 502)._ports == (443, 80)
    assert SenecDriver("192.0.2.1", 8443)._ports == (8443,)


async def test_never_written():
    driver = SenecDriver("192.0.2.1")
    for write in (lambda: driver.write_soc_limits(min_soc=20), lambda: driver.set_remote_power(-1000, 180),
                  lambda: driver.write_export_limit(5000)):
        with pytest.raises(DeviceRefused, match="nur die Werte an"):
            await write()
    assert await driver.release_remote_power() is False and await driver.remote_active() is False


async def test_polled_at_most_every_30_seconds_and_reported_without_the_device_id(tmp_path):
    sim = sunny(SimulatedSenec())
    server, port = await serve(sim)
    async with server:
        runtime = Runtime({}, Storage(tmp_path / "t.db"))
        try:
            await runtime.update_settings({"inverter.host": "127.0.0.1", "inverter.port": port,
                                           "inverter.driver": "senec", "inverter.poll_interval": 2})
            for _ in range(150):
                if runtime.collector.latest:
                    break
                await asyncio.sleep(0.1)
            assert runtime.collector.device.driver == "senec"
            assert runtime.collector.poll_interval() == 30.0  # a Senec restarts when asked too often
            report = await Diagnostics(runtime).run()
        finally:
            await runtime.collector.stop()
    check = {c["id"]: c for c in report["checks"]}["senec_values"]
    assert check["details"]["scheme"] == "http" and check["details"]["values"]["GUI_GRID_POW"] < 0
    assert str(0x12345678) not in json.dumps(report["checks"])
    assert "connections" not in {c["id"] for c in report["checks"] if c["status"] != "skipped"}
