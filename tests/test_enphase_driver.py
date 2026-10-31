# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Enphase IQ Gateway with IQ Batteries (#522): display only, chosen in the setup with a token from Enphase."""

import asyncio
import base64
import json
import time
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from openampere import discovery
from openampere.api import create_app
from openampere.diagnostics import Diagnostics
from openampere.drivers import registry
from openampere.drivers.enphase.driver import EnphaseDriver, TokenRejected, to_snapshot, token_expiry
from openampere.drivers.modbus import DetectionFailed, DeviceRefused, DeviceUnreachable, friendly_error
from openampere.runtime import Runtime
from openampere.simulator import SimulatedEnphase
from openampere.storage import Storage
from conftest import login


def jwt(exp: float) -> str:
    """A token shaped like Enphase's: only its expiry matters here (the gateway checks it, OpenAmpere only reads it)."""
    def part(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")
    return f"{part({'alg': 'ES256'})}.{part({'exp': int(exp)})}.c2lnbmF0dXJl"


async def serve(sim):
    server = await asyncio.start_server(sim.serve_client, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


def sunny(sim):
    sim.energy.step(datetime(2026, 6, 1, 13, 0), 60)
    return sim


def test_the_expiry_of_the_token():
    assert token_expiry(jwt(1_900_000_000)) == 1_900_000_000
    assert token_expiry("not a token") is None and token_expiry("") is None


async def test_read_with_the_token_and_its_signs():
    sim = sunny(SimulatedEnphase(token="t1"))
    server, port = await serve(sim)
    async with server:
        info = await registry.detect("127.0.0.1", port, only="enphase", token="t1")
        driver = registry.create("enphase", "127.0.0.1", port, 0, timeout=3, token="t1")
        try:
            await driver.connect()
            snap = (await driver.read()).sanitize()
        finally:
            await driver.close()
    assert (info.driver, info.manufacturer, info.serial, info.firmware) == ("enphase", "Enphase", "122100000001", "D8.2.4382")
    assert not info.supports_control
    e = sim.energy
    assert e.grid_w < 0 and e.battery_w < 0  # exporting and charging
    assert snap.pv_power == pytest.approx(e.pv_w, abs=0.5)
    assert snap.grid_power == pytest.approx(e.grid_w, abs=0.5)  # + = import
    assert snap.house_power == pytest.approx(e.house_w, abs=0.5)
    assert snap.battery_power == pytest.approx(e.battery_w, abs=0.5)  # + = discharging
    assert snap.battery_soc == round(e.soc) and snap.totals.pv is None
    assert all(authorised for path, authorised in sim.requests if path != "/info")


@pytest.mark.parametrize(("token", "words"), [("", "braucht ein Zugangs-Token"), ("wrong", "lehnt das Zugangs-Token ab")])
async def test_a_missing_or_wrong_token_says_what_to_do(token, words):
    sim = SimulatedEnphase(token="t1")
    server, port = await serve(sim)
    async with server:
        with pytest.raises(TokenRejected) as caught:
            await registry.detect("127.0.0.1", port, only="enphase", token=token)
    assert words in friendly_error(caught.value) and "Modbus" not in friendly_error(caught.value)


async def test_a_gateway_without_batteries_still_shows_pv_and_grid():
    sim = sunny(SimulatedEnphase(token="t1", batteries=False))
    server, port = await serve(sim)
    async with server:
        driver = registry.create("enphase", "127.0.0.1", port, 0, timeout=3, token="t1")
        try:
            await driver.connect()
            snap = await driver.read()
        finally:
            await driver.close()
    assert snap.pv_power is not None and snap.battery_power is None and snap.battery_soc is None


def test_inverters_count_when_the_production_meter_is_off():
    production = {"production": [{"type": "inverters", "wNow": 3200},
                                 {"type": "eim", "measurementType": "production", "activeCount": 0, "wNow": 0}],
                  "consumption": [{"measurementType": "net-consumption", "activeCount": 0, "wNow": 5}]}
    snap = to_snapshot(production, None, None, 0)
    assert snap.pv_power == 3200 and snap.grid_power is None  # no consumption meter: no grid value


async def test_another_web_server_is_no_gateway_and_nothing_listening_is_unreachable():
    async def other(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 13\r\nConnection: close\r\n\r\n<html></html>")
        await writer.drain()
        writer.close()
    server = await asyncio.start_server(other, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        with pytest.raises(DetectionFailed, match="nicht wie ein Enphase-Gateway"):
            await registry.detect("127.0.0.1", port, only="enphase", token="t1")
    with pytest.raises(DeviceUnreachable):
        await registry.detect("127.0.0.1", port, only="enphase", token="t1")


async def test_never_written():
    driver = EnphaseDriver("192.0.2.1", token="t1")
    for write in (lambda: driver.write_soc_limits(min_soc=20), lambda: driver.set_remote_power(-1000, 180),
                  lambda: driver.write_export_limit(5000)):
        with pytest.raises(DeviceRefused, match="nur die Werte an"):
            await write()


def test_every_driver_can_be_tested_in_the_setup(tmp_path):
    """The setup test once allowed only "auto", "foxess" and "saj": choosing another driver failed with a validation
    error before anything was tried."""
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    client = login(TestClient(create_app(runtime)))
    for key in registry.DRIVERS:
        answer = client.post("/api/setup/test", json={"host": "127.0.0.1", "port": 9, "driver": key})
        assert answer.status_code == 200, (key, answer.text)


async def test_the_token_is_stored_encrypted_bound_to_the_address_and_shown_by_its_expiry(tmp_path):
    token = jwt(time.time() + 20 * 86400)  # runs out in 20 days
    sim = sunny(SimulatedEnphase(token=token))
    server, port = await serve(sim)
    async with server:
        runtime = Runtime({}, Storage(tmp_path / "t.db"))
        try:
            await runtime.update_settings({"inverter.host": "127.0.0.1", "inverter.port": port,
                                           "inverter.driver": "enphase", "inverter.token": token,
                                           "inverter.poll_interval": 2})
            for _ in range(150):
                if runtime.collector.latest:
                    break
                await asyncio.sleep(0.1)
            assert runtime.collector.device.driver == "enphase"
            assert runtime.collector.poll_interval() == 15.0
            # the setup test of the stored address uses the stored token
            assert (await discovery.test_connection(runtime, "127.0.0.1", port, 0, "enphase"))["ok"]
            report = await Diagnostics(runtime).run()
        finally:
            await runtime.collector.stop()
    stored = runtime.storage.get_settings()
    assert stored["inverter.token"] and token not in json.dumps(stored)  # stored, but never in plain text
    check = {c["id"]: c for c in report["checks"]}["enphase_token"]
    assert check["status"] == "warn" and ("noch 19 Tage" in check["summary"] or "noch 20 Tage" in check["summary"])
    assert token not in json.dumps(report)
