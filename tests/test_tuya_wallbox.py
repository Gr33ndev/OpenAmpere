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


def test_the_local_key_is_stored_encrypted_and_never_shown(tmp_path):
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    wallboxes = NativeWallboxes(runtime)
    saved = wallboxes.save([{"name": "Garage", "host": "192.168.1.20", "local_key": KEY, "device_id": "abc123def456"}])
    stored = runtime.storage.get_meta("wallboxes")[0]
    assert KEY not in json.dumps(stored) and stored["local_key"].startswith("enc:")
    view = wallboxes.view(hide_device_id=True)["wallboxes"][0]
    assert "local_key" not in view and view["local_key_set"] and view["device_id"] is None
    # the app sends the key empty: the stored one stays
    wallboxes.save([{"id": saved[0].id, "name": "Garage 2", "host": "192.168.1.20", "local_key": ""}])
    assert wallboxes.wallboxes[0].local_key == KEY and wallboxes.wallboxes[0].name == "Garage 2"
    with pytest.raises(ValueError):
        wallboxes.save([{"name": "Draußen", "host": "8.8.8.8", "local_key": KEY}])  # not in the home network


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
        for now in (1400, 1410):
            await wallboxes.read(wb)
            await control.tick(now=now)
        assert not charger.charging and charger.writes[-1] == {"140": False}
        log = runtime.storage.control_log()
        assert {e["action"] for e in log} == {"wallbox"} and not any(e["dry_run"] for e in log)


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
    search = asyncio.create_task(tuya.search(0.5, ports=(port,)))
    await asyncio.sleep(0.1)
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
        sender.sendto(announcement("127.0.0.1"), ("127.0.0.1", port))
        sender.sendto(announcement("192.168.1.99"), ("127.0.0.1", port))  # forged: names another address
    found = await search
    assert [d["host"] for d in found] == ["127.0.0.1"]


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
