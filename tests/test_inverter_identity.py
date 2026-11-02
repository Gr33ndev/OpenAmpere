# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Another device at the inverter's address is neither read nor written (#567): a tablet in another network, or the
router gave the address to another device."""

import asyncio
from datetime import datetime

import pytest

from conftest import login
from openampere.collector import Collector
from openampere.drivers.base import WorkMode
from openampere.drivers.foxess.driver import FoxessDriver
from openampere.drivers.foxess.registers import H3_LEGACY, H3_NEW
from openampere.drivers.modbus import WrongDevice
from openampere.drivers.registry import AutoDriver
from openampere.drivers.saj.driver import SajDriver
from openampere.simulator import SajSimulatedInverter, SimulatedInverter
from openampere.storage import Storage

OWN, OTHER = "60BH000000000001", "60BH000000000002"


async def start(sim):
    sim.energy.step(datetime(2026, 6, 21, 13, 0), 3600)
    sim.update_registers()
    server = await asyncio.start_server(sim.serve_client, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


def foxess(serial, register_map=H3_NEW):
    return SimulatedInverter(register_map, "H3-10.0-Smart" if register_map is H3_NEW else " H3-10.0-E", serial,
                             strict_function=False, max_connections=2)


async def test_nothing_is_written_to_another_device_after_a_new_connection():
    own, other = foxess(OWN), foxess(OTHER)
    own_server, own_port = await start(own)
    other_server, other_port = await start(other)
    async with own_server, other_server:
        driver = FoxessDriver("127.0.0.1", own_port, 247)
        try:
            assert (await driver.connect()).serial == OWN
            await driver.set_remote_power(-3000, timeout_s=30)
            # the same address now reaches another device: the tablet is in another network
            await driver.close()
            driver._port = other_port
            with pytest.raises(WrongDevice):
                await driver.release_remote_power()  # also the "safe" switch-off
            with pytest.raises(WrongDevice):
                await driver.write_work_mode(WorkMode.BACKUP)
            assert other.energy.work_mode is not WorkMode.BACKUP and not other.energy.remote_enabled
            with pytest.raises(WrongDevice):
                await driver.read()
            # back home: detected again and switched off there
            driver._port = own_port
            assert (await driver.connect()).serial == OWN
            await driver.release_remote_power()
            assert not own.energy.remote_enabled
        finally:
            await driver.close()


async def test_a_device_detected_first_must_be_the_known_one():
    """The tablet starts in another network: the serial number OpenAmpere knows counts, not the first one it sees."""
    server, port = await start(foxess(OTHER))
    async with server:
        for driver in (FoxessDriver("127.0.0.1", port, 247), AutoDriver("127.0.0.1", port, 247)):
            driver.expected_serial = OWN
            try:
                with pytest.raises(WrongDevice) as err:
                    await driver.connect()
                assert err.value.found == OTHER and err.value.expected == OWN
            finally:
                await driver.close()


async def test_saj_is_checked_as_well():
    own, other = SajSimulatedInverter(serial="HST2103SIM000001"), SajSimulatedInverter(serial="HST2103SIM000002")
    own_server, own_port = await start(own)
    other_server, other_port = await start(other)
    async with own_server, other_server:
        driver = SajDriver("127.0.0.1", own_port, 1)
        try:
            await driver.connect()
            await driver.close()
            driver._port = other_port
            with pytest.raises(WrongDevice):
                await driver.write_backup_soc(30)
        finally:
            await driver.close()


async def test_a_device_without_serial_number_is_not_checked():
    """The FoxESS legacy map has no serial number: nothing to compare, as before."""
    first, second = foxess(OWN, H3_LEGACY), foxess(OTHER, H3_LEGACY)
    first_server, first_port = await start(first)
    second_server, second_port = await start(second)
    async with first_server, second_server:
        driver = FoxessDriver("127.0.0.1", first_port, 247)
        try:
            assert (await driver.connect()).serial is None
            await driver.close()
            driver._port = second_port
            await driver.write_work_mode(WorkMode.BACKUP)
            assert second.energy.work_mode is WorkMode.BACKUP
        finally:
            await driver.close()


async def wait_for(condition, seconds=10.0):
    for _ in range(int(seconds / 0.05)):
        if condition():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("timed out")


async def test_collector_stores_nothing_from_another_device_until_the_owner_accepts_it(tmp_path):
    storage = Storage(tmp_path / "t.db")
    storage.set_meta("known_device", {"serial": OWN, "host": "127.0.0.1"})
    server, port = await start(foxess(OTHER))
    async with server:
        collector = Collector(FoxessDriver("127.0.0.1", port, 247), storage, 0.2, 30)
        collector.start()
        try:
            await wait_for(lambda: collector.wrong_device is not None)
            assert not collector.connected and collector.latest is None
            assert collector.wrong_device["found"] == "60BH…02" and collector.wrong_device["expected"] == "60BH…01"
            assert "Wechselrichter als bisher" in collector.last_error
            assert any(e["event"] == "anderes_geraet" for e in storage.get_meta("connection_events"))
            # the inverter was replaced: the owner confirms it
            assert await collector.accept_device("127.0.0.1")
            await wait_for(lambda: collector.latest is not None)
            assert collector.connected and collector.wrong_device is None
            assert storage.get_meta("known_device") == {"serial": OTHER, "host": "127.0.0.1"}
            assert not await collector.accept_device("127.0.0.1")  # nothing left to accept
        finally:
            await collector.stop()
            storage.close()


async def test_older_versions_knew_the_serial_number_from_the_firmware_note(tmp_path):
    storage = Storage(tmp_path / "t.db")
    storage.note_firmware(OWN, "1.0")
    collector = Collector(None, storage, 1, 30)
    assert collector.known_serial == OWN
    storage.close()


def test_status_and_accept_in_the_app(tmp_path):
    from test_security import app_client
    runtime, client = app_client(tmp_path)
    login(client)
    assert client.get("/api/status").json()["wrong_device"] is None
    assert client.post("/api/setup/accept-device").status_code == 409
    collector = runtime.collector
    collector.wrong_device = {"since": 1.0, "found": "60BH…02", "expected": "60BH…01"}
    collector._wrong_serial = OTHER
    assert client.get("/api/status").json()["wrong_device"]["found"] == "60BH…02"
    assert client.post("/api/setup/accept-device").status_code == 200
    assert runtime.storage.get_meta("known_device")["serial"] == OTHER
    entry = runtime.storage.control_log(10)[0]
    assert entry["action"] == "inverter_accepted"
    runtime.storage.close()
