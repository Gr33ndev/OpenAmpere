# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""The FoxESS AC EV Charger as a wallbox OpenAmpere reads itself, read only (#439): polling with one short connection
per read, back-off, unreachable with the previous smartbox named, only the mode "manual", no write request ever, and
no energy counter, card number or serial number read, shown or exported."""

import asyncio
import contextlib
import logging
import time

import pytest
from fastapi.testclient import TestClient
from pymodbus.client import AsyncModbusTcpClient

from conftest import login
from openampere.consumers import SurplusControl
from openampere.devices import Devices
from openampere.drivers.base import Snapshot
from openampere.runtime import Runtime
from openampere.simulator import SimulatedFoxCharger
from openampere.storage import Storage
from openampere.wallbox import modbus
from openampere.wallbox import native as native_module
from openampere.wallbox.foxess import FoxEssChargerLive
from openampere.wallbox.native import NativeWallboxes, report_markdown, validate

WRITES = {5, 6, 15, 16}  # Modbus function codes that write
PRIVATE = range(0x1016, 0x1032)  # energy counters, card number, model code, serial number: the live view needs none


@pytest.fixture(autouse=True)
def no_write_ever(monkeypatch):
    """Any write request through pymodbus fails the test, whatever would send it."""
    def refuse(*_args, **_kwargs):
        raise AssertionError("a write request to a wallbox OpenAmpere only shows")
    for name in ("write_register", "write_registers", "write_coil", "write_coils", "mask_write_register",
                 "readwrite_registers"):
        monkeypatch.setattr(AsyncModbusTcpClient, name, refuse, raising=False)
    monkeypatch.setattr(modbus, "REQUEST_GAP_S", 0)


@contextlib.asynccontextmanager
async def simulated(**kwargs):
    """The simulated charger, how often a connection was opened, and every block that was asked for."""
    charger = SimulatedFoxCharger(**kwargs)
    opened, asked = [], []
    handle = charger.handle

    def recorded(pdu: bytes) -> bytes:
        if pdu[0] in (3, 4):
            asked.append((int.from_bytes(pdu[1:3], "big"), int.from_bytes(pdu[3:5], "big")))
        return handle(pdu)
    charger.handle = recorded

    active = [0]
    charger.peak = 0  # the most connections at the same time; the charger itself takes only one

    async def serve(reader, writer):
        opened.append(True)
        active[0] += 1
        charger.peak = max(charger.peak, active[0])
        try:
            await charger.serve_client(reader, writer)
        finally:
            active[0] -= 1
    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    try:
        yield charger, server.sockets[0].getsockname()[1], opened, asked
    finally:
        server.close()
        await server.wait_closed()
        assert not set(charger.functions) & WRITES, charger.functions  # whatever the test did: never a write


class NoEvcc:
    def fresh(self):
        return None


def closed_port() -> int:
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def setup(tmp_path, monkeypatch, port: int) -> tuple[Runtime, NativeWallboxes]:
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    runtime.config.control.enabled, runtime.config.control.dry_run = True, False  # all writing allowed: still none
    monkeypatch.setattr(type(runtime.collector), "stale", property(lambda self: False))
    wallboxes = NativeWallboxes(runtime)
    wallboxes.save([{"name": "Garage", "model": "foxess_ac", "host": "127.0.0.1", "port": port}])
    return runtime, wallboxes


async def poll(wallboxes: NativeWallboxes) -> None:
    for live in wallboxes.live.values():
        live.retry_at, live.updated = 0.0, 0.0
    wallboxes.refresh()
    await wallboxes.wait()


async def closed(charger) -> bool:
    """Whether the charger saw its connection closed (it notices on its next turn of the event loop)."""
    for _ in range(100):
        if charger.connections == 0:
            return True
        await asyncio.sleep(0.01)
    return False


# ---- the registers ------------------------------------------------------------------------------------------------


def test_idle_charging_single_phase_and_locked():
    idle = FoxEssChargerLive.interpret(SimulatedFoxCharger().regs)
    assert (idle.state, idle.connected, idle.charging, idle.power_w) == ("idle", False, False, 0.0)
    assert idle.voltage_v == [230.1, 230.2, 230.3] and idle.installed_phases == 3
    assert (idle.max_current_a, idle.steps_a[0], idle.steps_a[-1]) == (32, 6, 32)
    assert (idle.firmware, idle.work_mode, idle.phase_switching) == ("1.16", "plug_and_charge", True)
    assert idle.set_current_a is None and idle.lifetime_kwh is None  # not known, not read

    charging = FoxEssChargerLive.interpret(SimulatedFoxCharger(charging=True).regs)
    assert (charging.state, charging.connected, charging.charging, charging.active_phases) == ("charging", True, True, 3)
    assert charging.current_a == [16.0, 16.0, 16.0] and charging.power_w == 11050  # measured, finer than 0.1 kW

    regs = {**SimulatedFoxCharger(single_phase=True, charging=True).regs, 0x1009: 0, 0x100A: 0, 0x100C: 0, 0x100D: 0}
    single = FoxEssChargerLive.interpret(regs)
    assert (single.installed_phases, single.active_phases, single.phase_switching) == (1, 1, None)

    # the plug register read 1 without a car on a real device (#431): only the status counts
    locked = FoxEssChargerLive.interpret({**SimulatedFoxCharger().regs, 0x1003: 8, 0x1005: 1, 0x3000: 2})
    assert (locked.state, locked.connected, locked.work_mode) == ("unknown", False, "locked")
    # a charging status without power (e.g. switching phases, or a full car) is no charge
    assert not FoxEssChargerLive.interpret({**SimulatedFoxCharger().regs, 0x1003: 9}).charging


# ---- reading in the background ------------------------------------------------------------------------------------


async def test_each_poll_reads_over_one_short_connection_with_function_code_3_only(tmp_path, monkeypatch):
    async with simulated(charging=True) as (charger, port, opened, asked):
        _runtime, wallboxes = setup(tmp_path, monkeypatch, port)
        wb = wallboxes.wallboxes[0]
        await poll(wallboxes)
        assert await closed(charger)  # closed right after reading: never held between polls
        reading = wallboxes.fresh(wb)
        assert reading.charging and reading.power_w == 11050 and wallboxes.power(wb) == 11050
        await poll(wallboxes)
        assert len(opened) == 2 and await closed(charger)  # a new short connection for each poll
    assert set(charger.functions) == {3}
    assert asked and not any(start <= a < start + count for start, count in asked for a in PRIVATE)


async def test_a_single_phase_charger_is_read_without_its_phase_switching_block(tmp_path, monkeypatch):
    async with simulated(single_phase=True) as (_charger, port, _opened, asked):
        _runtime, wallboxes = setup(tmp_path, monkeypatch, port)
        await poll(wallboxes)
        reading = wallboxes.fresh(wallboxes.wallboxes[0])
    assert reading is not None and reading.phase_switching is None
    assert (0x3007, 5) in asked  # asked for, refused as unknown addresses, skipped


async def test_unreachable_names_the_smartbox_backs_off_and_shows_no_old_values(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="openampere.wallbox.native")
    async with simulated(charging=True) as (_charger, port, _opened, _asked):
        runtime, wallboxes = setup(tmp_path, monkeypatch, port)
        wb = wallboxes.wallboxes[0]
        await poll(wallboxes)
        assert wallboxes.power(wb) > 0
        # values older than FRESH_S are not used, also not for the house load
        assert wallboxes.fresh(wb, time.time() + native_module.FRESH_S + 1) is None
        assert wallboxes.power(wb, time.time() + native_module.FRESH_S + 1) == 0.0
    # the previous smartbox is powered again (or the charger is off): nothing answers
    for _ in range(3):
        await poll(wallboxes)
    live = wallboxes.live[wb.id]
    assert live.online is False and live.failures == 3 and "bisherige Smartbox" in live.error
    assert live.retry_at - live.offline_since >= 50  # 15 s, 30 s, 60 s, … up to 5 minutes
    assert sum("not reachable" in r.message for r in caplog.records) == 1
    view = wallboxes.view()["wallboxes"][0]
    assert view["online"] is False and view["reading"] is None and "Smartbox" in view["error"]
    devices = Devices(runtime, SurplusControl(runtime, None, wallboxes), NoEvcc(), wallboxes)  # type: ignore[arg-type]
    shown = next(d for d in devices.live() if d["source"] == "wallbox")
    assert shown["power_w"] == 0.0 and "Smartbox" in shown["error"]


async def test_a_charger_outside_the_modbus_mode_says_it_refuses_reading(tmp_path, monkeypatch):
    async with simulated(busy=True) as (_charger, port, _opened, _asked):
        _runtime, wallboxes = setup(tmp_path, monkeypatch, port)
        await poll(wallboxes)
    live = next(iter(wallboxes.live.values()))
    assert live.online is False and "Gerät beschäftigt" in live.error


async def test_the_check_the_setup_test_and_the_poll_never_connect_at_the_same_time(tmp_path, monkeypatch):
    """The charger may allow one connection (and the simulated one does): the wallbox check, the test in the setup
    and the poll of the same charger take turns."""
    from openampere import probe
    from openampere.probe import Probe
    from openampere.wallbox import WallboxCheck

    monkeypatch.setattr(probe, "REQUEST_GAP_S", 0)
    monkeypatch.setattr(probe, "CONNECT_SETTLE_S", 0.05)
    async with simulated(charging=True) as (charger, port, opened, _asked):
        charger.latency_s = 0.05  # every answer takes a while: without taking turns the reads would overlap
        runtime, wallboxes = setup(tmp_path, monkeypatch, port)
        wb = wallboxes.wallboxes[0]
        reader = Probe(runtime)
        reader.exclusive = wallboxes.exclusive  # as the app sets it up
        check = WallboxCheck(reader)
        for _ in range(3):
            for live in wallboxes.live.values():
                live.retry_at, live.updated = 0.0, 0.0
            wallboxes.refresh()  # the poll starts in the background
            result, tested = await asyncio.gather(check.run("foxess_ac", "127.0.0.1", port, None),
                                                  wallboxes.test("foxess_ac", "127.0.0.1", port, None))
            await wallboxes.wait()
            assert result["outcome"] == "readable" and tested.charging
            assert wallboxes.live[wb.id].online and wallboxes.live[wb.id].error is None
        assert charger.peak == 1 and len(opened) == 9  # one at a time, every one of them answered


async def test_a_check_of_another_device_does_not_wait_for_the_poll(tmp_path, monkeypatch):
    _runtime, wallboxes = setup(tmp_path, monkeypatch, 1502)
    lock = wallboxes.live.setdefault(wallboxes.wallboxes[0].id, native_module.Live()).lock
    async with lock:  # a poll of the wallbox is running
        async with asyncio.timeout(1):
            async with wallboxes.exclusive("127.0.0.1", 502):  # another port: another device
                pass
            async with wallboxes.exclusive("127.0.0.2", 1502):  # another address
                pass
    async with wallboxes.exclusive("127.0.0.1", 1502):
        assert lock.locked()  # the same charger: its poll waits
    assert not lock.locked()


# ---- never written ------------------------------------------------------------------------------------------------


async def test_the_surplus_control_counts_its_power_but_never_writes(tmp_path, monkeypatch):
    async with simulated(charging=True) as (charger, port, _opened, _asked):
        runtime, wallboxes = setup(tmp_path, monkeypatch, port)
        wb = wallboxes.wallboxes[0]
        control = SurplusControl(runtime, None, wallboxes)
        for now, grid in ((time.time(), -8000.0), (time.time() + 10, 3000.0)):  # lots of surplus, then none
            runtime.collector.latest = Snapshot(timestamp=now, grid_power=grid, battery_power=0.0, battery_soc=90.0)
            await poll(wallboxes)
            assert await wallboxes.step(wb, 9000.0) == 11050  # shown and counted, nothing decided
            await control.tick()
        assert not wallboxes.controls(wb) and runtime.storage.control_log() == []
        assert runtime.storage.get_meta(native_module.OWNED) is None
        await wallboxes.release_all("Steuerung ausgeschaltet")
        runtime.config.control.enabled = False
        await control.tick()
        devices = Devices(runtime, control, NoEvcc(), wallboxes)  # type: ignore[arg-type]
        shown = next(d for d in devices.live() if d["source"] == "wallbox")
        assert shown["power_w"] == 11050 and shown["on"] is True  # in the house load like any wallbox
    assert set(charger.functions) == {3}


def test_only_the_manual_mode_and_no_key_or_device_id(tmp_path):
    good = {"name": "Garage", "model": "foxess_ac", "host": "192.168.1.30"}
    wb = validate([{**good, "local_key": "ab12cd34ef56gh78", "device_id": "abcdefghij12",
                    "mode_current_a": {"now": 16}}])[0]
    assert (wb.port, wb.unit, wb.mode, wb.local_key, wb.device_id, wb.mode_current_a) == (502, 1, "manual", "", "", {})
    assert validate([{**good, "port": 0, "unit": 0}])[0].port == 502  # missing: the usual port and unit id
    assert validate([{**good, "port": 1502, "unit": 2}])[0].unit == 2
    for mode in ("off", "pv", "minpv", "now"):
        with pytest.raises(ValueError, match="nur an"):
            validate([{**good, "mode": mode}])
    for bad in ({"port": 70000}, {"unit": 256}, {"port": "x"}):
        with pytest.raises(ValueError):
            validate([{**good, **bad}])
    assert validate([{**good, "model": "de_tuya", "local_key": "ab12cd34ef56gh78", "port": 502}])[0].port is None

    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    wallboxes = NativeWallboxes(runtime)
    wb = wallboxes.save([good])[0]
    for mode in ("off", "pv", "minpv", "now"):
        with pytest.raises(ValueError, match="nur an"):
            wallboxes.set_mode(wb.id, mode)
    with pytest.raises(ValueError):
        wallboxes.set_mode(wb.id, "manual", current_a=10)
    assert wallboxes.wallboxes[0].mode == "manual" and runtime.storage.control_log() == []
    view = wallboxes.view()
    item = view["wallboxes"][0]
    assert item["read_only"] is True and item["modes"] == ["manual"] and item["local_key_set"]
    model = next(m for m in view["models"] if m["key"] == "foxess_ac")
    assert model == {"key": "foxess_ac", "label": "FoxESS AC EV Charger", "protocol": "modbus", "read_only": True,
                     "modes": ["manual"], "sold_by": ["EKD"], "port": 502, "unit": 1}
    tuya = next(m for m in view["models"] if m["key"] == "de_tuya")
    assert tuya["read_only"] is False and "port" not in tuya


def test_the_inverters_address_is_refused(tmp_path):
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    runtime.config.inverter.host, runtime.config.inverter.port = "127.0.0.1", 502
    wallboxes = NativeWallboxes(runtime)
    with pytest.raises(ValueError, match="Adresse deines Wechselrichters"):
        wallboxes.save([{"name": "Garage", "model": "foxess_ac", "host": "127.0.0.1"}])
    # another port of the same address can be another device (#417)
    assert wallboxes.save([{"name": "Garage", "model": "foxess_ac", "host": "127.0.0.1", "port": 1502}])


async def test_view_and_report_have_no_address_counter_card_or_serial(tmp_path, monkeypatch):
    async with simulated(charging=True) as (_charger, port, _opened, _asked):
        _runtime, wallboxes = setup(tmp_path, monkeypatch, port)
        await poll(wallboxes)
    report = wallboxes.report()
    assert report[0]["model"] == "foxess_ac" and report[0]["port"] == port and report[0]["reading"]["charging"]
    text = "\n".join(report_markdown(report[0]))
    assert f"Port {port}, Geräteadresse 1" in text and "127.0.0.1" not in str(report)
    for shown in (str(report), str(wallboxes.view())):
        assert "SIMULATEDCHARGER" not in shown and "A022KP1" not in shown and "12345" not in shown
        assert "lifetime_kwh" not in shown


# ---- the setup in the app -----------------------------------------------------------------------------------------


def test_the_setup_reads_once_saves_and_refuses_the_controlled_modes(tmp_path, monkeypatch):
    from openampere import api as api_module
    from openampere.api import create_app

    created = []

    class Wallboxes(NativeWallboxes):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            created.append(self)

        def refresh(self, now=None):  # no background reads from a test
            pass
    monkeypatch.setattr(api_module, "NativeWallboxes", Wallboxes)

    async def run():
        async with simulated() as (charger, port, _opened, _asked):
            web = login(TestClient(create_app(Runtime({}, Storage(tmp_path / "t.db"))), base_url="https://testserver"))
            tested = await asyncio.to_thread(web.post, "/api/wallboxes/test",
                                             json={"model": "foxess_ac", "host": "127.0.0.1", "port": port})
            assert tested.status_code == 200, tested.text
            assert tested.json()["reading"]["work_mode"] == "plug_and_charge"
            assert "lifetime_kwh" not in tested.json()["reading"]
            failed = await asyncio.to_thread(web.post, "/api/wallboxes/test",
                                             json={"model": "foxess_ac", "host": "127.0.0.1", "port": closed_port()})
            assert failed.status_code == 400 and "bisherige Smartbox" in failed.json()["detail"]
            saved = web.put("/api/wallboxes", json={"wallboxes": [
                {"name": "Garage", "model": "foxess_ac", "host": "127.0.0.1", "port": port, "unit": 1}]})
            assert saved.status_code == 200, saved.text
            wb = saved.json()["wallboxes"][0]
            assert (wb["port"], wb["unit"], wb["read_only"], wb["mode"]) == (port, 1, True, "manual")
            refused = web.post(f"/api/wallboxes/{wb['id']}/mode", json={"mode": "pv"})
            assert refused.status_code == 400 and "nur an" in refused.json()["detail"]
            bad = web.put("/api/wallboxes", json={"wallboxes": [
                {"name": "Garage", "model": "foxess_ac", "host": "127.0.0.1", "mode": "now"}]})
            assert bad.status_code == 400
            assert created[0].wallboxes[0].mode == "manual"
        assert set(charger.functions) == {3}
    asyncio.run(run())


def test_home_assistant_gets_it_as_read_only_and_cannot_set_a_mode(tmp_path, monkeypatch):
    from test_external_api import V1, app_client, new_token

    from openampere import api as api_module
    from openampere.api import create_app
    from openampere.wallbox.base import Reading
    from openampere.wallbox.native import Live

    created = []

    class Wallboxes(NativeWallboxes):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            created.append(self)

        def refresh(self, now=None):
            pass
    monkeypatch.setattr(api_module, "NativeWallboxes", Wallboxes)
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    web = login(TestClient(create_app(runtime), base_url="https://testserver"))
    assert web.put("/api/wallboxes", json={"wallboxes": [
        {"name": "Garage", "model": "foxess_ac", "host": "192.168.0.51"}]}).status_code == 200
    wallboxes = created[0]
    wb = wallboxes.wallboxes[0]
    wallboxes.live[wb.id] = Live(reading=Reading(state="charging", connected=True, charging=True, power_w=11050.0,
                                                 active_phases=3), updated=time.time(), online=True)
    client = app_client(web, new_token(web, "control"))
    info = client.get(f"{V1}/info").json()
    assert info["wallboxes"] == [{"id": wb.id, "name": "Garage", "model": "FoxESS AC EV Charger", "read_only": True}]
    state = client.get(f"{V1}/state").json()["wallboxes"][0]
    assert (state["mode"], state["power_w"], state["current_a"]) == ("manual", 11050.0, None)
    monkeypatch.setattr(runtime.config.control, "enabled", True)
    response = client.put(f"{V1}/wallboxes/{wb.id}/mode", json={"mode": "now"})
    assert response.status_code == 400 and wallboxes.wallboxes[0].mode == "manual"
    assert "192.168.0.51" not in str(info) + str(state)
