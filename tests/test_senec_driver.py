# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Senec.Home V3 / V2.1 over lala.cgi (#521): display only, chosen in the setup, never written."""

import asyncio
import json
from datetime import datetime

import pytest

from openampere import discovery
from openampere.diagnostics import Diagnostics
from openampere.drivers import registry
from openampere.drivers.modbus import (DetectionFailed, DeviceRefused, DeviceUnreachable, ModbusTransientError,
                                       friendly_error)
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
                                            ("st_V3", "V3"), ("VARIABLE_NOT_FOUND", None), ("fl_XYZ", None),
                                            ("fl_42C8", None), ("fl_", None), ("fl_7FC00000", None),
                                            ("fl_7F800000", None), ("fl_FF800000", None)])
def test_typed_hex_values(raw, value):
    assert parse_value(raw) == value


async def test_no_status_line_is_no_senec_and_a_cut_off_answer_a_failed_read():
    """Plain HTTP at a TLS port gets no status line (BadStatusLine); an answer cut off while the device restarts
    (IncompleteRead) is a failed read, not a hard error."""
    async def garbage(reader, writer):
        await reader.read(1024)
        writer.write(b"\x15\x03\x03\x00\x02\x02\x46")  # a TLS alert instead of an HTTP answer
        await writer.drain()
        writer.close()

    async def cut_off(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 500\r\nConnection: close\r\n\r\n{\"ENERGY\"")
        await writer.drain()
        writer.close()
    for handler, expected in ((garbage, DetectionFailed), (cut_off, ModbusTransientError)):
        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        async with server:
            if expected is DetectionFailed:
                with pytest.raises(DetectionFailed):
                    await registry.detect("127.0.0.1", port, only="senec")
            else:
                driver = SenecDriver("127.0.0.1", port, timeout=1)
                driver._url = f"http://127.0.0.1:{port}/lala.cgi"
                with pytest.raises(ModbusTransientError):
                    await driver.read()


async def test_an_answer_that_is_no_json_object_is_a_failed_read():
    """A firmware that answers a list or null: a read without values, not a crash of the collector."""
    async def odd(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n[]")
        await writer.drain()
        writer.close()
    server = await asyncio.start_server(odd, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        driver = SenecDriver("127.0.0.1", port, timeout=1)
        driver._url = f"http://127.0.0.1:{port}/lala.cgi"
        with pytest.raises(ModbusTransientError):
            await driver.read()


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


async def test_found_again_after_the_device_restarted():
    """A restarted Senec no longer takes the old session cookie: after the collector closes the driver, it asks for
    FACTORY again and reads on."""
    sim = sunny(SimulatedSenec(require_session=True))
    server, port = await serve(sim)
    async with server:
        driver = SenecDriver("127.0.0.1", port, timeout=1, read_attempts=1)
        try:
            await driver.connect()
            assert (await driver.read()).battery_soc is not None
            sim.session = "sim2"  # the device restarted
            with pytest.raises(ModbusTransientError) as caught:
                await driver.read()
            # what the owner reads: the storage system, no Modbus proxy
            assert "Senec-Speicher" in friendly_error(caught.value) and "Modbus" not in friendly_error(caught.value)
            await driver.close()  # what the collector does after repeated errors
            await driver.connect()
            assert (await driver.read()).battery_soc is not None
        finally:
            await driver.close()


async def test_a_failed_read_is_one_request_and_never_sooner_than_30_seconds_again(tmp_path, monkeypatch):
    """A busy or restarting Senec must not be asked every few seconds: a failed read is one request (no quick
    retries), and the collector waits at least MIN_POLL_S before the next try and before a reconnect."""
    from openampere import collector as collector_module
    sim = sunny(SimulatedSenec(require_session=True))
    server, port = await serve(sim)
    real_sleep, sleeps = asyncio.sleep, []

    async def recorded(delay, *args):
        sleeps.append(delay)
        await real_sleep(0.01)
    async with server:
        runtime = Runtime({}, Storage(tmp_path / "t.db"))
        monkeypatch.setattr(collector_module.asyncio, "sleep", recorded)  # every wait of the collector, shortened
        try:
            await runtime.update_settings({"inverter.host": "127.0.0.1", "inverter.port": port,
                                           "inverter.driver": "senec", "inverter.poll_interval": 2})
            for _ in range(150):
                if runtime.collector.latest:
                    break
                await real_sleep(0.1)
            sleeps.clear()
            sim.session = "sim2"  # the device restarted: reads fail until the reconnect asks for FACTORY again
            before = len(sim.requests)
            for _ in range(300):
                if len(sleeps) >= 6:
                    break
                await real_sleep(0.02)
        finally:
            await runtime.collector.stop()
    # the normal wait is 30 s minus the time the read took: 30 s from one request to the next
    assert len(sleeps) >= 6 and min(sleeps) >= 29.5, sleeps
    reads = [form for _path, form, _cookie in sim.requests[before:] if "ENERGY" in form]
    rounds = len(sleeps)
    assert len(reads) <= rounds + 2  # one read per round, no retries a second apart


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
        with pytest.raises(DetectionFailed, match="nicht wie ein Senec-Speicher"):
            await registry.detect("127.0.0.1", port, only="senec")


async def test_nothing_listening_is_unreachable():
    server = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    server.close()
    await server.wait_closed()
    with pytest.raises(DeviceUnreachable) as caught:
        await registry.detect("127.0.0.1", port, only="senec")
    # no word about Modbus or its port: a Senec has none
    assert "Weboberfläche des Senec-Speichers" in friendly_error(caught.value) and "Modbus" not in friendly_error(caught.value)


async def test_the_setup_test_names_the_web_interface(tmp_path):
    server = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    server.close()
    await server.wait_closed()
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    result = await discovery.test_connection(runtime, "127.0.0.1", port, 0, driver="senec")
    assert not result["ok"] and "Senec" in result["error"] and "Modbus" not in result["error"]


async def test_the_report_without_a_read_yet_skips_the_values(tmp_path):
    from openampere.diagnostics import Check
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    runtime.collector.driver = SenecDriver("192.0.2.1")
    check = await Diagnostics(runtime)._senec_values()
    assert isinstance(check, Check) and check.status == "skipped"


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
            requests_before_report = len(sim.requests)
            report = await Diagnostics(runtime).run()
        finally:
            await runtime.collector.stop()
    check = {c["id"]: c for c in report["checks"]}["senec_values"]
    assert check["details"]["scheme"] == "http" and check["details"]["values"]["GUI_GRID_POW"] < 0
    assert str(0x12345678) not in json.dumps(report["checks"])
    # the check shows the values of the last read: the report sends no request of its own
    assert not any("ENERGY" in form for _path, form, _cookie in sim.requests[requests_before_report:])
    assert "connections" not in {c["id"] for c in report["checks"] if c["status"] != "skipped"}
