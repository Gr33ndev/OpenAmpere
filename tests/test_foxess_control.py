# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Real control of the FoxESS AC EV Charger once its owner switches it on (#465): the command validity and fallback
current written once, the current with function code 16 before start or stop with function code 6, the current
written again while the car charges and never after a stop (the charger would start again), nothing in test mode or
outside the work mode "controlled", and a charge OpenAmpere started stopped when it lets go."""

import asyncio
import contextlib
import time

import pytest
from fastapi.testclient import TestClient

from conftest import login
from openampere.runtime import Runtime
from openampere.simulator import SimulatedFoxCharger
from openampere.storage import Storage
from openampere.wallbox import modbus
from openampere.wallbox import native as native_module
from openampere.wallbox.native import NativeWallboxes, read_only, real, simulated

PREPARED = [(0x3005, 60), (0x3006, 60)]


@pytest.fixture(autouse=True)
def no_gap(monkeypatch):
    monkeypatch.setattr(modbus, "REQUEST_GAP_S", 0)


@contextlib.asynccontextmanager
async def charger_with_car(work_mode: int = 0, switching: bool = False):
    """switching: automatic 1/3-phase switching on, so real control also writes the power setpoint (#478); off for
    the tests of the current, start and stop."""
    charger = SimulatedFoxCharger()
    charger.regs[0x3000] = work_mode
    charger.regs[0x300A] = int(switching)
    charger.plug()
    server = await asyncio.start_server(charger.serve_client, "127.0.0.1", 0)
    try:
        yield charger, server.sockets[0].getsockname()[1]
    finally:
        server.close()
        await server.wait_closed()


def setup(tmp_path, port: int, *, control: bool = True, mode: str = "now", dry_run: bool = False):
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    runtime.config.control.enabled, runtime.config.control.dry_run = True, dry_run
    wallboxes = NativeWallboxes(runtime)
    wallboxes.save([{"name": "Garage", "model": "foxess_ac", "host": "127.0.0.1", "port": port, "mode": mode}])
    stored = wallboxes.wallboxes
    stored[0].control = control  # as set_control() stores it after the question in the app
    wallboxes._store(stored)
    return runtime, wallboxes, wallboxes.wallboxes[0]


async def check(wallboxes: NativeWallboxes, wb, now: float, available: float = 0.0) -> None:
    """One poll and one check at the time `now` (the read counts as made then)."""
    live = await wallboxes.read(wb)
    live.updated = now
    await wallboxes.step(wb, available, now)


def entries(runtime: Runtime, action: str) -> list[dict]:
    return [e for e in runtime.storage.control_log(100) if e["details"].get("to", {}).get("action") == action]


def test_only_a_controllable_model_keeps_the_control_flag(tmp_path):
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    wallboxes = NativeWallboxes(runtime)
    wallboxes.save([{"name": "Mobil", "model": "de_tuya", "host": "192.168.1.20", "local_key": "k" * 16,
                     "control": True},
                    {"name": "Garage", "model": "foxess_ac", "host": "192.168.1.21"}])
    de, fox = wallboxes.wallboxes
    assert not de.control and not fox.control
    assert read_only(fox) and simulated(fox) and not real(fox)  # without the owner's yes: shown and simulated
    view = {w["name"]: w for w in wallboxes.view()["wallboxes"]}
    assert view["Garage"]["controllable"] and view["Garage"]["simulated"] and not view["Mobil"]["controllable"]


async def test_now_prepares_once_sets_the_current_and_starts(tmp_path):
    async with charger_with_car() as (charger, port):
        runtime, wallboxes, wb = setup(tmp_path, port)
        assert real(wb) and not read_only(wb) and not simulated(wb)
        t0 = time.time()
        await check(wallboxes, wb, t0)
        assert charger.writes == [*PREPARED, (0x3001, 160), (0x4001, 1)]  # 16 A, the owner's limit
        assert charger.regs[0x1003] == 3 and set(charger.functions) == {3, 6, 16}
        assert entries(runtime, "prepare")[0]["result"] == "ok"
        assert [e["dry_run"] for e in entries(runtime, "start")] == [False]
        await check(wallboxes, wb, t0 + 5)
        assert charger.writes[-1] == (0x4001, 1) and len(charger.writes) == 4  # charging: nothing to write


async def test_the_current_is_written_again_while_charging_without_a_log_entry(tmp_path):
    async with charger_with_car() as (charger, port):
        runtime, wallboxes, wb = setup(tmp_path, port)
        t0 = time.time()
        await check(wallboxes, wb, t0)
        logged = len(runtime.storage.control_log(100))
        await check(wallboxes, wb, t0 + 10)
        assert len(charger.writes) == 4
        await check(wallboxes, wb, t0 + 26)  # within the charger's validity of 60 s
        assert charger.writes[4:] == [(0x3001, 160)]
        await check(wallboxes, wb, t0 + 52)
        assert charger.writes[5:] == [(0x3001, 160)]
        assert len(runtime.storage.control_log(100)) == logged  # the heartbeat is not logged


async def test_after_a_stop_no_current_is_written_that_would_start_it_again(tmp_path):
    async with charger_with_car() as (charger, port):
        _runtime, wallboxes, wb = setup(tmp_path, port)
        t0 = time.time()
        await check(wallboxes, wb, t0)
        wallboxes.set_mode(wb.id, "off")
        wb = wallboxes.wallboxes[0]
        await check(wallboxes, wb, t0 + 10)
        assert charger.writes[-1] == (0x4001, 2) and charger.regs[0x1003] == 4
        stopped_at = len(charger.writes)
        for minute in range(1, 6):
            await check(wallboxes, wb, t0 + 10 + 60 * minute)
        assert len(charger.writes) == stopped_at and charger.restarted_by_current == 0


async def test_solar_waits_for_surplus_and_stops_without_it(tmp_path):
    async with charger_with_car() as (charger, port):
        _runtime, wallboxes, wb = setup(tmp_path, port, mode="pv")
        t0 = time.time() - 3600  # long after the last start or stop
        await check(wallboxes, wb, t0, available=500)
        assert charger.writes == []  # not enough for 6 A on three phases
        for i in range(3):
            await check(wallboxes, wb, t0 + 400 + i * 10, available=11_000)
        assert (0x4001, 1) in charger.writes and charger.regs[0x1003] == 3
        assert (0x3001, 150) in charger.writes  # 15 A fit into 11 kW on three phases


async def test_test_mode_writes_nothing(tmp_path):
    async with charger_with_car() as (charger, port):
        runtime, wallboxes, wb = setup(tmp_path, port, dry_run=True)
        await check(wallboxes, wb, time.time())
        assert charger.writes == [] and 16 not in charger.functions and 6 not in charger.functions
        assert all(e["dry_run"] for e in entries(runtime, "start") + entries(runtime, "current"))


@pytest.mark.parametrize("work_mode", [1, 2])
async def test_nothing_is_written_outside_the_work_mode_controlled(tmp_path, work_mode):
    async with charger_with_car(work_mode=work_mode) as (charger, port):
        _runtime, wallboxes, wb = setup(tmp_path, port)
        await check(wallboxes, wb, time.time())
        assert charger.writes == []


async def test_switching_control_off_stops_its_charge_and_simulates_again(tmp_path):
    async with charger_with_car() as (charger, port):
        runtime, wallboxes, wb = setup(tmp_path, port)
        await check(wallboxes, wb, time.time())
        assert charger.regs[0x1003] == 3
        await wallboxes.set_control(wb.id, False, source="admin")
        assert charger.writes[-1] == (0x4001, 2) and charger.restarted_by_current == 0
        wb = wallboxes.wallboxes[0]
        assert not wb.control and simulated(wb) and not wallboxes._owned()
        written = len(charger.writes)
        await check(wallboxes, wb, time.time())
        assert len(charger.writes) == written  # a simulation again
        assert entries(runtime, "control")[0]["details"]["to"]["value"] is False


async def test_a_planned_shutdown_stops_a_charge_openampere_started(tmp_path):
    async with charger_with_car() as (charger, port):
        _runtime, wallboxes, wb = setup(tmp_path, port)
        await check(wallboxes, wb, time.time())
        await wallboxes.release_real("OpenAmpere wird beendet")
        assert charger.writes[-1] == (0x4001, 2) and charger.regs[0x1003] == 4


async def test_switching_control_on_ends_the_simulation(tmp_path):
    async with charger_with_car() as (charger, port):
        runtime, wallboxes, wb = setup(tmp_path, port, control=False)
        await check(wallboxes, wb, time.time())
        assert charger.writes == [] and wallboxes.live[wb.id].plan.on  # only planned
        await wallboxes.set_control(wb.id, True, source="admin")
        wb = wallboxes.wallboxes[0]
        assert wb.control and not wallboxes.live[wb.id].plan.on
        await check(wallboxes, wb, time.time())
        assert charger.writes[-1] == (0x4001, 1)


def test_the_app_switches_it_on_only_when_logged_in(tmp_path, monkeypatch):
    from openampere import api as api_module
    from openampere.api import create_app

    class Wallboxes(NativeWallboxes):
        def refresh(self, now=None):  # no background reads from a test
            pass
    monkeypatch.setattr(api_module, "NativeWallboxes", Wallboxes)
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    web = login(TestClient(create_app(runtime), base_url="https://testserver"))
    saved = web.put("/api/wallboxes", json={"wallboxes": [{"name": "Garage", "model": "foxess_ac",
                                                           "host": "192.168.1.21"}]}).json()
    wb_id = saved["wallboxes"][0]["id"]
    guest = TestClient(create_app(runtime), base_url="https://testserver")
    assert guest.post(f"/api/wallboxes/{wb_id}/control", json={"on": True}).status_code in (401, 403)
    r = web.post(f"/api/wallboxes/{wb_id}/control", json={"on": True})
    assert r.status_code == 200, r.text
    fox = r.json()["wallboxes"][0]
    assert fox["control"] and not fox["simulated"] and not fox["read_only"]
    assert web.post("/api/wallboxes/nope/control", json={"on": True}).status_code == 400


async def test_saving_the_list_neither_switches_control_off_nor_on(tmp_path):
    async with charger_with_car() as (charger, port):
        _runtime, wallboxes, wb = setup(tmp_path, port)
        await check(wallboxes, wb, time.time())
        item = {k: v for k, v in wallboxes.view()["wallboxes"][0].items() if k != "control"}  # the app before #465
        wallboxes.save([{**item, "name": "Carport"}])
        wb = wallboxes.wallboxes[0]
        assert wb.control and wb.name == "Carport" and charger.regs[0x1003] == 3
        assert charger.writes[-1] == (0x4001, 1)  # nothing stopped
        wallboxes.save([{**item, "control": False}])
        assert wallboxes.wallboxes[0].control  # only set_control() switches it off

        runtime2 = Runtime({}, Storage(tmp_path / "u.db"))
        fresh = NativeWallboxes(runtime2)
        fresh.save([{"name": "Garage", "model": "foxess_ac", "host": "127.0.0.1", "port": port, "control": True}])
        assert not fresh.wallboxes[0].control  # never switched on without the question


async def test_nothing_is_written_when_the_fallback_current_does_not_read_back(tmp_path, monkeypatch):
    async with charger_with_car() as (charger, port):
        runtime, wallboxes, wb = setup(tmp_path, port)
        handle = charger.handle

        def keeps_its_fallback(pdu: bytes) -> bytes:
            answer = handle(pdu)
            charger.regs[0x3006] = 100  # 10 A, whatever is written
            return answer
        monkeypatch.setattr(charger, "handle", keeps_its_fallback)
        await check(wallboxes, wb, time.time())
        assert [a for a, _ in charger.writes] == [0x3005, 0x3006]  # no current, no start
        assert charger.regs[0x1003] == 1
        assert entries(runtime, "prepare")[0]["result"].startswith("Rücklesen abweichend")


async def test_test_mode_during_a_charge_openampere_started_stops_it(tmp_path):
    async with charger_with_car() as (charger, port):
        runtime, wallboxes, wb = setup(tmp_path, port)
        t0 = time.time()
        await check(wallboxes, wb, t0)
        assert charger.regs[0x1003] == 3
        runtime.config.control.dry_run = True
        await check(wallboxes, wb, t0 + 10)
        assert charger.writes[-1] == (0x4001, 2) and charger.regs[0x1003] == 4
        assert not wallboxes._owned()
        written = len(charger.writes)
        await check(wallboxes, wb, t0 + 60)
        assert len(charger.writes) == written  # test mode: nothing more


async def test_manual_stops_a_charge_openampere_started(tmp_path):
    async with charger_with_car() as (charger, port):
        _runtime, wallboxes, wb = setup(tmp_path, port)
        t0 = time.time()
        await check(wallboxes, wb, t0)
        wallboxes.set_mode(wb.id, "manual")
        await check(wallboxes, wallboxes.wallboxes[0], t0 + 10)
        assert charger.writes[-1] == (0x4001, 2) and charger.regs[0x1003] == 4
        assert not wallboxes._owned()


async def test_a_stop_after_a_reconnection_does_not_wait_for_the_fallback(tmp_path, monkeypatch):
    async with charger_with_car() as (charger, port):
        _runtime, wallboxes, wb = setup(tmp_path, port)
        await check(wallboxes, wb, time.time())
        live = wallboxes.live[wb.id]
        live.prepared, live.unprepared_at = False, time.time()  # back after an outage, the fallback not taken yet
        await wallboxes.release_real("OpenAmpere wird beendet")
        assert charger.writes[-1] == (0x4001, 2) and charger.regs[0x1003] == 4


async def test_after_unplugging_the_current_is_written_again_before_the_next_start(tmp_path):
    async with charger_with_car() as (charger, port):
        _runtime, wallboxes, wb = setup(tmp_path, port)
        t0 = time.time()
        await check(wallboxes, wb, t0)
        charger.regs.update({0x1003: 0, 0x1005: 0, 0x3001: 320})  # unplugged: the charger forgets the current
        await check(wallboxes, wb, t0 + 10)
        charger.plug()
        written = len(charger.writes)
        await check(wallboxes, wb, t0 + 20)
        assert charger.writes[written:] == [(0x3001, 160), (0x4001, 1)]


async def test_a_charge_openampere_started_is_stopped_when_the_fallback_fails_later(tmp_path, monkeypatch):
    async with charger_with_car() as (charger, port):
        runtime, wallboxes, wb = setup(tmp_path, port)
        t0 = time.time()
        await check(wallboxes, wb, t0)
        assert charger.regs[0x1003] == 3
        handle = charger.handle

        def keeps_its_fallback(pdu: bytes) -> bytes:
            answer = handle(pdu)
            charger.regs[0x3006] = 320  # after a reconnection the charger no longer takes 6 A
            return answer
        monkeypatch.setattr(charger, "handle", keeps_its_fallback)
        wallboxes.live[wb.id].prepared = False  # it answered again after missing some polls
        await check(wallboxes, wb, t0 + 30)  # the heartbeat finds the fallback not taken
        await check(wallboxes, wb, t0 + 40)
        assert charger.writes[-1] == (0x4001, 2) and charger.regs[0x1003] == 4
        assert not wallboxes._owned() and charger.restarted_by_current == 0


async def test_letting_go_right_after_the_start_stops_a_charger_that_is_still_starting(tmp_path):
    async with charger_with_car() as (charger, port):
        _runtime, wallboxes, wb = setup(tmp_path, port)
        await check(wallboxes, wb, time.time())
        charger.regs.update({0x1003: 2, 0x100B: 0, 0x100C: 0, 0x100D: 0, 0x100E: 0})  # start received, car not yet
        await wallboxes.release_real("OpenAmpere wird beendet")
        assert charger.writes[-1] == (0x4001, 2) and not wallboxes._owned()


async def test_letting_go_does_not_read_a_charger_that_waits_for_its_retry(tmp_path):
    async with charger_with_car() as (charger, port):
        _runtime, wallboxes, wb = setup(tmp_path, port)
        await check(wallboxes, wb, time.time())
        live = wallboxes.live[wb.id]
        live.retry_at, live.updated = time.time() + 60, time.time() - 600  # did not answer: asked again in a minute
        reads = charger.functions.count(3)
        assert not await wallboxes.release(wb, "Hauptschalter aus")  # nothing fresh to decide on: again later
        assert charger.functions.count(3) == reads and wallboxes._owned()


async def test_a_charger_that_resets_the_current_outside_a_charge_gets_it_once_more_when_charging(tmp_path):
    async with charger_with_car() as (charger, port):
        charger.resets_current = True
        runtime, wallboxes, wb = setup(tmp_path, port, mode="minpv")
        t0 = time.time()
        live = wallboxes.live.setdefault(wb.id, native_module.Live())
        live.start_sent = t0  # the car waits for its own timer: no start for a while
        await check(wallboxes, wb, t0, available=0)
        await check(wallboxes, wb, t0 + 10, available=0)
        await check(wallboxes, wb, t0 + 20, available=0)
        assert [w for w in charger.writes if w[0] == 0x3001] == [(0x3001, 60)]  # written once, not on every check
        assert not [e for e in entries(runtime, "current") if e["result"].startswith("Rücklesen")]
        live.start_sent = float("-inf")
        await check(wallboxes, wb, t0 + 30, available=0)  # started: at its own highest current for now
        assert charger.regs[0x1003] == 3
        await check(wallboxes, wb, t0 + 40, available=0)  # charging: the current written again at once and checked
        assert charger.writes[-1] == (0x3001, 60) and charger.regs[0x100B] == 60


async def test_a_charger_without_the_fallback_is_not_shown_as_away_and_says_why(tmp_path, monkeypatch):
    async with charger_with_car() as (charger, port):
        _runtime, wallboxes, wb = setup(tmp_path, port)
        handle = charger.handle

        def keeps_its_fallback(pdu: bytes) -> bytes:
            answer = handle(pdu)
            charger.regs[0x3006] = 100
            return answer
        monkeypatch.setattr(charger, "handle", keeps_its_fallback)
        await check(wallboxes, wb, time.time())
        live = wallboxes.live[wb.id]
        assert live.online is True and live.error is None and wallboxes.fresh(wb) is not None
        assert wallboxes.view()["wallboxes"][0]["not_prepared"] is True


async def test_a_work_mode_change_during_a_charge_openampere_started_lets_go(tmp_path):
    async with charger_with_car() as (charger, port):
        _runtime, wallboxes, wb = setup(tmp_path, port)
        t0 = time.time()
        await check(wallboxes, wb, t0)
        with pytest.raises(ValueError, match="„Gesteuert“ bleiben"):
            await wallboxes.set_work_mode(wb.id, "plug_and_charge")
        assert charger.regs[0x3000] == 0
        charger.regs[0x3000] = 1  # changed in the FoxESS app
        await check(wallboxes, wb, t0 + 10)
        assert charger.writes[-1] == (0x4001, 2) and not wallboxes._owned()


async def test_a_charger_with_a_lower_limit_of_its_own_is_not_asked_again_and_again(tmp_path):
    async with charger_with_car() as (charger, port):
        charger.limit = 100  # the installer limited it to 10 A
        runtime, wallboxes, wb = setup(tmp_path, port)
        t0 = time.time()
        await check(wallboxes, wb, t0)  # asks for 16 A before the start
        for i in range(1, 9):  # four minutes of charging
            await check(wallboxes, wb, t0 + 30 * i)
        differing = [e for e in runtime.storage.control_log(100) if e["result"].startswith("Rücklesen abweichend")]
        assert len(differing) <= 1  # said once, not every heartbeat
        assert charger.writes[-1] == (0x3001, 100) and wallboxes.live[wb.id].limit_a == 10



async def test_the_limit_of_the_charger_is_asked_again_after_a_while_and_with_another_mode(tmp_path):
    async with charger_with_car() as (charger, port):
        charger.limit = 100
        _runtime, wallboxes, wb = setup(tmp_path, port)
        t0 = time.time()
        for i in range(4):
            await check(wallboxes, wb, t0 + 30 * i)
        live = wallboxes.live[wb.id]
        assert live.limit_a == 10
        charger.limit = None  # e.g. the dimming of the grid operator ended
        live.limit_at = time.time() - native_module.LIMIT_AGAIN_S
        await check(wallboxes, wb, t0 + 200)
        assert charger.writes[-1] == (0x3001, 160) and live.limit_a is None
        charger.limit = 100
        await check(wallboxes, wb, t0 + 260)  # the heartbeat finds the lower current again
        assert live.limit_a == 10
        wallboxes.set_mode(wb.id, "minpv")
        assert wallboxes.live[wb.id].limit_a is None


# ---- phases (#478) ------------------------------------------------------------------------------------------------


async def test_solar_with_little_surplus_charges_on_one_phase_and_switches_after_the_interval(tmp_path):
    async with charger_with_car(switching=True) as (charger, port):
        runtime, wallboxes, wb = setup(tmp_path, port, mode="pv")
        t0 = time.time() - 7200
        for i in range(3):  # 2 kW: enough for one phase, not for three
            await check(wallboxes, wb, t0 + i * 10, available=2000)
        assert (0x3002, 41) in charger.writes and charger.writes[-1] == (0x4001, 1)
        assert charger.regs[0x100B] > 0 and charger.regs[0x100C] == 0  # one phase charges
        written = len(charger.writes)
        await check(wallboxes, wb, t0 + 60, available=9000)  # plenty, but within the switch interval (12 min)
        assert (0x3002, 220) not in charger.writes[written:]  # the heartbeat keeps one phase (41), no switch
        await check(wallboxes, wb, t0 + 60 + 12 * 60, available=9000)
        assert (0x3002, 220) in charger.writes[written:] and charger.regs[0x100C] > 0  # three phases now
        assert not [e for e in entries(runtime, "phases") if e["result"].startswith(("Fehler", "Rücklesen"))]
        assert [e["details"]["to"]["value"]["phases"] for e in entries(runtime, "phases")] == [3, 1]


async def test_fast_charges_on_three_phases_and_the_heartbeat_keeps_the_power(tmp_path):
    async with charger_with_car(switching=True) as (charger, port):
        _runtime, wallboxes, wb = setup(tmp_path, port)
        t0 = time.time()
        await check(wallboxes, wb, t0)
        assert charger.writes[2:] == [(0x3001, 160), (0x3002, 220), (0x4001, 1)]
        await check(wallboxes, wb, t0 + 30)
        assert charger.writes[-2:] == [(0x3001, 160), (0x3002, 220)]  # both refreshed


async def test_no_power_setpoint_after_a_stop_and_three_phases_back_on_letting_go(tmp_path):
    async with charger_with_car(switching=True) as (charger, port):
        _runtime, wallboxes, wb = setup(tmp_path, port, mode="pv")
        t0 = time.time() - 7200
        for i in range(3):
            await check(wallboxes, wb, t0 + i * 10, available=2000)
        assert charger.regs[0x3002] == 41
        await wallboxes.release_real("OpenAmpere wird beendet")
        assert charger.writes[-2:] == [(0x3002, 220), (0x4001, 2)]  # not left capped, then the stop
        written = len(charger.writes)
        for minute in range(1, 4):
            await check(wallboxes, wb, t0 + 30 + minute * 60, available=0)
        assert len(charger.writes) == written and charger.restarted_by_current == 0


async def test_without_automatic_switching_the_power_setpoint_is_left_alone(tmp_path):
    async with charger_with_car(switching=False) as (charger, port):
        _runtime, wallboxes, wb = setup(tmp_path, port, mode="pv")
        t0 = time.time() - 7200
        for i in range(3):
            await check(wallboxes, wb, t0 + i * 10, available=9000)
        assert charger.regs[0x1003] == 3 and not [w for w in charger.writes if w[0] == 0x3002]


async def test_test_mode_logs_a_planned_phase_switch_with_phases_and_current(tmp_path):
    async with charger_with_car(switching=True) as (charger, port):
        runtime, wallboxes, wb = setup(tmp_path, port, mode="pv", dry_run=True)
        t0 = time.time() - 7200
        for i in range(3):
            await check(wallboxes, wb, t0 + i * 10, available=2000)
        planned = entries(runtime, "phases")
        assert planned and planned[0]["dry_run"] and planned[0]["details"]["to"]["value"] == {"phases": 1, "current_a": 7}  # 1.8 kW
        assert charger.writes == []


async def test_a_stop_of_a_one_phase_charge_writes_the_highest_power_back_first(tmp_path):
    async with charger_with_car(switching=True) as (charger, port):
        _runtime, wallboxes, wb = setup(tmp_path, port, mode="pv")
        t0 = time.time() - 7200
        for i in range(3):
            await check(wallboxes, wb, t0 + i * 10, available=2000)
        assert charger.regs[0x3002] == 41
        wallboxes.set_mode(wb.id, "off")
        await check(wallboxes, wallboxes.wallboxes[0], t0 + 60, available=0)
        assert charger.writes[-2:] == [(0x3002, 220), (0x4001, 2)] and charger.restarted_by_current == 0


async def test_after_a_restart_letting_go_still_lifts_the_one_phase_cap(tmp_path):
    async with charger_with_car(switching=True) as (charger, port):
        runtime, wallboxes, wb = setup(tmp_path, port, mode="pv")
        t0 = time.time() - 7200
        for i in range(3):
            await check(wallboxes, wb, t0 + i * 10, available=2000)
        restarted = NativeWallboxes(runtime)  # the same database, nothing in memory
        rwb = restarted.wallboxes[0]
        await restarted.read(rwb)
        assert restarted._owned()[rwb.id]["phases"] == 1  # stored, so known after the restart
        await restarted.release(rwb, "Lademodus „von Hand“")
        assert charger.writes[-2:] == [(0x3002, 220), (0x4001, 2)]



async def test_a_power_limit_of_the_owner_is_kept(tmp_path):
    """#478: the owner limited the charger to 7 kW: three phases use that, and letting go puts back exactly that."""
    async with charger_with_car(switching=True) as (charger, port):
        charger.regs[0x3002] = 70
        _runtime, wallboxes, wb = setup(tmp_path, port, mode="pv")
        t0 = time.time() - 7200
        for i in range(3):
            await check(wallboxes, wb, t0 + i * 10, available=2000)
        assert charger.regs[0x3002] == 41
        await wallboxes.release_real("OpenAmpere wird beendet")
        assert charger.writes[-2:] == [(0x3002, 70), (0x4001, 2)]


async def test_without_switching_an_owner_limit_survives_stop_and_letting_go(tmp_path):
    async with charger_with_car(switching=False) as (charger, port):
        charger.regs[0x3002] = 70
        _runtime, wallboxes, wb = setup(tmp_path, port)
        t0 = time.time()
        await check(wallboxes, wb, t0)
        wallboxes.set_mode(wb.id, "off")
        await check(wallboxes, wallboxes.wallboxes[0], t0 + 10)
        await wallboxes.release_real("OpenAmpere wird beendet")
        assert not [w for w in charger.writes if w[0] == 0x3002] and charger.regs[0x3002] == 70


async def test_switching_turned_off_during_a_one_phase_charge_puts_the_power_back_once(tmp_path):
    async with charger_with_car(switching=True) as (charger, port):
        runtime, wallboxes, wb = setup(tmp_path, port, mode="pv")
        t0 = time.time() - 7200
        for i in range(3):
            await check(wallboxes, wb, t0 + i * 10, available=2000)
        charger.regs[0x300A] = 0  # the owner turned automatic switching off in the FoxESS app
        before = len(charger.writes)
        await check(wallboxes, wb, t0 + 40, available=2000)
        assert (0x3002, 220) in charger.writes[before:]
        written = len(charger.writes)
        for i in range(1, 4):
            await check(wallboxes, wb, t0 + 40 + 30 * i, available=2000)
        assert (0x3002, 41) not in charger.writes[written:] and not wallboxes._owned()[wb.id].get("phases")


async def test_an_owner_limit_below_three_phases_means_no_switching(tmp_path):
    async with charger_with_car(switching=True) as (charger, port):
        charger.regs[0x3002] = 37  # 3.7 kW, set by the owner for a weak supply
        _runtime, wallboxes, wb = setup(tmp_path, port, mode="pv")
        t0 = time.time() - 7200
        for i in range(3):
            await check(wallboxes, wb, t0 + i * 10, available=2000)
        assert not [w for w in charger.writes if w[0] == 0x3002] and charger.regs[0x3002] == 37


async def test_a_leftover_one_phase_cap_is_not_taken_as_the_owners_power(tmp_path):
    async with charger_with_car(switching=True) as (charger, port):
        charger.regs[0x3002] = 41  # left from an earlier control
        runtime, wallboxes, wb = setup(tmp_path, port)  # "Schnell": three phases
        await check(wallboxes, wb, time.time())
        assert (0x3002, 220) in charger.writes and wallboxes._owned()[wb.id]["power_w"] == 22000


async def test_the_switch_interval_holds_while_the_charger_switches(tmp_path):
    async with charger_with_car(switching=True) as (charger, port):
        _runtime, wallboxes, wb = setup(tmp_path, port, mode="pv")
        t0 = time.time() - 7200
        for i in range(3):
            await check(wallboxes, wb, t0 + i * 10, available=9000)  # three phases
        assert charger.regs[0x3002] == 220
        charger.regs.update({0x1003: 9, 0x100B: 0, 0x100C: 0, 0x100D: 0})  # switching, no power for a moment
        written = len(charger.writes)
        await check(wallboxes, wb, t0 + 40, available=4300)
        assert (0x3002, 41) not in charger.writes[written:]


async def test_letting_go_without_a_charge_still_lifts_the_own_cap(tmp_path):
    async with charger_with_car(switching=True) as (charger, port):
        charger.regs[0x3002] = 70  # the owner's 7 kW
        _runtime, wallboxes, wb = setup(tmp_path, port, mode="pv")
        t0 = time.time() - 7200
        for i in range(3):
            await check(wallboxes, wb, t0 + i * 10, available=2000)
        assert charger.regs[0x3002] == 41
        charger.regs.update({0x1003: 4, 0x100B: 0})  # the car paused by itself
        await wallboxes.release_real("OpenAmpere wird beendet")
        assert charger.writes[-2:] == [(0x3002, 70), (0x4001, 2)]  # the owner's power, then a stop: no restart
        assert charger.restarted_by_current == 0 and not wallboxes._owned()


async def test_a_failed_switch_to_three_phases_keeps_the_stored_cap(tmp_path, monkeypatch):
    async with charger_with_car(switching=True) as (charger, port):
        runtime, wallboxes, wb = setup(tmp_path, port, mode="pv")
        t0 = time.time() - 7200
        for i in range(3):
            await check(wallboxes, wb, t0 + i * 10, available=2000)
        handle = charger.handle

        def refuses_more_power(pdu: bytes) -> bytes:
            if pdu[0] == 16 and pdu[1:3] == bytes([0x30, 0x02]):
                return bytes([16 | 0x80, 4])
            return handle(pdu)
        monkeypatch.setattr(charger, "handle", refuses_more_power)
        await check(wallboxes, wb, t0 + 60 + 12 * 60, available=9000)  # wants three phases, the write fails
        assert wallboxes._owned()[wb.id]["phases"] == 1  # a restart now still knows the cap is OpenAmpere's


async def test_a_cap_that_does_not_go_back_on_letting_go_is_kept_and_tried_again(tmp_path, monkeypatch):
    async with charger_with_car(switching=True) as (charger, port):
        _runtime, wallboxes, wb = setup(tmp_path, port, mode="pv")
        t0 = time.time() - 7200
        for i in range(3):
            await check(wallboxes, wb, t0 + i * 10, available=2000)
        handle = charger.handle

        def ignores_power(pdu: bytes) -> bytes:  # answers the write but keeps 4.1 kW
            if pdu[0] == 16 and pdu[1:3] == bytes([0x30, 0x02]):
                return pdu[:5]
            return handle(pdu)
        monkeypatch.setattr(charger, "handle", ignores_power)
        await wallboxes.release_real("OpenAmpere wird beendet")
        note = wallboxes._owned()[wb.id]
        assert note["phases"] == 1 and not note["started"] and charger.regs[0x3002] == 41
        written = len(charger.writes)
        await wallboxes.release(wb, "Lademodus „von Hand“")  # too soon: nothing written
        assert len(charger.writes) == written
        monkeypatch.setattr(charger, "handle", handle)
        note["lift_tried"] = time.time() - native_module.PREPARE_AGAIN_S
        wallboxes.runtime.storage.set_meta(native_module.OWNED, {wb.id: note})
        await wallboxes.release(wb, "Lademodus „von Hand“")
        assert charger.regs[0x3002] == 220 and not wallboxes._owned()


async def test_switching_control_off_keeps_trying_to_lift_the_cap(tmp_path, monkeypatch):
    async with charger_with_car(switching=True) as (charger, port):
        _runtime, wallboxes, wb = setup(tmp_path, port, mode="pv")
        t0 = time.time() - 7200
        for i in range(3):
            await check(wallboxes, wb, t0 + i * 10, available=2000)
        handle = charger.handle

        def ignores_power(pdu: bytes) -> bytes:
            if pdu[0] == 16 and pdu[1:3] == bytes([0x30, 0x02]):
                return pdu[:5]
            return handle(pdu)
        monkeypatch.setattr(charger, "handle", ignores_power)
        await wallboxes.set_control(wb.id, False, source="admin")
        assert wb.id in wallboxes._retired and wallboxes._owned()[wb.id]["lift_tried"]
        assert wallboxes.view()["wallboxes"][0]["controlled"] is False  # only the note waits
        await wallboxes.retire()
        assert wb.id in wallboxes._retired  # too soon: still waiting, not given up
        monkeypatch.setattr(charger, "handle", handle)
        owned = wallboxes._owned()
        owned[wb.id]["lift_tried"] = time.time() - native_module.PREPARE_AGAIN_S
        wallboxes.runtime.storage.set_meta(native_module.OWNED, owned)
        await wallboxes.retire()
        assert charger.regs[0x3002] == 220 and not wallboxes._owned() and wb.id not in wallboxes._retired


async def test_a_failed_restore_after_switching_was_turned_off_is_not_repeated_every_poll(tmp_path, monkeypatch):
    async with charger_with_car(switching=True) as (charger, port):
        runtime, wallboxes, wb = setup(tmp_path, port, mode="pv")
        t0 = time.time() - 7200
        for i in range(3):
            await check(wallboxes, wb, t0 + i * 10, available=2000)
        handle = charger.handle

        tries = []

        def ignores_power(pdu: bytes) -> bytes:
            if pdu[0] == 16 and pdu[1:3] == bytes([0x30, 0x02]):
                tries.append(int.from_bytes(pdu[6:8], "big"))
                return pdu[:5]
            return handle(pdu)
        monkeypatch.setattr(charger, "handle", ignores_power)
        charger.regs[0x300A] = 0
        for i in range(6):  # three minutes
            await check(wallboxes, wb, t0 + 40 + 30 * i, available=2000)
        assert tries == [220]  # once, and the heartbeat no longer writes the cap
        assert wallboxes._owned()[wb.id]["phases"] == 1  # the release still lifts it later


async def test_a_phase_switch_in_progress_counts_as_a_running_charge(tmp_path):
    async with charger_with_car(switching=True) as (charger, port):
        _runtime, wallboxes, wb = setup(tmp_path, port, mode="minpv")
        t0 = time.time() - 7200
        await check(wallboxes, wb, t0, available=0)
        assert charger.regs[0x1003] == 3
        charger.regs.update({0x1003: 9, 0x100B: 0, 0x100C: 0, 0x100D: 0})  # switching phases
        before = len(charger.writes)
        await check(wallboxes, wb, t0 + 400, available=0)  # long after the start: a start would be due
        assert (0x4001, 1) not in charger.writes[before:]
        assert [w for w in charger.writes[before:] if w[0] == 0x3001]  # the heartbeat goes on


async def test_a_car_at_zero_watts_on_status_3_is_not_counted_as_charging(tmp_path):
    async with charger_with_car(switching=True) as (charger, port):
        _runtime, wallboxes, wb = setup(tmp_path, port, mode="minpv")
        t0 = time.time() - 7200
        await check(wallboxes, wb, t0, available=0)
        charger.regs.update({0x100B: 0, 0x100C: 0, 0x100D: 0, 0x100E: 0})  # status 3, the car draws nothing
        before = len(charger.writes)
        await check(wallboxes, wb, t0 + 30, available=0)
        assert not [w for w in charger.writes[before:] if w[0] in (0x3001, 0x3002)]  # no heartbeat to a car at 0 W


async def test_a_charger_that_resets_the_power_outside_a_charge_is_not_written_again_and_again(tmp_path):
    async with charger_with_car(switching=True) as (charger, port):
        charger.resets_power = True
        runtime, wallboxes, wb = setup(tmp_path, port, mode="minpv")
        live = wallboxes.live.setdefault(wb.id, native_module.Live())
        t0 = time.time() - 7200
        live.start_sent = t0 + 100  # the car waits for its own timer: no start for a while
        for i in range(6):
            await check(wallboxes, wb, t0 + 100 + i * 10, available=2000)
        assert len([w for w in charger.writes if w[0] == 0x3002]) == 1  # once, not every poll
        assert not [e for e in runtime.storage.control_log(100) if e["result"].startswith("Rücklesen")]


async def test_letting_go_during_a_phase_switch_sends_no_stop_to_the_owners_charge(tmp_path):
    async with charger_with_car(switching=True) as (charger, port):
        charger.regs.update({0x1003: 3, 0x100B: 160, 0x100C: 160, 0x100D: 160, 0x100E: 110})  # it charges by itself
        _runtime, wallboxes, wb = setup(tmp_path, port, mode="pv")
        t0 = time.time() - 7200
        await check(wallboxes, wb, t0, available=2000)  # too little for three phases: capped at one
        assert charger.regs[0x3002] == 41 and not wallboxes._owned()[wb.id]["started"]
        charger.regs.update({0x1003: 9, 0x100B: 0, 0x100C: 0, 0x100D: 0, 0x100E: 0})  # switching right now
        await wallboxes.release(wb, "Lademodus „von Hand“")
        assert charger.writes[-1] == (0x3002, 220) and (0x4001, 2) not in charger.writes


async def test_letting_go_of_a_paused_car_keeps_the_note_when_the_cap_does_not_go_back(tmp_path, monkeypatch):
    async with charger_with_car(switching=True) as (charger, port):
        _runtime, wallboxes, wb = setup(tmp_path, port, mode="pv")
        t0 = time.time() - 7200
        for i in range(3):
            await check(wallboxes, wb, t0 + i * 10, available=2000)
        charger.regs.update({0x1003: 4, 0x100B: 0})  # the car paused by itself
        handle = charger.handle

        def ignores_power(pdu: bytes) -> bytes:
            if pdu[0] == 16 and pdu[1:3] == bytes([0x30, 0x02]):
                return pdu[:5]
            return handle(pdu)
        monkeypatch.setattr(charger, "handle", ignores_power)
        assert not await wallboxes.release(wb, "Lademodus „von Hand“")
        assert wallboxes._owned()[wb.id]["phases"] == 1 and wallboxes._owned()[wb.id]["lift_tried"]


async def test_the_cap_note_keeps_openamperes_stop(tmp_path, monkeypatch):
    async with charger_with_car(switching=True) as (charger, port):
        _runtime, wallboxes, wb = setup(tmp_path, port, mode="pv")
        t0 = time.time() - 7200
        for i in range(3):
            await check(wallboxes, wb, t0 + i * 10, available=2000)
        handle = charger.handle

        def ignores_power(pdu: bytes) -> bytes:
            if pdu[0] == 16 and pdu[1:3] == bytes([0x30, 0x02]):
                return pdu[:5]
            return handle(pdu)
        monkeypatch.setattr(charger, "handle", ignores_power)
        assert not await wallboxes.release(wb, "Lademodus „von Hand“")  # the stop goes out, the power does not
        assert charger.writes[-1] == (0x4001, 2) and wallboxes._owned()[wb.id]["stopped"] is True
        charger.regs[0x1003] = 5  # "finished" after OpenAmpere's stop: not a full car
        monkeypatch.setattr(charger, "handle", handle)
        wallboxes.set_mode(wb.id, "now")
        wallboxes.live[wb.id].start_sent = float("-inf")
        await check(wallboxes, wallboxes.wallboxes[0], t0 + 3600, available=0)
        assert charger.writes[-1] == (0x4001, 1)  # started again
