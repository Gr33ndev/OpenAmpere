# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""RCT Power over RCT's own protocol on port 8899 (#646): display only, chosen in the setup, never written."""

import asyncio
import random
import struct
import time
from datetime import datetime

import pytest

from openampere import discovery
from openampere.diagnostics import Diagnostics
from openampere.drivers import registry
from openampere.drivers.modbus import (DetectionFailed, DeviceRefused, DeviceUnreachable, ModbusTransientError,
                                       WrongDevice, friendly_error)
from openampere.drivers.rct import protocol as p
from openampere.drivers.rct.driver import LIVE, OBJECTS, RctDriver, to_snapshot
from openampere.runtime import Runtime
from openampere.simulator import SimulatedRct
from openampere.storage import Storage
from waiting import wait_for


async def serve(sim):
    server = await asyncio.start_server(sim.serve_client, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


def sunny(sim: SimulatedRct) -> SimulatedRct:
    random.seed(646)  # the model draws clouds and load spikes from the global random: the same values in every test order
    sim.energy.step(datetime(2026, 6, 1, 13, 0), 60)
    return sim


# ---- the protocol ------------------------------------------------------------


def test_requests_as_in_rcts_document():
    """The two examples of RCT's protocol description: battery power, and the AC power whose id holds a '-' (0x2D),
    which is sent twice; the escape is not part of the CRC."""
    assert p.read_request(0x400F015B) == bytes.fromhex("2b 01 04 40 0f 01 5b 58 b4")
    assert p.read_request(0xDB2D69AE) == bytes.fromhex("2b 01 04 db 2d 2d 69 ae 55 ab")


def test_the_crc_pads_an_odd_number_of_bytes():
    assert p.crc16(bytes.fromhex("02 05 3e 21 5f 90 55")) == p.crc16(bytes.fromhex("02 05 3e 21 5f 90 55 00"))


def test_frames_are_found_in_a_stream_cut_anywhere():
    stream = (b"\x00" + p.frame(p.RESPONSE, 0xDB2D69AE, p.encode(p.FLOAT, 1234.5))
              + p.frame(p.LONG_RESPONSE, 0x7924ABD9, p.encode(p.STRING, "a+b-c")))
    reader, found = p.FrameReader(), []
    for byte in stream:  # one byte at a time
        found += reader.feed(bytes([byte]))
    assert found == [(p.RESPONSE, 0xDB2D69AE, struct.pack(">f", 1234.5)), (p.LONG_RESPONSE, 0x7924ABD9, b"a+b-c\x00")]


def test_a_wrong_crc_a_plant_frame_and_a_cut_off_frame_are_dropped():
    good = p.frame(p.RESPONSE, 0x959930BF, p.encode(p.FLOAT, 0.5))
    bad = bytearray(good)
    bad[-1] ^= 0xFF
    plant = p.frame(p.RESPONSE | p.PLANT, 0x12345678, b"\x00\x00\x00\x00")  # a further device of a plant
    cut = good[:5]  # a new start byte follows before it is complete
    found = p.FrameReader().feed(bytes(bad) + plant + cut + good)
    assert found == [(p.RESPONSE, 0x959930BF, struct.pack(">f", 0.5))]


@pytest.mark.parametrize(("kind", "data", "value"), [
    (p.FLOAT, struct.pack(">f", 2.5), 2.5), (p.FLOAT, bytes.fromhex("7fc00000"), None),
    (p.FLOAT, bytes.fromhex("7f800000"), None), (p.FLOAT, b"\x00", None), (p.UINT8, b"\x04", 4),
    (p.INT32, b"\xff\xff\xff\xff", -1), (p.UINT32, b"\xff\xff\xff\xff", 0xFFFFFFFF),
    (p.STRING, b"PS 6.0\x00junk", "PS 6.0"), (p.STRING, b"\x00", None)])
def test_values_by_type(kind, data, value):
    assert p.decode(kind, data) == value


def test_unknown_types_are_refused():
    with pytest.raises(ValueError):
        p.decode("t_enum", b"\x00")
    with pytest.raises(ValueError):
        p.encode("t_enum", 0)


def test_long_writes_have_a_two_byte_length():
    sent = p.frame(p.LONG_WRITE, 0x7924ABD9, b"x" * 300)
    assert sent[1] == p.LONG_WRITE and int.from_bytes(sent[2:4], "big") == 304


# ---- the driver --------------------------------------------------------------


async def test_read_with_its_signs_and_units():
    sim = sunny(SimulatedRct())
    server, port = await serve(sim)
    async with server:
        driver = RctDriver("127.0.0.1", port)
        try:
            info = await driver.connect()
            snap = (await driver.read()).sanitize()
            settings = await driver.read_settings()
        finally:
            await driver.close()
    e = sim.energy
    assert info.manufacturer == "RCT Power" and info.serial == "SIMRCT00000001" and info.firmware == "SIM-1.0"
    assert info.driver == "rct" and not info.supports_control
    assert snap.pv_power == pytest.approx(e.pv_w, abs=0.2) and len(snap.pv_inputs) == 2
    assert snap.grid_power == pytest.approx(e.grid_w, abs=0.1)  # + = import, as the snapshot
    assert snap.battery_power == pytest.approx(e.battery_w, abs=0.1)  # RCT: + = discharging, as the snapshot
    assert snap.house_power == pytest.approx(e.house_w, abs=0.1)
    assert snap.battery_soc == pytest.approx(e.soc, abs=0.1) and snap.battery_soh == 97.0  # shares become %
    assert snap.temperatures == {"inverter": 38.0} and snap.battery_temperature == 24.0
    assert snap.totals.pv == pytest.approx(1_500_000 + e.totals["pv"], abs=1)
    assert snap.totals.grid_export == pytest.approx(800_000 + e.totals["grid_export"], abs=1)
    assert snap.totals.consumption is not None
    assert settings.min_soc == e.min_soc and settings.max_soc == e.max_soc


async def test_one_connection_for_many_reads():
    sim = sunny(SimulatedRct())
    connections = []

    async def counted(reader, writer):
        connections.append(1)
        await sim.serve_client(reader, writer)
    server = await asyncio.start_server(counted, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        driver = RctDriver("127.0.0.1", port)
        try:
            for _ in range(3):
                await driver.read()
        finally:
            await driver.close()
    assert len(connections) == 1


async def test_an_object_without_answer_stays_empty(monkeypatch):
    """A device without battery or with older firmware does not know every object: it does not answer them, the read
    goes on without them."""
    sim = sunny(SimulatedRct())
    known = sim.answer
    monkeypatch.setattr(sim, "answer", lambda oid: b"" if oid == OBJECTS["battery_soh"][0] else known(oid))
    monkeypatch.setattr("openampere.drivers.rct.driver.ANSWER_WAIT_S", 0.3)
    server, port = await serve(sim)
    async with server:
        driver = RctDriver("127.0.0.1", port)
        try:
            snap = await driver.read()
        finally:
            await driver.close()
    assert snap.battery_soh is None and snap.battery_soc is not None
    assert "battery_soh" not in driver.last_values


async def test_a_device_that_answers_nothing_is_no_rct(monkeypatch):
    monkeypatch.setattr("openampere.drivers.rct.driver.DETECT_WAIT_S", 0.3)
    server, port = await serve(SimulatedRct(silent=True))
    async with server:
        with pytest.raises(DetectionFailed, match="kein RCT-Wechselrichter"):
            await registry.detect("127.0.0.1", port, only="rct")


async def test_a_known_inverter_silent_on_reconnect_is_a_failed_read(monkeypatch):
    """After a restart the network board may take the connection but answer nothing for a while: for the owner's
    inverter that is a failed read, not "no RCT inverter at this address"."""
    monkeypatch.setattr("openampere.drivers.rct.driver.DETECT_WAIT_S", 0.3)
    sim = sunny(SimulatedRct())
    server, port = await serve(sim)
    async with server:
        driver = RctDriver("127.0.0.1", port)
        try:
            await driver.connect()
            await driver.disconnect()
            sim.silent = True
            with pytest.raises(ModbusTransientError) as caught:
                await driver.connect()
            assert not isinstance(caught.value, DetectionFailed)
            fresh = RctDriver("127.0.0.1", port)
            fresh.expected_serial = "SIMRCT00000001"  # as the collector sets it after a restart of OpenAmpere
            with pytest.raises(ModbusTransientError):
                await fresh.connect()
            await fresh.close()
            sim.silent = False
            assert (await driver.connect()).serial == "SIMRCT00000001"
        finally:
            await driver.close()


async def test_a_read_without_answer_is_a_failed_read(monkeypatch):
    monkeypatch.setattr("openampere.drivers.rct.driver.ANSWER_WAIT_S", 0.2)
    sim = sunny(SimulatedRct())
    server, port = await serve(sim)
    async with server:
        driver = RctDriver("127.0.0.1", port)
        try:
            await driver.connect()
            sim.silent = True  # e.g. while the inverter restarts
            with pytest.raises(ModbusTransientError) as caught:
                await driver.read()
            assert "RCT-Wechselrichter antwortet gerade nicht" in friendly_error(caught.value)
            sim.silent = False
            assert (await driver.read()).battery_soc is not None  # connects again by itself
        finally:
            await driver.close()


async def test_a_closed_connection_is_a_failed_read_and_opened_again():
    sim = sunny(SimulatedRct())
    server, port = await serve(sim)
    async with server:
        driver = RctDriver("127.0.0.1", port)
        try:
            await driver.connect()
            driver._writer.transport.abort()  # noqa: SLF001 - the connection breaks
            with pytest.raises(ModbusTransientError):
                await driver.read()
            assert (await driver.read()).pv_power is not None
        finally:
            await driver.close()


async def test_another_inverter_at_the_address_is_not_read():
    """A new connection must reach the owner's inverter (#567)."""
    server, port = await serve(sunny(SimulatedRct(serial="OTHER0000000001")))
    async with server:
        driver = RctDriver("127.0.0.1", port)
        driver.expected_serial = "SIMRCT00000001"
        try:
            with pytest.raises(WrongDevice):
                await driver.connect()
            assert driver.info is None and driver._writer is None  # noqa: SLF001
        finally:
            await driver.close()


async def test_not_tried_by_automatic_detection(monkeypatch):
    """Only when the owner chooses it: it does not answer on the Modbus port."""
    monkeypatch.setattr(registry, "DRIVERS", {"rct": RctDriver})
    with pytest.raises(DetectionFailed):
        await registry.detect("127.0.0.1", 9, only=None)


async def test_nothing_listening_is_unreachable():
    server = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    server.close()
    await server.wait_closed()
    with pytest.raises(DeviceUnreachable) as caught:
        await registry.detect("127.0.0.1", port, only="rct")
    assert "RCT-Wechselrichter" in friendly_error(caught.value) and "Modbus" not in friendly_error(caught.value)


async def test_the_setup_test_reads_it(tmp_path):
    server, port = await serve(sunny(SimulatedRct()))
    async with server:
        runtime = Runtime({}, Storage(tmp_path / "t.db"))
        result = await discovery.test_connection(runtime, "127.0.0.1", port, 0, driver="rct")
    assert result["ok"], result


async def test_the_collector_reads_it(tmp_path):
    server, port = await serve(sunny(SimulatedRct()))
    async with server:
        runtime = Runtime({}, Storage(tmp_path / "t.db"))
        try:
            await runtime.update_settings({"inverter.host": "127.0.0.1", "inverter.port": port,
                                           "inverter.driver": "rct", "inverter.poll_interval": 2})
            await wait_for(lambda: runtime.collector.latest, "first reading")
            assert runtime.collector.latest is not None
            check = Diagnostics(runtime)._rct_values()
            assert check.status == "info" and check.details["missing"] == []
            assert "inverter_sn" not in check.details["values"]
            assert not any(name.endswith("_total") for name in check.details["values"])
        finally:
            await runtime.collector.stop()


async def test_the_report_without_a_read_yet_skips_the_values(tmp_path):
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    runtime.collector.driver = RctDriver("192.0.2.1")
    assert Diagnostics(runtime)._rct_values().status == "skipped"


async def test_old_values_and_missing_objects_in_the_report(tmp_path):
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    driver = RctDriver("192.0.2.1")
    driver.last_values, driver.last_values_at = {"grid_power": 512.0, "battery_soc": 0.5}, time.time() - 600
    runtime.collector.driver = driver
    check = Diagnostics(runtime)._rct_values()
    assert check.status == "warn" and check.summary.startswith("Letzte Werte von vor 10 min")
    assert check.details["values"]["grid_power"] == 500.0 and len(check.details["missing"]) == len(LIVE) - 2


def test_the_modbus_default_port_stands_for_its_own_port():
    assert RctDriver("192.0.2.1", 502)._port == 8899  # noqa: SLF001
    assert RctDriver("192.0.2.1", 0)._port == 8899  # noqa: SLF001
    assert RctDriver("192.0.2.1", 9000)._port == 9000  # noqa: SLF001


def test_missing_values_stay_missing():
    snap = to_snapshot({"dc_a_power": 1000.0, "battery_soc": 0.4}, 0)
    assert snap.pv_power is None and snap.pv_inputs[0].power == 1000.0  # one input alone is not the PV power
    assert snap.battery_soc == 40.0 and snap.grid_power is None and snap.totals.pv is None


async def test_never_written():
    driver = RctDriver("192.0.2.1")
    for write in (lambda: driver.write_soc_limits(min_soc=20), lambda: driver.set_remote_power(-1000, 180),
                  lambda: driver.write_export_limit(5000), lambda: driver.write_work_mode(None)):
        with pytest.raises(DeviceRefused, match="nur die Werte an"):
            await write()
    assert await driver.release_remote_power() is False and await driver.remote_active() is False
    assert (await driver.read_export_limit()).supported is False


async def test_the_device_query_refuses_it(tmp_path, monkeypatch):
    """The device query reads Modbus registers; at the RCT inverter it says why it cannot."""
    from openampere.probe import Probe, ProbeError
    probe = Probe(Runtime({}, Storage(tmp_path / "t.db")))

    async def connected(host, port):
        return RctDriver(host)
    monkeypatch.setattr(probe, "_inverter_driver", connected)
    with pytest.raises(ProbeError, match="Protokoll von RCT"):
        await probe._read("192.0.2.1", 502, 1, 3, [])  # noqa: SLF001
