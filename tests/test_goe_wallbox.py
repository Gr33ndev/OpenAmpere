# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""go-e Charger over its local HTTP API (#597): shown, found by the search, never written."""

import asyncio
import threading

import pytest
from fastapi.testclient import TestClient

from openampere import discovery
from openampere.api import create_app
from openampere.runtime import Runtime
from openampere.simulator import SimulatedGoe
from openampere.storage import Storage
from openampere.wallbox.base import WallboxUnreachable
from openampere.wallbox.goe import GoeChargerLive, GoeHttp
from openampere.wallbox.native import MODELS, NativeWallboxes


async def serve(charger):
    server = await asyncio.start_server(charger.serve_client, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


def test_api_v2_values():
    idle = GoeChargerLive.interpret({**SimulatedGoe().v2(), "api": 2})
    assert (idle.state, idle.connected, idle.charging, idle.power_w) == ("idle", False, False, 0.0)
    assert idle.voltage_v == [230.0, 231.0, 232.0] and idle.installed_phases == 3
    assert idle.session_kwh is None and idle.firmware == "56.9" and idle.max_current_a == 16
    charging = GoeChargerLive.interpret({**SimulatedGoe(charging=True).v2(), "api": 2})
    assert (charging.state, charging.connected, charging.charging) == ("charging", True, True)
    assert charging.power_w == 11040 and charging.current_a == [16.0, 16.0, 16.0] and charging.active_phases == 3
    assert (charging.session_kwh, charging.session_s, charging.set_current_a) == (2.5, 1800, 16)


def test_api_v1_values_in_their_units():
    """v1: currents in 0.1 A, the total power in 0.01 kW, the energy of the charge in 10 Ws, all as strings."""
    charging = GoeChargerLive.interpret({**SimulatedGoe(charging=True).v1(), "api": 1})
    assert (charging.state, charging.charging, charging.power_w) == ("charging", True, 11040)
    assert charging.current_a == [16.0, 16.0, 16.0] and charging.session_kwh == 2.5 and charging.session_s is None
    assert charging.set_current_a == 16 and charging.firmware == "056.9"


@pytest.mark.parametrize(("car", "state", "connected"), [(3, "plugged_in", True), (4, "finished", True),
                                                         (5, "fault", False), (0, "unknown", False),
                                                         (None, "unknown", False)])
def test_states(car, state, connected):
    reading = GoeChargerLive.interpret({**SimulatedGoe().v2(), "car": car, "api": 2})
    assert (reading.state, reading.connected) == (state, connected)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), 10 ** 400, "NaN"])
def test_values_that_are_no_number_are_left_out(bad):
    """JSON allows NaN and huge numbers: never an error that would stop the read."""
    status = {**SimulatedGoe(charging=True).v2(), "car": bad, "amp": bad, "ama": bad, "wh": bad, "api": 2}
    status["nrg"] = [bad] * 16
    reading = GoeChargerLive.interpret(status)
    assert (reading.state, reading.power_w, reading.set_current_a, reading.max_current_a) == ("unknown", 0.0, None, None)
    assert reading.voltage_v == [None] * 3


def test_implausible_power_is_no_charge():
    """A total in another unit (e.g. 0.01 W) would read as megawatts: not shown, the charge as unknown."""
    status = {**SimulatedGoe(charging=True).v2(), "api": 2}
    status["nrg"] = [*status["nrg"][:11], 1_104_000, *status["nrg"][12:]]
    reading = GoeChargerLive.interpret(status)
    assert (reading.state, reading.charging, reading.power_w) == ("unknown", False, 0.0)


async def test_reads_v2_with_a_filter_and_falls_back_to_v1():
    for api, paths in ((2, ["/api/status?filter=car,nrg,wh,amp,ama,fwv,cdi"]),
                       (1, ["/api/status?filter=car,nrg,wh,amp,ama,fwv,cdi", "/status"])):
        charger = SimulatedGoe(api=api, charging=True)
        server, port = await serve(charger)
        async with server:
            status = await GoeHttp("127.0.0.1", port).status()
            assert status["api"] == api and [p for _, p in charger.requests] == paths
            charger.requests.clear()
            await GoeHttp("127.0.0.1", port).status()  # the API that answered is asked first next time
            assert [p for _, p in charger.requests] == paths[-1:]
        assert {m for m, _ in charger.requests} == {"GET"}


async def test_another_device_or_none_is_unreachable():
    async def not_goe(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 15\r\nConnection: close\r\n\r\n<html>hi</html>")
        await writer.drain()
        writer.close()
    server = await asyncio.start_server(not_goe, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        with pytest.raises(WallboxUnreachable, match="nicht wie ein go-e Charger"):
            await GoeHttp("127.0.0.1", port).status()
    with pytest.raises(WallboxUnreachable, match="antwortet nicht"):
        await GoeHttp("127.0.0.1", port).status()  # closed now


async def test_shown_as_a_wallbox_and_never_written(tmp_path, monkeypatch):
    charger = SimulatedGoe(charging=True)
    server, port = await serve(charger)
    monkeypatch.setattr(GoeChargerLive, "port", port)
    async with server:
        runtime = Runtime({}, Storage(tmp_path / "t.db"))
        runtime.config.control.enabled, runtime.config.control.dry_run = True, False  # writing allowed: still none
        wallboxes = NativeWallboxes(runtime)
        wallboxes.save([{"name": "Garage", "model": "goe", "host": "127.0.0.1", "port": 1234, "local_key": "x",
                         "device_id": "abcdefghijkl"}])
        try:
            stored = wallboxes.wallboxes[0]
            assert (stored.port, stored.unit, stored.local_key, stored.device_id) == (None, None, "", "")
            with pytest.raises(ValueError):
                wallboxes.set_mode(stored.id, "pv", source="test")
            wallboxes.refresh()
            await wallboxes.wait()
            view = wallboxes.view()
        finally:
            runtime.storage.close()
    item = view["wallboxes"][0]
    assert item["read_only"] and item["modes"] == ["manual"] and item["online"]
    assert item["reading"]["charging"] and item["reading"]["power_w"] == 11040
    assert {m for m, _ in charger.requests} == {"GET"}
    assert all(p.startswith(("/api/status?", "/status")) for _, p in charger.requests)
    model = next(m for m in view["models"] if m["key"] == "goe")
    assert model["protocol"] == "http" and model["read_only"] and "port" not in model
    assert not getattr(MODELS["goe"], "controllable", False)


async def test_search_finds_only_go_e_chargers():
    charger = SimulatedGoe()
    server, port = await serve(charger)
    async with server:
        found = await discovery.find_goe(["127.0.0.1"], port)
    assert found == [{"host": "127.0.0.1", "api": 2}]
    assert {m for m, _ in charger.requests} == {"GET"}
    assert await discovery.find_goe(["127.0.0.1"], port) == []  # nothing listens any more


async def test_search_looks_up_the_local_networks_off_the_event_loop(monkeypatch):
    loop_thread = threading.get_ident()
    called = []
    monkeypatch.setattr(discovery, "local_prefixes", lambda: called.append(threading.get_ident()) or [])
    assert await discovery.find_goe() == []
    assert called and called[0] != loop_thread


async def test_endpoints(tmp_path, monkeypatch, authed):
    charger = SimulatedGoe(charging=True)
    server, port = await serve(charger)
    monkeypatch.setattr(GoeChargerLive, "port", port)
    runtime = Runtime({}, Storage(tmp_path / "t.db"))

    async def fake_find():
        return [{"host": "192.168.1.20", "api": 2}]
    monkeypatch.setattr(discovery, "find_goe", fake_find)
    try:
        async with server:
            client = TestClient(create_app(runtime))
            client.headers.update({"x-openampere": "1"})
            assert client.post("/api/wallboxes/search", json={"model": "goe"}).status_code == 401
            authed(client)
            answer = client.post("/api/wallboxes/search", json={"model": "goe"})
            assert answer.json() == {"available": True, "devices": [{"host": "192.168.1.20", "api": 2}]}
            tested = await asyncio.to_thread(client.post, "/api/wallboxes/test", json={"model": "goe", "host": "127.0.0.1"})
            assert tested.status_code == 200 and tested.json()["reading"]["power_w"] == 11040
    finally:
        runtime.storage.close()
    assert {m for m, _ in charger.requests} == {"GET"}
