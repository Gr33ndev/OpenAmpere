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
from openampere.drivers.modbus import ModbusReadError, WrongDevice
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


async def test_a_detection_without_serial_number_is_not_the_known_inverter():
    """Review of #568: a device whose detection finds no serial number (here the legacy map) is not the owner's
    inverter that told one."""
    server, port = await start(foxess(OTHER, H3_LEGACY))
    async with server:
        driver = FoxessDriver("127.0.0.1", port, 247)
        driver.expected_serial = OWN
        try:
            with pytest.raises(WrongDevice) as err:
                await driver.connect()
            assert err.value.found is None
        finally:
            await driver.close()


async def test_a_device_without_serial_number_is_reported_and_can_be_taken_over(tmp_path):
    storage = Storage(tmp_path / "t.db")
    storage.set_meta("known_device", {"serial": OWN, "host": "127.0.0.1"})
    server, port = await start(foxess(OTHER, H3_LEGACY))
    async with server:
        collector = Collector(FoxessDriver("127.0.0.1", port, 247), storage, 0.2, 30)
        collector.start()
        try:
            await wait_for(lambda: collector.wrong_device is not None)
            assert collector.wrong_device["found"] is None and collector.latest is None
            assert any(e["event"] == "anderes_geraet" for e in storage.get_meta("connection_events"))
            assert await collector.accept_device("127.0.0.1")
            await wait_for(lambda: collector.latest is not None)
            assert collector.known_serial is None
            assert storage.get_meta("known_device") == {"serial": None, "host": "127.0.0.1"}
            assert Collector(None, storage, 1, 30).known_serial is None  # also after a restart
        finally:
            await collector.stop()
            storage.close()


async def test_automatic_detection_finds_a_replaced_inverter_of_another_make(tmp_path):
    """Review of #568: after another device answered, the automatic driver detects the make again, so a SAJ that
    replaced a FoxESS is reported with its own serial number and can be taken over."""
    fox, saj = foxess(OWN), SajSimulatedInverter(serial="HST2103SIM000002")
    fox_server, fox_port = await start(fox)
    saj_server, saj_port = await start(saj)
    async with fox_server, saj_server:
        driver = AutoDriver("127.0.0.1", fox_port, 0)
        try:
            assert (await driver.connect()).serial == OWN
            await driver.close()
            driver._args["port"] = driver._driver._port = saj_port  # the same address reaches the SAJ now
            # the SAJ answers the FoxESS driver's requests only with errors, nothing is read from it
            with pytest.raises(ModbusReadError):
                await driver.read()
            # the SAJ answers this unit as a gateway would ("target failed"): like a proxy whose inverter is gone for
            # a moment, so the make is detected anew only on the third new connection in a row
            first = driver._driver
            for _ in range(2):
                with pytest.raises(ModbusReadError):
                    await driver.connect()
                assert driver._driver is first
            with pytest.raises(WrongDevice) as err:
                await driver.connect()
            assert err.value.found == "HST2103SIM000002"
            driver.expected_serial = err.value.found  # the owner took it over
            assert (await driver.connect()).driver == "saj"
        finally:
            await driver.close()


async def test_a_connection_changed_in_the_settings_takes_the_device_found_there(tmp_path):
    """Review of #568: an owner who sets up a replaced inverter in the settings does not get the notice."""
    storage = Storage(tmp_path / "t.db")
    storage.set_meta("known_device", {"serial": OWN, "host": "127.0.0.1"})
    server, port = await start(foxess(OTHER))
    async with server:
        collector = Collector(None, storage, 0.2, 30)
        try:
            await collector.reconfigure(FoxessDriver("127.0.0.1", port, 247), 0.2, new_device="127.0.0.1")
            await wait_for(lambda: collector.latest is not None)
            assert collector.wrong_device is None and collector.known_serial == OTHER
            # stored at once: a restart right after setting it up keeps the new inverter
            assert storage.get_meta("known_device") == {"serial": OTHER, "host": "127.0.0.1"}
            assert Collector(None, storage, 1, 30).known_serial == OTHER
        finally:
            await collector.stop()
            storage.close()


async def test_automatic_detection_keeps_its_driver_while_the_inverter_is_away():
    """Review of #568: a timeout or network error is no reason to detect the make anew (traffic to other makes'
    registers, and what the driver learned would be lost)."""
    server, port = await start(foxess(OWN))
    async with server:
        driver = AutoDriver("127.0.0.1", port, 0)
        try:
            await driver.connect()
            first = driver._driver
            await driver.close()
            server.close()
            await server.wait_closed()
            for _ in range(4):
                with pytest.raises((ConnectionError, OSError)):
                    await driver.connect()
            assert driver._driver is first
        finally:
            await driver.close()


async def test_changing_only_how_to_read_keeps_refusing_another_device(tmp_path):
    """Review of #568: a new timeout or poll interval while another device answers does not take that device over."""
    from openampere.runtime import Runtime
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    runtime.storage.set_meta("known_device", {"serial": OWN, "host": "127.0.0.1"})
    runtime.collector.known_serial = OWN
    server, port = await start(foxess(OTHER))
    async with server:
        try:
            await runtime.update_settings({"inverter.host": "127.0.0.1", "inverter.port": port, "inverter.driver": "foxess",
                                           "inverter.poll_interval": 2}, same_device=True)
            await wait_for(lambda: runtime.collector.wrong_device is not None)
            await runtime.update_settings({"inverter.poll_interval": 3, "inverter.timeout": 5})
            await asyncio.sleep(1.5)
            assert runtime.collector.known_serial == OWN and runtime.collector.latest is None
            await wait_for(lambda: runtime.collector.wrong_device is not None)
            # the owner sets up another address: the device there is theirs
            await runtime.update_settings({"inverter.port": port, "inverter.host": "localhost"})
            await wait_for(lambda: runtime.collector.latest is not None)
            assert runtime.collector.known_serial == OTHER
        finally:
            await runtime.collector.stop()
            runtime.storage.close()


async def test_only_gateway_answers_in_a_row_count_as_another_make(monkeypatch):
    """Review of #568: a timeout between gateway answers starts the count again (a proxy at night)."""
    from openampere.drivers import registry
    from openampere.drivers.modbus import ModbusTransientError

    def gateway():
        err = ModbusTransientError("target failed")
        err.code = 11
        return err

    class Inner:
        info = object()
        expected_serial = OWN

        def __init__(self, errors):
            self.errors = errors

        async def connect(self):
            return self.info

        async def check_identity(self):
            raise self.errors.pop(0)

        async def close(self):
            pass

        def _expected(self):
            return OWN

    detections = []

    async def detect_driver(*args, **kwargs):
        detections.append(args)
        raise ConnectionError("detected anew")

    monkeypatch.setattr(registry, "detect_driver", detect_driver)
    driver = AutoDriver("127.0.0.1", 502, 0)
    driver._driver = inner = Inner([gateway(), gateway(), ModbusTransientError("timeout"), gateway(), gateway()])
    for _ in range(5):
        with pytest.raises((ModbusReadError, ConnectionError)):
            await driver.connect()
    assert detections == [] and driver._driver is inner
    inner.errors.append(gateway())  # the third in a row
    with pytest.raises(ConnectionError, match="detected anew"):
        await driver.connect()
    assert len(detections) == 1


async def test_a_device_without_serial_number_set_up_in_the_settings_survives_a_restart(tmp_path):
    """Review of #568: the previous inverter's serial number (also in the firmware note) must not come back."""
    storage = Storage(tmp_path / "t.db")
    storage.set_meta("known_device", {"serial": OWN, "host": "127.0.0.1"})
    storage.note_firmware(OWN, "1.0")
    server, port = await start(foxess(OTHER, H3_LEGACY))
    async with server:
        collector = Collector(None, storage, 0.2, 30)
        try:
            await collector.reconfigure(FoxessDriver("127.0.0.1", port, 247), 0.2, new_device="127.0.0.1")
            await wait_for(lambda: collector.latest is not None)
            assert storage.get_meta("known_device") == {"serial": None, "host": "127.0.0.1"}
        finally:
            await collector.stop()
    restarted = Collector(FoxessDriver("127.0.0.1", port, 247), storage, 0.2, 30)
    assert restarted.known_serial is None
    storage.close()
