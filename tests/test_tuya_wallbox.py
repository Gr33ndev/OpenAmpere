# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""The portable dé wallbox over Tuya LAN 3.5: protocol, data points, several wallboxes, offline behaviour and the
control within the solar surplus."""

import asyncio
import contextlib
import json
import logging

import pytest

from openampere import tuya
from openampere.consumers import SurplusControl
from openampere.drivers.base import Snapshot
from openampere.runtime import Runtime
from openampere.secretbox import SecretBox
from openampere.simulator import SimulatedTuyaCharger
from openampere.storage import Storage
from openampere.wallbox import native as native_module
from openampere.wallbox.de_tuya import DeTuyaCharger
from openampere.wallbox.native import NativeWallboxes, validate

KEY = "ab12cd34ef56gh78"


@contextlib.asynccontextmanager
async def simulated(**kwargs):
    charger = SimulatedTuyaCharger(KEY, **kwargs)
    server = await asyncio.start_server(charger.serve_client, "127.0.0.1", 0)
    try:
        yield charger, server.sockets[0].getsockname()[1]
    finally:
        server.close()
        await server.wait_closed()


def closed_port() -> int:
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def setup(tmp_path, monkeypatch, port: int, *, mode="pv", count=1) -> tuple[Runtime, NativeWallboxes]:
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    runtime.config.control.enabled, runtime.config.control.dry_run = True, False
    monkeypatch.setattr(type(runtime.collector), "stale", property(lambda self: False))
    wallboxes = NativeWallboxes(runtime)
    monkeypatch.setattr(wallboxes, "device", lambda wb: tuya.TuyaDevice(wb.host, wb.local_key, port=port, timeout=1))
    wallboxes.save([{"name": f"Wallbox {i + 1}", "host": "127.0.0.1", "local_key": KEY, "mode": mode}
                    for i in range(count)])
    return runtime, wallboxes


def site(runtime, grid, battery=0.0, soc=90.0):
    runtime.collector.latest = Snapshot(timestamp=0, grid_power=grid, battery_power=battery, battery_soc=soc)


# ---- protocol ---------------------------------------------------------------------------------------------------


async def test_status_and_writes_over_the_protocol():
    async with simulated(plugged=True) as (charger, port):
        device = tuya.TuyaDevice("127.0.0.1", KEY, port=port)
        dps = await device.status()
        assert dps["109"] == "IDLEINS" and dps["150"] == 16
        assert await device.set({"150": 10}) == {"150": 10}
        assert await device.set({"140": True}) == {}  # start is only acknowledged
        dps = await device.status()
        assert dps["109"] == "WORKING" and dps["150"] == 10
        assert charger.writes == [{"150": 10}, {"140": True}]
        assert (await device.set({"150": 8}, read_back=True))["150"] == 8  # read back over the same connection
        await asyncio.sleep(0.05)
        assert charger.connections == 5 and not charger.busy  # one connection per request, always closed


async def test_wrong_key_is_named_and_unreachable_is_its_own_error():
    async with simulated() as (_charger, port):
        with pytest.raises(tuya.TuyaRefused, match="Local Key"):  # the device just closes the connection
            await tuya.TuyaDevice("127.0.0.1", "x" * 16, port=port, timeout=1).status()
    with pytest.raises(tuya.TuyaUnreachable):
        await tuya.TuyaDevice("127.0.0.1", KEY, port=closed_port(), timeout=1).status()
    with pytest.raises(tuya.TuyaError):
        tuya.TuyaDevice("127.0.0.1", "kurz")


async def test_a_second_connection_while_another_tool_holds_the_device_is_unreachable():
    async with simulated() as (charger, port):
        charger.busy = True  # e.g. the owner's own script is connected
        with pytest.raises(tuya.TuyaUnreachable):
            await tuya.TuyaDevice("127.0.0.1", KEY, port=port, timeout=1).status()


def test_announcements_are_decoded():
    info = {"ip": "192.0.2.20", "gwId": "simulated0000000000", "version": "3.5", "productKey": "abc"}
    frame = tuya.pack(tuya.DISCOVERY_KEY, 0, 0x13, b"\x00\x00\x00\x00" + json.dumps(info).encode())
    assert tuya.decode_announcement(frame)["gwId"] == "simulated0000000000"
    assert tuya.decode_announcement(b"\x00\x00\x55\xaa" + bytes(40)) is None


# ---- data points (values as an owner read them, rounded) -----------------------------------------------------------


def test_idle_and_charging_data_points():
    idle = DeTuyaCharger.interpret({
        "102": '{"L1":[2350,0,0],"L2":[2350,0,0],"L3":[2350,0,0],"t":220,"p":0,"d":0,"e":0}',
        "106": '{"r":"Type B","fv":"1.0.0","cp":"12.6","t":"230","e":"3000"}', "107": "[6, 8, 10, 13, 16]",
        "109": "SLEEP", "150": 13, "152": 16})
    assert (idle.state, idle.connected, idle.charging, idle.power_w) == ("idle", False, False, 0.0)
    assert idle.installed_phases == 3 and idle.steps_a == [6, 8, 10, 13, 16] and idle.set_current_a == 13
    charging = DeTuyaCharger.interpret({
        "102": '{"L1":[2320,74,17],"L2":[2320,72,16],"L3":[2320,72,16],"t":210,"p":51,"d":620,"e":0}',
        "106": '{"fv":"1.0.0","cp":"6.6","e":"3000"}', "107": "[6, 8, 10, 13, 16]", "109": "WORKING", "150": 8,
        "152": 16})
    assert charging.connected and charging.charging and charging.active_phases == 3
    assert 4900 < charging.power_w < 5200  # the car's app showed about 5 kW
    assert charging.current_a == [7.4, 7.2, 7.2] and charging.session_s == 62
    # some firmware keeps the last current after charging: not shown as charging
    stale = DeTuyaCharger.interpret({"102": '{"L1":[2300,74,17]}', "109": "STOP", "106": '{"cp":"9.0"}'})
    assert stale.connected and not stale.charging and stale.power_w == 0 and stale.current_a[0] == 0.0
    assert DeTuyaCharger.interpret({"102": "kein json", "107": "[]"}).steps_a == [6, 8, 10, 13, 16]


# ---- several wallboxes, settings ------------------------------------------------------------------------------


def test_validation_of_several_wallboxes():
    good = {"name": "Garage", "host": "192.168.1.20", "local_key": KEY}
    assert len(validate([good, {**good, "name": "Carport"}])) == 2
    for bad in ({**good, "local_key": "kurz"}, {**good, "host": "http://x"}, {**good, "max_current_a": 40},
                {**good, "mode": "turbo"}, {**good, "device_id": "a b"}, {**good, "name": " "}):
        with pytest.raises(ValueError):
            validate([bad])
    with pytest.raises(ValueError):
        validate([good] * 5)
    ids = {wb.id for wb in validate([{**good, "id": "same0000"}, {**good, "id": "same0000"}])}
    assert len(ids) == 2  # every wallbox keeps its own id
    # a stored wallbox whose key could not be read may stay without one, a new one may not
    assert validate([{**good, "id": "ab12cd34", "local_key": ""}], keyless=("ab12cd34",))[0].local_key == ""
    with pytest.raises(ValueError, match="Local Key"):
        validate([{**good, "id": "ab12cd34", "local_key": ""}])


def test_the_local_key_is_stored_encrypted_and_never_shown(tmp_path):
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    wallboxes = NativeWallboxes(runtime)
    saved = wallboxes.save([{"name": "Garage", "host": "192.168.1.20", "local_key": KEY, "device_id": "abc123def456"}])
    stored = runtime.storage.get_meta("wallboxes")[0]
    assert KEY not in json.dumps(stored) and stored["local_key"].startswith("enc:")
    view = wallboxes.view(hide_device_id=True)["wallboxes"][0]
    assert "local_key" not in view and view["local_key_set"] and view["device_id"] is None
    # the app shows the key fields and the steps to the key for a Tuya wallbox only
    tuya_model = wallboxes.view()["models"][0]
    assert {k: tuya_model[k] for k in ("key", "label", "protocol", "read_only")} == {
        "key": "de_tuya", "label": "dé Mobile Wallbox 11 kW (Tuya)", "protocol": "tuya", "read_only": False}
    # the app sends the key empty: the stored one stays
    wallboxes.save([{"id": saved[0].id, "name": "Garage 2", "host": "192.168.1.20", "local_key": ""}])
    assert wallboxes.wallboxes[0].local_key == KEY and wallboxes.wallboxes[0].name == "Garage 2"
    with pytest.raises(ValueError):
        wallboxes.save([{"name": "Draußen", "host": "8.8.8.8", "local_key": KEY}])  # not in the home network


async def test_a_wallbox_whose_key_cannot_be_read_after_a_restore_stays_and_waits_for_a_new_one(
        tmp_path, monkeypatch, caplog):
    """A backup restored on another server: the stored local key was encrypted with another secret.key (#391)."""
    async with simulated(plugged=True) as (charger, port):
        charger.current_a = 13  # the owner's current
        runtime, wallboxes = setup(tmp_path, monkeypatch, port, mode="now")
        site(runtime, grid=0)
        wb = wallboxes.wallboxes[0]
        await wallboxes.read(wb)
        await SurplusControl(runtime, None, wallboxes).tick(now=0)
        assert charger.charging and charger.current_a == 16
        wallboxes.save([{**vars(wb), "local_key": "", "max_current_a": 10}])

        runtime.secrets = SecretBox(tmp_path / "other" / "secret.key")  # the new server
        caplog.set_level(logging.WARNING)
        restored = NativeWallboxes(runtime)
        monkeypatch.setattr(restored, "device", lambda w: tuya.TuyaDevice(w.host, w.local_key, port=port, timeout=1))
        control = SurplusControl(runtime, None, restored)
        kept = restored.wallboxes[0]
        assert (kept.id, kept.name, kept.host, kept.mode, kept.max_current_a) == (wb.id, wb.name, wb.host, "now", 10)
        assert kept.local_key == "" and not restored.view()["wallboxes"][0]["local_key_set"]
        assert not restored.report()[0]["local_key_set"]
        # neither read nor controlled
        connections, writes = charger.connections, len(charger.writes)
        for now in (10, 20):
            restored.refresh()
            await restored.wait()
            await control.tick(now=now)
        assert charger.connections == connections and len(charger.writes) == writes and not restored.live.get(wb.id)
        assert sum("cannot be read" in r.message for r in caplog.records) == 1  # logged once, not on every read
        # changed in the app without a key (mode, switch): it stays and keeps waiting
        restored.set_mode(wb.id, "pv")
        restored.save([{**restored.view()["wallboxes"][0], "local_key": "", "enabled": True}])
        assert restored.wallboxes[0].mode == "pv" and restored.wallboxes[0].local_key == ""

        # the key entered again: read and controlled under the same id, the owner's current is still known
        restored.save([{**restored.view()["wallboxes"][0], "local_key": KEY, "mode": "now"}])
        assert restored.wallboxes[0].local_key == KEY and restored.wallboxes[0].id == wb.id
        assert not restored._retired and runtime.storage.get_meta("wallbox_owned")[wb.id]["current_a"] == 13
        restored.refresh()
        await restored.wait()
        assert restored.view()["wallboxes"][0]["online"]
        restored.set_mode(wb.id, "manual")
        await restored.read(restored.wallboxes[0])
        await control.tick(now=30)
        assert charger.current_a == 13 and runtime.storage.get_meta("wallbox_owned") == {}  # let go of again


async def test_a_wallbox_removed_on_the_new_server_is_let_go_of_with_the_key_entered_again(tmp_path, monkeypatch):
    """Owned before the restore, switched off in the app afterwards: the key entered then is used to let go of it."""
    async with simulated(plugged=True) as (charger, port):
        charger.current_a = 13
        runtime, wallboxes = setup(tmp_path, monkeypatch, port, mode="now")
        site(runtime, grid=0)
        await wallboxes.read(wallboxes.wallboxes[0])
        await SurplusControl(runtime, None, wallboxes).tick(now=0)
        assert charger.current_a == 16

        runtime.secrets = SecretBox(tmp_path / "other" / "secret.key")
        restored = NativeWallboxes(runtime)
        monkeypatch.setattr(restored, "device", lambda w: tuya.TuyaDevice(w.host, w.local_key, port=port, timeout=1))
        control = SurplusControl(runtime, None, restored)
        await control.tick(now=10)
        restored.save([{**restored.view()["wallboxes"][0], "local_key": KEY, "enabled": False}])
        await control.tick(now=20)
        assert charger.current_a == 13 and not charger.charging and runtime.storage.get_meta("wallbox_owned") == {}


# ---- offline -------------------------------------------------------------------------------------------------------


async def test_an_unplugged_wallbox_is_shown_as_away_asked_less_often_and_logged_once(tmp_path, monkeypatch, caplog):
    port = closed_port()
    runtime, wallboxes = setup(tmp_path, monkeypatch, port, count=2)
    caplog.set_level(logging.INFO, logger="openampere.wallbox.native")
    for _ in range(3):
        for live in wallboxes.live.values():
            live.retry_at = 0.0
        wallboxes.refresh()
        await wallboxes.wait()
    first = wallboxes.wallboxes[0]
    live = wallboxes.live[first.id]
    assert live.online is False and live.failures == 3 and live.error
    assert live.retry_at - live.offline_since >= 50  # 15 s, 30 s, 60 s, … up to 5 minutes
    assert sum("not reachable" in r.message for r in caplog.records) == 2  # once per wallbox
    view = wallboxes.view()["wallboxes"][0]
    assert view["online"] is False and view["reading"] is None and view["offline_since"]
    # the surplus control does not wait for it and writes nothing
    site(runtime, grid=-8000)
    control = SurplusControl(runtime, None, wallboxes)
    await asyncio.wait_for(control.tick(), 2)
    # plugged in again: read and shown right away, logged once
    async with simulated(plugged=True) as (_charger, new_port):
        monkeypatch.setattr(wallboxes, "device",
                            lambda wb: tuya.TuyaDevice(wb.host, wb.local_key, port=new_port, timeout=1))
        live.retry_at, live.start_sent = 0.0, 1000.0
        await wallboxes.read(first)
    assert live.online and live.error is None and wallboxes.fresh(first).connected
    assert live.start_sent == float("-inf")  # a wallbox back after a power cut is started again at once
    assert any("answers again" in r.message for r in caplog.records)


async def test_refresh_never_waits_for_a_wallbox(tmp_path, monkeypatch):
    _runtime, wallboxes = setup(tmp_path, monkeypatch, closed_port())

    async def slow(*_args, **_kwargs):
        await asyncio.sleep(30)
    monkeypatch.setattr(tuya.TuyaDevice, "status", slow)
    wallboxes.refresh()  # returns at once
    task = next(iter(wallboxes.live.values())).task
    assert task is not None and not task.done()
    wallboxes.refresh()  # a read is still running: no second one
    task.cancel()


# ---- control -------------------------------------------------------------------------------------------------------


async def test_solar_mode_starts_with_surplus_and_stops_without(tmp_path, monkeypatch):
    async with simulated(plugged=True) as (charger, port):
        charger.current_a = 13  # the owner's current
        runtime, wallboxes = setup(tmp_path, monkeypatch, port)
        control = SurplusControl(runtime, None, wallboxes)
        wb = wallboxes.wallboxes[0]
        monkeypatch.setattr(native_module, "MIN_OFF_S", 0)
        site(runtime, grid=-6000)  # 6 kW export: 8 A on three phases fit (5.5 kW), 10 A not
        await wallboxes.read(wb)
        await control.tick(now=1000)
        assert charger.writes == []  # two checks in a row (clouds)
        await wallboxes.read(wb)
        await control.tick(now=1010)
        assert charger.writes == [{"150": 8}, {"140": True}] and charger.charging
        assert runtime.storage.get_meta("wallbox_owned")[wb.id]["current_a"] == 13

        site(runtime, grid=1000)  # clouds: the car draws 5.5 kW, 1 kW come from the grid
        for now in (1020, 1030):
            await wallboxes.read(wb)
            await control.tick(now=now)
        assert charger.charging and charger.current_a == 6  # down to the lowest step, minimum on time
        for now in (1400, 1620):  # through clouds for the waiting time, 10 minutes by default (#402)
            await wallboxes.read(wb)
            await control.tick(now=now)
            assert charger.charging and charger.current_a == 6
        await wallboxes.read(wb)
        await control.tick(now=1630)  # 10 minutes after the first check without enough surplus for 6 A
        assert not charger.charging and charger.writes[-1] == {"140": False}
        log = runtime.storage.control_log()
        assert {e["action"] for e in log} == {"wallbox"} and not any(e["dry_run"] for e in log)


async def test_solar_mode_starts_again_when_the_wallbox_shows_stop_after_its_own_stop(tmp_path, monkeypatch):
    """The dé wallbox may report STOP ("complete") also after a stop by OpenAmpere (#454): that is no full car."""
    async with simulated(plugged=True, stopped_shows="STOP") as (charger, port):
        runtime, wallboxes = setup(tmp_path, monkeypatch, port)
        control = SurplusControl(runtime, None, wallboxes)
        wb = wallboxes.wallboxes[0]
        monkeypatch.setattr(native_module, "MIN_OFF_S", 0)
        site(runtime, grid=-6000)
        for now in (1000, 1010):
            await wallboxes.read(wb)
            await control.tick(now=now)
        assert charger.charging
        site(runtime, grid=3000)  # a cloud: not even 6 A fit, stopped after the waiting time
        for now in (1020, 1030, 1640):
            await wallboxes.read(wb)
            await control.tick(now=now)
        assert not charger.charging and charger.dps()["109"] == "STOP"

        # a restart in between: the stop is remembered with the wallbox
        wallboxes = NativeWallboxes(runtime)
        monkeypatch.setattr(wallboxes, "device", lambda wb: tuya.TuyaDevice(wb.host, wb.local_key, port=port, timeout=1))
        control = SurplusControl(runtime, None, wallboxes)
        wb = wallboxes.wallboxes[0]
        site(runtime, grid=-6000)  # the sun is back
        await wallboxes.read(wb)
        await control.tick(now=2000)
        await wallboxes.read(wb)

        class Busy:  # the dé app holds the only connection: the start does not reach the wallbox
            async def set(self, values, read_back=True):
                raise tuya.TuyaUnreachable("belegt")

        monkeypatch.setattr(wallboxes, "device", lambda wb: Busy())
        await control.tick(now=2010)
        monkeypatch.setattr(wallboxes, "device", lambda wb: tuya.TuyaDevice(wb.host, wb.local_key, port=port, timeout=1))
        assert not charger.charging and runtime.storage.get_meta("wallbox_owned")[wb.id]["stopped"]
        for now in range(2020, 2400, 10):  # asked again after a while
            await wallboxes.read(wb)
            await control.tick(now=now)
        assert charger.charging and charger.writes.count({"140": True}) == 2
        assert not runtime.storage.get_meta("wallbox_owned")[wb.id]["stopped"]


async def test_a_stop_sent_in_test_mode_is_noted_too(tmp_path, monkeypatch):
    """Test mode still stops a charge OpenAmpere started (#454): a STOP after it is no full car either."""
    async with simulated(plugged=True, stopped_shows="STOP") as (charger, port):
        runtime, wallboxes = setup(tmp_path, monkeypatch, port)
        control = SurplusControl(runtime, None, wallboxes)
        wb = wallboxes.wallboxes[0]
        monkeypatch.setattr(native_module, "MIN_OFF_S", 0)
        site(runtime, grid=-6000)
        for now in (1000, 1010):
            await wallboxes.read(wb)
            await control.tick(now=now)
        assert charger.charging
        runtime.config.control.dry_run = True
        site(runtime, grid=3000)  # a cloud: not even 6 A fit, stopped after the waiting time
        for now in (1020, 1030, 1640):
            await wallboxes.read(wb)
            await control.tick(now=now)
        assert not charger.charging and charger.dps()["109"] == "STOP"
        assert runtime.storage.get_meta("wallbox_owned")[wb.id]["stopped"]
        runtime.config.control.dry_run = False
        site(runtime, grid=-6000)
        for now in (2000, 2010):
            await wallboxes.read(wb)
            await control.tick(now=now)
        assert charger.charging and charger.writes.count({"140": True}) == 2


async def test_solar_mode_never_starts_a_full_car(tmp_path, monkeypatch):
    """A wallbox complete without a stop by OpenAmpere since the car last charged: the car is full."""
    async with simulated(plugged=True, stopped_shows="STOP") as (charger, port):
        runtime, wallboxes = setup(tmp_path, monkeypatch, port)
        control = SurplusControl(runtime, None, wallboxes)
        wb = wallboxes.wallboxes[0]
        monkeypatch.setattr(native_module, "MIN_OFF_S", 0)
        site(runtime, grid=-6000)
        for now in (1000, 1010):
            await wallboxes.read(wb)
            await control.tick(now=now)
        site(runtime, grid=3000)  # a cloud: not even 6 A fit, stopped after the waiting time
        for now in (1020, 1030, 1640):  # stopped by OpenAmpere at a cloud ...
            await wallboxes.read(wb)
            await control.tick(now=now)
        charger.apply({"140": True})  # ... started again by the owner, until the car is full
        await wallboxes.read(wb)
        await control.tick(now=1650)
        charger.full = True
        starts = charger.writes.count({"140": True})
        site(runtime, grid=-6000)
        for now in range(1660, 3500, 10):
            await wallboxes.read(wb)
            await control.tick(now=now)
        assert charger.dps()["109"] == "STOP" and charger.writes.count({"140": True}) == starts


async def test_fast_mode_uses_the_owners_limit_and_the_steps_of_the_wallbox(tmp_path, monkeypatch):
    async with simulated(plugged=True, steps=(6, 8, 10, 13, 16)) as (charger, port):
        charger.current_a = 6
        runtime, wallboxes = setup(tmp_path, monkeypatch, port, mode="now")
        settings = [{**{k: v for k, v in vars(w).items()}, "max_current_a": 12} for w in wallboxes.wallboxes]
        wallboxes.save(settings)
        site(runtime, grid=500)
        await wallboxes.read(wallboxes.wallboxes[0])
        await SurplusControl(runtime, None, wallboxes).tick(now=0)
        assert charger.writes == [{"150": 10}, {"140": True}]  # 13 A is above the owner's 12 A


async def test_test_mode_only_logs_and_letting_go_restores_the_owners_current(tmp_path, monkeypatch):
    async with simulated(plugged=True) as (charger, port):
        charger.current_a = 13
        runtime, wallboxes = setup(tmp_path, monkeypatch, port, mode="now")
        runtime.config.control.dry_run = True
        control = SurplusControl(runtime, None, wallboxes)
        wb = wallboxes.wallboxes[0]
        site(runtime, grid=0)
        for now in (0, 10, 20):
            await wallboxes.read(wb)
            await control.tick(now=now)
        assert charger.writes == []
        log = runtime.storage.control_log()
        assert log and all(e["dry_run"] for e in log) and len(log) == 2  # current and start, each once

        runtime.config.control.dry_run = False
        await wallboxes.read(wb)
        await control.tick(now=30)
        assert charger.writes == [{"150": 16}, {"140": True}] and charger.charging
        runtime.config.control.enabled = False  # master switch off: OpenAmpere lets go
        await wallboxes.read(wb)
        await control.tick(now=40)
        assert not charger.charging and charger.current_a == 13  # its start undone, the owner's current back
        assert runtime.storage.get_meta("wallbox_owned") == {}


async def test_manual_mode_lets_go_but_keeps_charging(tmp_path, monkeypatch):
    async with simulated(plugged=True) as (charger, port):
        charger.current_a = 13
        runtime, wallboxes = setup(tmp_path, monkeypatch, port, mode="now")
        control = SurplusControl(runtime, None, wallboxes)
        wb = wallboxes.wallboxes[0]
        site(runtime, grid=0)
        await wallboxes.read(wb)
        await control.tick(now=0)
        assert charger.charging and charger.current_a == 16
        wallboxes.set_mode(wb.id, "manual")
        await wallboxes.read(wb)
        await control.tick(now=10)
        assert charger.charging and charger.current_a == 13  # the owner takes over
        await wallboxes.read(wb)
        writes = len(charger.writes)
        await control.tick(now=20)
        assert len(charger.writes) == writes  # "manual": never written again


async def test_a_removed_wallbox_is_let_go_of_when_it_answers(tmp_path, monkeypatch):
    async with simulated(plugged=True) as (charger, port):
        charger.current_a = 10
        runtime, wallboxes = setup(tmp_path, monkeypatch, port, mode="now")
        control = SurplusControl(runtime, None, wallboxes)
        site(runtime, grid=0)
        await wallboxes.read(wallboxes.wallboxes[0])
        await control.tick(now=0)
        assert charger.charging
        wallboxes.save([])
        # after a restart, too: the owned wallbox is known from the meta
        again = NativeWallboxes(runtime)
        monkeypatch.setattr(again, "device", lambda wb: tuya.TuyaDevice(wb.host, wb.local_key, port=port, timeout=1))
        await SurplusControl(runtime, None, again).tick(now=10)
        assert not charger.charging and charger.current_a == 10
        assert runtime.storage.get_meta("wallbox_owned") == {}


async def test_two_wallboxes_share_the_surplus_in_their_order(tmp_path, monkeypatch):
    async with simulated(plugged=True) as (first, port1), simulated(plugged=True) as (second, port2):
        runtime, wallboxes = setup(tmp_path, monkeypatch, port1, mode="pv", count=2)
        ports = dict(zip([w.id for w in wallboxes.wallboxes], (port1, port2), strict=True))
        monkeypatch.setattr(wallboxes, "device",
                            lambda wb: tuya.TuyaDevice(wb.host, wb.local_key, port=ports[wb.id], timeout=1))
        monkeypatch.setattr(native_module, "MIN_OFF_S", 0)
        control = SurplusControl(runtime, None, wallboxes)
        site(runtime, grid=-6000)  # enough for one car at 8 A, not for two
        for now in (0, 10):
            for wb in wallboxes.wallboxes:
                await wallboxes.read(wb)
            await control.tick(now=now)
        assert first.charging and not second.charging


async def test_the_report_has_no_address_device_id_key_or_counter(tmp_path, monkeypatch):
    from openampere.diagnostics import Diagnostics, report_markdown
    async with simulated(plugged=True) as (_charger, port):
        runtime, wallboxes = setup(tmp_path, monkeypatch, port)
        settings = [{**vars(w), "host": "192.168.178.77", "device_id": "bfsimulated12345678"}
                    for w in wallboxes.wallboxes]
        wallboxes.save(settings)
        monkeypatch.setattr(wallboxes, "device", lambda wb: tuya.TuyaDevice("127.0.0.1", wb.local_key, port=port))
        await wallboxes.read(wallboxes.wallboxes[0])
        logging.getLogger("openampere.wallbox.native").warning("wallbox at 192.168.178.77 bfsimulated12345678 %s", KEY)
        diagnostics = Diagnostics(runtime)
        diagnostics.wallboxes = wallboxes
        report = await diagnostics.run()
        text = report_markdown(report) + json.dumps(report)
    assert "Wallbox 1 (direkt)" in text and "de_tuya" in text
    for private in ("192.168.178.77", "bfsimulated12345678", KEY, "lifetime_kwh"):
        assert private not in text


# ---- the search, and both ways to run: standalone (host network) and the Home Assistant app ----------------------


def announcement(ip: str, version: str = "3.5") -> bytes:
    info = {"ip": ip, "gwId": "simulated0000000000", "version": version, "productKey": "abc"}
    return tuya.pack(tuya.DISCOVERY_KEY, 0, 0x13, b"\x00\x00\x00\x00" + json.dumps(info).encode())


def test_an_announcement_counts_only_from_the_address_it_names():
    assert tuya.announced(announcement("192.168.1.50"), "192.168.1.50")["host"] == "192.168.1.50"
    assert tuya.announced(announcement("192.168.1.99"), "192.168.1.50") is None  # sent by another device
    assert tuya.announced(announcement("8.8.8.8"), "8.8.8.8") is None  # not in the home network
    assert tuya.announced(announcement("192.168.1.50", "3.3"), "192.168.1.50") is None
    assert tuya.announced(b"garbage", "192.168.1.50") is None


async def test_the_search_receives_announcements_and_drops_forged_ones():
    port = closed_port()
    # bound to 127.0.0.1 here so the test needs no network; the app binds to the broadcast addresses
    search = asyncio.create_task(tuya.search(0.5, ports=(port,), addresses=("127.0.0.1",), ask=False))
    await asyncio.sleep(0.1)
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
        sender.sendto(announcement("127.0.0.1"), ("127.0.0.1", port))
        sender.sendto(announcement("192.168.1.99"), ("127.0.0.1", port))  # forged: names another address
    found = await search
    assert [d["host"] for d in found] == ["127.0.0.1"]


async def test_the_search_never_listens_on_all_interfaces(monkeypatch):
    """Only broadcasts reach it: nothing sent to this computer's own address (CodeQL: bind to all interfaces)."""
    bound = []

    class Spy(tuya.socket.socket):
        def bind(self, address):
            bound.append(address[0])
            super().bind(("127.0.0.1", 0))  # no real broadcast port in a test
    monkeypatch.setattr(tuya.socket, "socket", Spy)
    await tuya.search(0.01)
    assert bound and tuya.LIMITED_BROADCAST in bound
    assert not {"", "0.0.0.0"} & set(bound)


async def test_the_search_is_unavailable_when_no_port_can_be_opened():
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as taken:
        taken.bind(("127.0.0.1", 0))
        with pytest.raises(OSError):  # the app then says the search is not available
            await tuya.search(0.01, ports=(taken.getsockname()[1],), addresses=("127.0.0.1",), ask=False)


@pytest.mark.parametrize("ha_app_mode", [False, True], ids=["standalone", "home-assistant-app"])
def test_setup_works_in_both_ways_to_run(tmp_path, monkeypatch, ha_app_mode):
    """Reading over TCP works in both; the search needs the host network and is off in the Home Assistant app."""
    from fastapi.testclient import TestClient

    from conftest import login
    from openampere import api as api_module
    from openampere import ha_app
    from openampere.api import create_app

    if ha_app_mode:
        from test_ha_app import USERS
        monkeypatch.setenv("SUPERVISOR_TOKEN", "test-token")

        async def users():
            return USERS
        monkeypatch.setattr(ha_app, "home_assistant_users", users)
    else:
        monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    searched = []

    async def fake_search(*_args, **_kwargs):
        searched.append(True)
        return [{"device_id": "simulated0000000000", "host": "192.168.1.50", "product_key": None}]
    monkeypatch.setattr(api_module, "tuya_search", fake_search)

    async def run():
        async with simulated(plugged=True) as (_charger, port):
            monkeypatch.setattr(api_module, "TuyaDevice", lambda host, key: tuya.TuyaDevice(host, key, port=port))
            runtime = Runtime({}, Storage(tmp_path / "t.db"))
            if ha_app_mode:  # through the sidebar of Home Assistant, as an admin
                client = TestClient(create_app(runtime), client=(ha_app.INGRESS_PROXY, 50000),
                                    headers={"x-openampere": "1", ha_app.USER_HEADER: "admin"})
            else:
                client = login(TestClient(create_app(runtime), client=("192.0.2.10", 50000)))
            with client:
                assert client.get("/api/wallboxes").json()["search"] is not ha_app_mode
                found = client.post("/api/wallboxes/search").json()
                assert found["available"] is not ha_app_mode and bool(searched) is not ha_app_mode
                answer = await asyncio.to_thread(client.post, "/api/wallboxes/test",
                                                 json={"host": "127.0.0.1", "local_key": KEY})
                assert answer.status_code == 200 and answer.json()["reading"]["connected"]
                saved = client.put("/api/wallboxes", json={"wallboxes": [
                    {"name": "Mobil", "host": "127.0.0.1", "local_key": KEY}]})
                assert saved.status_code == 200 and saved.json()["wallboxes"][0]["local_key_set"]
                assert ha_app.active() is ha_app_mode
    asyncio.run(run())


async def test_the_home_assistant_app_never_searches_for_a_wallbox_that_went_away(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPERVISOR_TOKEN", "test-token")
    _runtime, wallboxes = setup(tmp_path, monkeypatch, closed_port())
    wallboxes.save([{**vars(w), "device_id": "simulated0000000000"} for w in wallboxes.wallboxes])
    calls = []

    async def fake_search(*_args, **_kwargs):
        calls.append(True)
        return []
    monkeypatch.setattr(tuya, "search", fake_search)
    wb = wallboxes.wallboxes[0]
    live = native_module.Live(online=False, offline_since=0.0)
    await wallboxes.read(wb, live)
    assert live.online is False and calls == []
    monkeypatch.delenv("SUPERVISOR_TOKEN")
    await wallboxes.read(wb, live)  # standalone: searched after two minutes away
    assert calls == [True]


async def test_sun_coming_back_within_the_waiting_time_keeps_the_charge(tmp_path, monkeypatch):
    async with simulated(plugged=True) as (charger, port):
        runtime, wallboxes = setup(tmp_path, monkeypatch, port)
        wallboxes.save([{**vars(w), "stop_after_min": 3} for w in wallboxes.wallboxes])
        control = SurplusControl(runtime, None, wallboxes)
        wb = wallboxes.wallboxes[0]
        monkeypatch.setattr(native_module, "MIN_OFF_S", 0)
        monkeypatch.setattr(native_module, "MIN_ON_S", 0)
        site(runtime, grid=-6000)
        for now in (0, 10):
            await wallboxes.read(wb)
            await control.tick(now=now)
        assert charger.charging

        site(runtime, grid=3000)  # a cloud
        for now in (20, 150):
            await wallboxes.read(wb)
            await control.tick(now=now)
        site(runtime, grid=-1000)  # the sun is back before 3 minutes: the waiting time starts again later
        await wallboxes.read(wb)
        await control.tick(now=190)
        site(runtime, grid=3000)
        for now in (200, 370):
            await wallboxes.read(wb)
            await control.tick(now=now)
            assert charger.charging
        await wallboxes.read(wb)
        await control.tick(now=380)
        assert not charger.charging


async def test_each_mode_charges_up_to_its_own_current(tmp_path, monkeypatch):
    """The owner chooses the highest current per mode, e.g. 8 A for minimum + solar and fast (#402)."""
    async with simulated(plugged=True, steps=(6, 8, 10, 13, 16)) as (charger, port):
        charger.current_a = 6
        runtime, wallboxes = setup(tmp_path, monkeypatch, port, mode="minpv")
        wb = wallboxes.wallboxes[0]
        wallboxes.set_mode(wb.id, "minpv", source="app", current_a=8)
        site(runtime, grid=-11_000)  # sun for 16 A
        await wallboxes.read(wb)
        await SurplusControl(runtime, None, wallboxes).tick(now=0)
        assert charger.writes == [{"150": 8}, {"140": True}]

        wallboxes.set_mode(wb.id, "now", source="app", current_a=10)
        await wallboxes.read(wb)
        await SurplusControl(runtime, None, wallboxes).tick(now=100)
        assert charger.current_a == 10
        assert wallboxes.wallboxes[0].mode_current_a == {"minpv": 8, "now": 10}
        log = [e["details"]["to"] for e in runtime.storage.control_log() if e["details"]["to"]["action"] == "mode_current"]
        assert {"action": "mode_current", "value": {"mode": "now", "current_a": 10}} in log

        with pytest.raises(ValueError, match="zwischen 6 und 16 A"):
            wallboxes.set_mode(wb.id, "now", current_a=20)  # above the owner's highest current
        with pytest.raises(ValueError, match="kein Ladestrom"):
            wallboxes.set_mode(wb.id, "off", current_a=8)
        for bad in (float("inf"), float("nan"), "x", [8]):  # values from the JSON of a request
            with pytest.raises(ValueError):
                wallboxes.set_mode(wb.id, "now", current_a=bad)


def test_mode_currents_and_waiting_time_are_checked():
    base = {"name": "Wallbox", "host": "192.168.1.60", "local_key": KEY, "max_current_a": 16}
    # a mode current above a lowered highest current is cut, so the stored wallbox stays valid
    wb = validate([{**base, "max_current_a": 10, "mode_current_a": {"now": 16, "pv": 8}}])[0]
    assert wb.mode_current_a == {"now": 10, "pv": 8} and wb.stop_after_min == 10
    for bad in ({"mode_current_a": {"off": 8}}, {"mode_current_a": {"now": 4}}, {"mode_current_a": {"now": "x"}},
                {"mode_current_a": {"now": float("inf")}}, {"mode_current_a": "now"}, {"stop_after_min": 0},
                {"stop_after_min": 61}, {"stop_after_min": float("inf")}, {"max_current_a": float("inf")}):
        with pytest.raises(ValueError):
            validate([{**base, **bad}])


async def test_without_a_car_nothing_is_written_and_a_charge_the_wallbox_starts_itself_stops_at_once(tmp_path, monkeypatch):
    async with simulated(plugged=False) as (charger, port):
        runtime, wallboxes = setup(tmp_path, monkeypatch, port, mode="pv")
        control = SurplusControl(runtime, None, wallboxes)
        wb = wallboxes.wallboxes[0]
        site(runtime, grid=-8000)  # plenty of sun, but no car
        for now in (0, 10, 20):
            await wallboxes.read(wb)
            await control.tick(now=now)
        assert charger.writes == []  # the mode waits for a car
        # a car is plugged in and the wallbox starts on its own with its 16 A, there is no surplus
        charger.plugged, charger.charging = True, True
        site(runtime, grid=11_000)
        await wallboxes.read(wb)
        await control.tick(now=30)
        assert charger.writes == [{"140": False}] and not charger.charging  # at once, not after the waiting time


def test_announcements_in_the_older_format_are_ignored():
    """The older format (prefix 55AA) is encrypted with AES-ECB, which OpenAmpere does not use (#396); such a device is
    found through the request or entered by its address."""
    import binascii
    import struct
    body = struct.pack(">I", 0) + json.dumps({"ip": "192.168.1.50", "gwId": "simulated0000000000",
                                              "version": "3.5"}).encode()
    frame = struct.pack(">4I", 0x55AA, 0, 0x13, len(body) + 8) + body
    frame += struct.pack(">I", binascii.crc32(frame) & 0xFFFFFFFF) + b"\x00\x00\xaa\x55"
    assert tuya.decode_announcement(frame) is None
    assert tuya.announced(frame, "192.168.1.50") is None


def test_the_request_asks_devices_to_announce_themselves():
    _, command, payload = tuya.unpack(tuya.DISCOVERY_KEY, tuya.discovery_request("192.168.1.10"))
    assert command == tuya.REQUEST_INFO and json.loads(payload) == {"from": "app", "ip": "192.168.1.10"}
    # where the search listens: broadcasts and the card's own address in the home network, never all interfaces
    assert tuya.listen_addresses([("192.168.1.10", "192.168.1.255")]) == ["255.255.255.255", "192.168.1.255",
                                                                           "192.168.1.10"]


async def test_the_search_asks_and_takes_an_answer_sent_to_its_own_address():
    import socket
    port, ask_port = closed_port(), closed_port()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as device:
        device.bind(("127.0.0.1", ask_port))
        device.setblocking(False)
        loop = asyncio.get_running_loop()
        original = tuya.REQUEST_PORT
        tuya.REQUEST_PORT = ask_port
        try:
            # the cards of a test machine are not a home network: ask from the loopback card
            search = asyncio.create_task(tuya.search(1.0, ports=(port,), addresses=("127.0.0.1",),
                                                     cards=[("127.0.0.1", "127.0.0.1")]))
            data, sender = await asyncio.wait_for(loop.sock_recvfrom(device, 2048), 2)
        finally:
            tuya.REQUEST_PORT = original
        assert json.loads(tuya.unpack(tuya.DISCOVERY_KEY, data)[2])["from"] == "app"
        device.sendto(announcement("127.0.0.1"), ("127.0.0.1", port))  # the answer, in the format of 3.5
        found = await search
    assert [d["host"] for d in found] == ["127.0.0.1"]
