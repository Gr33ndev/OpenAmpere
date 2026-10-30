# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""The FoxESS AC EV Charger as a wallbox OpenAmpere reads itself, read only (#439): polling with one short connection
per read, back-off, unreachable with the previous smartbox named, no write request ever, and no energy counter, card
number or serial number read, shown or exported. Its other modes are a simulation (#440): decided like a real control,
logged as "would set", never written, and never taking surplus from other devices."""

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
    """Any write request through pymodbus fails the test, whatever would send it, also in the simulated modes (#440):
    neither the write path of native.py nor the Tuya connection that can write is ever reached."""
    def refuse(*_args, **_kwargs):
        raise AssertionError("a write request to a wallbox OpenAmpere only shows")
    for name in ("write_register", "write_registers", "write_coil", "write_coils", "mask_write_register",
                 "readwrite_registers"):
        monkeypatch.setattr(AsyncModbusTcpClient, name, refuse, raising=False)
    for name in ("_write", "_set_current", "_start", "device"):
        monkeypatch.setattr(NativeWallboxes, name, refuse)
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


def test_the_modes_are_offered_as_a_simulation_and_no_key_or_device_id(tmp_path):
    good = {"name": "Garage", "model": "foxess_ac", "host": "192.168.1.30"}
    wb = validate([{**good, "local_key": "ab12cd34ef56gh78", "device_id": "abcdefghij12",
                    "mode_current_a": {"now": 16}}])[0]
    assert (wb.port, wb.unit, wb.mode, wb.local_key, wb.device_id) == (502, 1, "manual", "", "")
    assert wb.mode_current_a == {"now": 16}  # the highest current of a mode limits the simulation, too (#440)
    assert validate([{**good, "port": 0, "unit": 0}])[0].port == 502  # missing: the usual port and unit id
    assert validate([{**good, "port": 1502, "unit": 2}])[0].unit == 2
    for mode in ("off", "pv", "minpv", "now"):
        assert validate([{**good, "mode": mode}])[0].mode == mode
    for bad in ({"port": 70000}, {"unit": 256}, {"port": "x"}, {"mode": "fast"}):
        with pytest.raises(ValueError):
            validate([{**good, **bad}])
    assert validate([{**good, "model": "de_tuya", "local_key": "ab12cd34ef56gh78", "port": 502}])[0].port is None

    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    wallboxes = NativeWallboxes(runtime)
    wb = wallboxes.save([good])[0]
    with pytest.raises(ValueError):
        wallboxes.set_mode(wb.id, "manual", current_a=10)
    view = wallboxes.view()
    item = view["wallboxes"][0]
    assert item["read_only"] is True and item["simulated"] is True and item["local_key_set"]
    assert item["modes"] == ["manual", "off", "pv", "minpv", "now"] and item["simulation"] is None  # "manual"
    model = next(m for m in view["models"] if m["key"] == "foxess_ac")
    assert model == {"key": "foxess_ac", "label": "FoxESS AC EV Charger", "protocol": "modbus", "read_only": True,
                     "controllable": True,  # the setup offers real control once added (#503)
                     "simulated": True, "modes": ["manual", "off", "pv", "minpv", "now"], "sold_by": ["EKD"],
                     "port": 502, "unit": 1}
    tuya = next(m for m in view["models"] if m["key"] == "de_tuya")
    assert tuya["read_only"] is False and tuya["simulated"] is False and not tuya["controllable"] and "port" not in tuya
    wallboxes.set_mode(wb.id, "pv", current_a=10)
    item = wallboxes.view()["wallboxes"][0]
    assert item["mode"] == "pv" and item["mode_current_a"] == {"pv": 10} and item["controlled"] is False
    assert item["simulation"] == {"state": "unknown", "current_a": None, "phases": None, "power_w": 0}
    assert runtime.storage.get_meta(native_module.OWNED) is None  # never written, so never owned


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


# ---- the simulated control (#440) ---------------------------------------------------------------------------------


def plugged(charger) -> None:
    """A car plugged in that does not charge: in the work mode "controlled" the charger waits for the app or a card."""
    charger.regs.update({0x1003: 1, 0x3000: 0})


def simulated_entries(runtime) -> list[tuple[str, object]]:
    """The planned writes in the control log, oldest first; every one marked as a simulation and not sent."""
    found = []
    for entry in reversed(runtime.storage.control_log(0)):
        if entry["action"] != "wallbox" or entry["details"]["to"]["action"] in ("mode", "mode_current"):
            continue  # other devices, and the owner's choice in the app (a setting like any other)
        assert entry["details"]["simulation"] is True and entry["dry_run"] is True, entry
        assert entry["result"].startswith("Simulation, nicht gesendet: "), entry
        found.append((entry["details"]["to"]["action"], entry["details"]["to"]["value"]))
    return found


async def check(wallboxes: NativeWallboxes, available: float, now: float) -> float:
    """One check at `now` with the last read counting as fresh then."""
    wb = wallboxes.wallboxes[0]
    wallboxes.live[wb.id].updated = now
    return await wallboxes.step(wb, available, now)


async def test_a_simulated_solar_charge_starts_raises_lowers_switches_phases_and_stops(tmp_path, monkeypatch):
    async with simulated() as (charger, port, _opened, _asked):
        plugged(charger)
        runtime, wallboxes = setup(tmp_path, monkeypatch, port)  # master switch on, test mode off: still no write
        wb = wallboxes.wallboxes[0]
        wallboxes.set_mode(wb.id, "pv")
        await poll(wallboxes)
        t0 = time.time()
        await check(wallboxes, 6000, t0)
        assert simulated_entries(runtime) == [] and wallboxes.live[wb.id].plan.state == "waiting"  # clouds?
        assert await check(wallboxes, 6000, t0 + 10) == 8 * 230 * 3  # confirmed: 8 A on three phases
        await check(wallboxes, 6000, t0 + 15)  # nothing changed: nothing logged
        await check(wallboxes, 9000, t0 + 20)  # up at most every 30 s
        assert simulated_entries(runtime) == [("start", {"current_a": 8, "phases": 3})]
        await check(wallboxes, 9000, t0 + 45)
        # the plan follows at once, the control log only every 5 minutes
        assert wallboxes.view()["wallboxes"][0]["simulation"]["current_a"] == 13
        assert simulated_entries(runtime) == [("start", {"current_a": 8, "phases": 3})]
        await check(wallboxes, 7000, t0 + 50)  # down right away
        await check(wallboxes, 3000, t0 + 60)  # too little for three phases, but they switch only every 12 minutes
        await check(wallboxes, 3000, t0 + 10 + 12 * 60)  # one phase now
        await check(wallboxes, 500, t0 + 740)  # a cloud: the lowest step through the waiting time
        assert wallboxes.live[wb.id].plan.on
        await check(wallboxes, 500, t0 + 740 + 10 * 60)  # the waiting time is over
        await check(wallboxes, 500, t0 + 1350)
        # the changes of the current (13, 10, 6 A; then 13 and 6 A on one phase) at most every 5 minutes, each entry
        # with the current at that time and the range since the entry before
        assert simulated_entries(runtime) == [
            ("start", {"current_a": 8, "phases": 3}),
            ("current", {"current_a": 6, "phases": 3, "low_a": 6, "high_a": 13, "minutes": 12}),
            ("current", {"current_a": 6, "phases": 1, "low_a": 6, "high_a": 13, "minutes": 10}),
            ("stop", False)]
        assert wallboxes.live[wb.id].plan.pending is None
        stop = runtime.storage.control_log(1)[0]
        assert stop["result"] == "Simulation, nicht gesendet: zu wenig Überschuss (500 W)"
        assert wallboxes.view()["wallboxes"][0]["simulation"] == {"state": "no_surplus", "current_a": None,
                                                                  "phases": None, "power_w": 0}
        # sun again: two checks in a row, and the car's pause of 5 minutes after a stop
        await check(wallboxes, 6000, t0 + 1360)
        await check(wallboxes, 6000, t0 + 1370)
        assert simulated_entries(runtime)[-1] == ("stop", False)
        await check(wallboxes, 6000, t0 + 1340 + 300)
        assert simulated_entries(runtime)[-1] == ("start", {"current_a": 8, "phases": 3})
        # the charger itself was never changed: it still waits for its app, and OpenAmpere owns nothing
        reading = wallboxes.fresh(wb)
        assert reading.connected and not reading.charging and reading.set_current_a is None
        assert runtime.storage.get_meta(native_module.OWNED) is None
    assert set(charger.functions) == {3}


async def test_a_fluctuating_surplus_logs_the_current_at_most_every_five_minutes(tmp_path, monkeypatch):
    """The simulation runs all day, also with the master switch off: clouds must not bury real changes in the control
    log (review of #443). Start and stop are always logged, changes of the current as one summary per 5 minutes."""
    async with simulated() as (charger, port, _opened, _asked):
        plugged(charger)
        runtime, wallboxes = setup(tmp_path, monkeypatch, port)
        wb = wallboxes.wallboxes[0]
        wallboxes.set_mode(wb.id, "pv")
        await poll(wallboxes)
        t0 = time.time()
        await check(wallboxes, 6000, t0)
        await check(wallboxes, 6000, t0 + 10)  # started with 8 A on three phases
        planned = []
        for k in range(120):  # 20 minutes, a cloud every 30 s: 6 kW and 9 kW in turn
            await check(wallboxes, 9000.0 if k // 3 % 2 else 6000.0, t0 + 20 + 10 * k)
            planned.append(wallboxes.live[wb.id].plan.current_a)
        assert set(planned) == {8, 13} and planned.count(13) > 30  # the plan follows every change at once
        entries = simulated_entries(runtime)
        currents = [value for action, value in entries if action == "current"]
        assert entries[0] == ("start", {"current_a": 8, "phases": 3}) and 3 <= len(currents) <= 4
        assert all((v["low_a"], v["high_a"], v["minutes"]) == (8, 13, 5) for v in currents), currents

        t = t0 + 1220
        for k in range(40):  # no more clouds: the last change is logged once
            await check(wallboxes, 6000.0, t + 10 * k)
        count = len(simulated_entries(runtime))
        for k in range(40, 100):  # 10 more minutes without a change: nothing is logged
            await check(wallboxes, 6000.0, t + 10 * k)
        assert len(simulated_entries(runtime)) == count

        await check(wallboxes, 9000.0, t + 1000)  # up after a long quiet time: logged at once
        assert simulated_entries(runtime)[count:] == [
            ("current", {"current_a": 13, "phases": 3, "low_a": 8, "high_a": 13, "minutes": 12})]
        await check(wallboxes, 6000.0, t + 1010)  # down again, 10 s later: collected
        assert len(simulated_entries(runtime)) == count + 1
        wallboxes.set_mode(wb.id, "off")
        await check(wallboxes, 6000.0, t + 1020)  # stopped: the collected change is logged first, then the stop
        assert simulated_entries(runtime)[count + 1:] == [
            ("current", {"current_a": 8, "phases": 3, "low_a": 8, "high_a": 13, "minutes": 1}), ("stop", False)]
        assert runtime.storage.control_log(1)[0]["result"] == "Simulation, nicht gesendet: Lademodus „Aus“"
    assert set(charger.functions) == {3}


async def test_minpv_and_now_and_a_charge_the_charger_started_by_itself(tmp_path, monkeypatch):
    async with simulated(charging=True) as (charger, port, _opened, _asked):  # plug and charge: it charges by itself
        runtime, wallboxes = setup(tmp_path, monkeypatch, port)
        wb = wallboxes.wallboxes[0]
        wallboxes.set_mode(wb.id, "pv")
        await poll(wallboxes)
        t0 = time.time()
        await check(wallboxes, 1000, t0)  # not its own charge and no surplus: stopped at once
        assert simulated_entries(runtime) == [("stop", False)]
        wallboxes.set_mode(wb.id, "minpv")
        await check(wallboxes, 1000, t0 + 10)  # at least the lowest step, on one phase
        assert simulated_entries(runtime)[-1] == ("start", {"current_a": 6, "phases": 1})
        assert "mindestens 1380 W" in runtime.storage.control_log(1)[0]["result"]
        wallboxes.set_mode(wb.id, "now")
        await check(wallboxes, 1000, t0 + 20)  # up at most every 30 s
        await check(wallboxes, 1000, t0 + 45)  # the highest current on one phase: three only after 12 minutes
        await check(wallboxes, 1000, t0 + 10 + 12 * 60)
        # 16 A on one phase since 12 minutes logged now; the switch to three phases waits for the next entry
        assert simulated_entries(runtime)[-1] == (
            "current", {"current_a": 16, "phases": 1, "low_a": 6, "high_a": 16, "minutes": 12})
        assert wallboxes.view()["wallboxes"][0]["simulation"] == {"state": "charging", "current_a": 16, "phases": 3,
                                                                  "power_w": 16 * 230 * 3}

        # back to "manual": the charge the simulation started would be stopped explicitly, and the plan is dropped
        wallboxes.set_mode(wb.id, "manual")
        assert await check(wallboxes, 1000, t0 + 800) == 11050  # only shown again
        await check(wallboxes, 1000, t0 + 810)
        # the switch to three phases is not lost: logged with the stop, before it
        assert simulated_entries(runtime)[-2:] == [
            ("current", {"current_a": 16, "phases": 3, "low_a": 16, "high_a": 16, "minutes": 1}), ("stop", False)]
        assert runtime.storage.control_log(1)[0]["result"] == \
            "Simulation, nicht gesendet: Lademodus „von Hand“, OpenAmpere gäbe die Wallbox ab"
        assert wallboxes.live[wb.id].plan == native_module.Plan()
        assert wallboxes.view()["wallboxes"][0]["simulation"] is None
        count = len(simulated_entries(runtime))

        # a new simulation starts from what the charger does: its own charge, which "off" would stop
        wallboxes.set_mode(wb.id, "pv")
        await check(wallboxes, 12000, t0 + 820)
        own = ("current", {"current_a": 16, "phases": 3, "low_a": 16, "high_a": 16})  # the first entry of this plan
        assert simulated_entries(runtime)[count:] == [own]
        wallboxes.set_mode(wb.id, "off")
        await check(wallboxes, 12000, t0 + 830)
        await check(wallboxes, 12000, t0 + 840)
        assert simulated_entries(runtime)[count:] == [own, ("stop", False)]
        assert wallboxes.view()["wallboxes"][0]["simulation"]["state"] == "off"

        # unplugged: the plan ends without a stop, the charge ends by itself
        wallboxes.set_mode(wb.id, "now")
        await check(wallboxes, 0, t0 + 850)
        charger.regs.update({0x1003: 0, 0x100B: 0, 0x100C: 0, 0x100D: 0, 0x100E: 0})
        await poll(wallboxes)
        before = len(simulated_entries(runtime))
        await check(wallboxes, 0, t0 + 860)
        assert len(simulated_entries(runtime)) == before
        assert wallboxes.view()["wallboxes"][0]["simulation"]["state"] == "no_car"
        assert runtime.storage.get_meta(native_module.OWNED) is None
    assert set(charger.functions) == {3}


async def test_removing_the_charger_ends_the_simulation_with_a_planned_stop(tmp_path, monkeypatch):
    async with simulated() as (charger, port, _opened, _asked):
        plugged(charger)
        runtime, wallboxes = setup(tmp_path, monkeypatch, port)
        wb = wallboxes.wallboxes[0]
        wallboxes.set_mode(wb.id, "now")
        await poll(wallboxes)
        await check(wallboxes, 0, time.time())
        report = wallboxes.report()[0]
        assert report["simulation"] == {"state": "charging", "current_a": 16, "phases": 3, "power_w": 11040}
        assert "- Simulation (nichts geschrieben): state charging, current_a 16" in "\n".join(report_markdown(report))
        wallboxes.save([])
        assert simulated_entries(runtime)[-1] == ("stop", False)
        assert runtime.storage.control_log(1)[0]["result"] == ("Simulation, nicht gesendet: Wallbox entfernt, "
                                                               "ausgeschaltet oder geändert, OpenAmpere gäbe die "
                                                               "Wallbox ab")
    assert set(charger.functions) == {3}


async def test_manual_saved_with_the_setup_ends_the_simulation_with_the_master_switch_off(tmp_path, monkeypatch):
    """Not only set_mode(): "manual" saved with the whole list (a restored backup, another client of PUT
    /api/wallboxes) ends the plan at once, as step() does not run for a wallbox in "manual" with the master switch
    off, and a later simulated mode starts afresh from what the charger does."""
    async with simulated() as (charger, port, _opened, _asked):
        plugged(charger)
        runtime, wallboxes = setup(tmp_path, monkeypatch, port)
        wb = wallboxes.wallboxes[0]
        wallboxes.set_mode(wb.id, "now")
        await poll(wallboxes)
        await check(wallboxes, 0, time.time())
        assert wallboxes.live[wb.id].plan.started is True
        runtime.config.control.enabled = False
        wallboxes.save([{**wallboxes.view()["wallboxes"][0], "mode": "manual"}])
        assert wallboxes.wallboxes[0].mode == "manual"
        assert simulated_entries(runtime)[-1] == ("stop", False)
        assert runtime.storage.control_log(1)[0]["result"] == ("Simulation, nicht gesendet: Lademodus „von Hand“, "
                                                               "OpenAmpere gäbe die Wallbox ab")
        assert wallboxes.live[wb.id].plan == native_module.Plan()
        wallboxes.save([{**wallboxes.view()["wallboxes"][0], "name": "Carport"}])  # nothing more to end
        assert simulated_entries(runtime)[-1] == ("stop", False) and len(simulated_entries(runtime)) == 2
    assert set(charger.functions) == {3}


async def test_the_simulation_takes_no_surplus_from_other_devices_and_runs_with_the_master_switch_off(
        tmp_path, monkeypatch):
    from openampere import consumers as consumers_module

    calls = []
    monkeypatch.setattr(consumers_module, "_call", calls.append)
    async with simulated() as (charger, port, _opened, _asked):
        plugged(charger)
        runtime, wallboxes = setup(tmp_path, monkeypatch, port)
        wb = wallboxes.wallboxes[0]
        wallboxes.set_mode(wb.id, "minpv")
        control = SurplusControl(runtime, None, wallboxes)
        rod = control.save([{"name": "Heizstab", "kind": "shelly1", "host": "rod.local", "power_w": 2000,
                             "min_on_min": 0}])[0]
        # the wallbox before the heating rod: a planned charge that took the surplus would leave it too little
        runtime.storage.set_meta("surplus_order", {"order": ["wallbox", f"c:{rod.id}", "battery"], "battery_soc": 50})
        t0 = time.time()

        async def tick(now: float, grid: float) -> None:
            runtime.collector.latest = Snapshot(timestamp=now, grid_power=grid, battery_power=0.0, battery_soc=90.0)
            await poll(wallboxes)
            wallboxes.live[wb.id].updated = now  # read just now
            await control.tick(now)

        await tick(t0, -2500.0)
        await tick(t0 + 30, -2500.0)
        assert simulated_entries(runtime) == [("start", {"current_a": 10, "phases": 1})]  # 2.3 kW planned
        assert calls == ["http://rod.local/relay/0?turn=on"]  # the heating rod still gets the whole 2.5 kW
        assert runtime.storage.get_meta(native_module.OWNED) is None

        # master switch off: the heating rod is switched off, the simulation goes on (it writes nothing)
        runtime.config.control.enabled = False
        await tick(t0 + 60, -9000.0)
        assert calls[-1] == "http://rod.local/relay/0?turn=off"
        assert wallboxes.view()["wallboxes"][0]["simulation"]["current_a"] == 16  # planned, logged later
        assert simulated_entries(runtime) == [("start", {"current_a": 10, "phases": 1})]
        await tick(t0 + 12 * 60, -9000.0)  # three phases after the charger's switch interval of 12 minutes
        assert simulated_entries(runtime)[-1] == (
            "current", {"current_a": 16, "phases": 1, "low_a": 10, "high_a": 16, "minutes": 12})
        assert wallboxes.view()["wallboxes"][0]["simulation"] == {"state": "charging", "current_a": 13, "phases": 3,
                                                                  "power_w": 13 * 230 * 3}

        # still with the master switch off, back to "manual": the planned charge ends at once, not only when step()
        # runs again (it does not for a wallbox in "manual" while the master switch is off)
        wallboxes.set_mode(wb.id, "manual")
        (action, value), stop = simulated_entries(runtime)[-2:]  # the held-back change first, then the stop
        assert (action, value["current_a"], value["phases"], value["low_a"], value["high_a"]) == ("current", 13, 3, 13, 16)
        assert stop == ("stop", False)
        assert wallboxes.live[wb.id].plan.on is False
        await tick(t0 + 13 * 60, -9000.0)
        assert simulated_entries(runtime)[-1] == ("stop", False)  # nothing more
    assert set(charger.functions) == {3}


# ---- the setup in the app -----------------------------------------------------------------------------------------


def test_the_setup_reads_once_saves_and_offers_the_modes_as_a_simulation(tmp_path, monkeypatch):
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
            chosen = web.post(f"/api/wallboxes/{wb['id']}/mode", json={"mode": "pv", "current_a": 10})
            assert chosen.status_code == 200, chosen.text
            item = chosen.json()["wallboxes"][0]
            assert (item["mode"], item["simulated"], item["read_only"]) == ("pv", True, True)
            assert item["simulation"]["state"] in ("unknown", "no_car")  # the plan of the simulation, not written
            assert created[0].wallboxes[0].mode == "pv"
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
    for mode in ("now", "pv", "manual"):  # its simulated modes are for the app alone (#440)
        response = client.put(f"{V1}/wallboxes/{wb.id}/mode", json={"mode": mode})
        assert response.status_code == 400 and "nur an" in response.json()["detail"]
    assert wallboxes.wallboxes[0].mode == "manual"
    assert "192.168.0.51" not in str(info) + str(state)
