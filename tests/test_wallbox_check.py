# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""The read-only wallbox check of the setup (#325): reads in the wallbox's current mode, never writes, and keeps the
card number, the energy counters and the serial number out of the report."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from openampere import probe
from openampere.api import create_app
from openampere.diagnostics import Diagnostics, report_markdown
from openampere.probe import Probe
from openampere.runtime import Runtime
from openampere.simulator import SimulatedFoxCharger
from openampere.storage import Storage
from openampere.wallbox import WallboxCheck
from openampere.wallbox.foxess import FoxEssCharger


@pytest.fixture
def runtime(tmp_path):
    rt = Runtime({}, Storage(tmp_path / "t.db"))
    yield rt
    rt.storage.close()


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(probe, "REQUEST_GAP_S", 0)
    monkeypatch.setattr(probe, "CONNECT_SETTLE_S", 0)


async def serve(charger):
    server = await asyncio.start_server(charger.serve_client, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


async def test_reads_the_charger_in_its_current_mode(runtime):
    charger = SimulatedFoxCharger()
    server, port = await serve(charger)
    async with server:
        result = await WallboxCheck(Probe(runtime)).run("foxess_ac", "127.0.0.1", port, None, "default", "yes")
    assert set(charger.functions) == {3}  # only reads
    assert (result["outcome"], result["port"], result["unit"]) == ("readable", port, 1)
    f = result["findings"]
    assert (f["model"], f["firmware"], f["status"], f["work_mode"]) == ("A022KP1", "1.16", "idle", "plug_and_charge")
    assert (f["max_power_kw"], f["min_current_a"], f["fallback_current_a"], f["validity_s"]) == (22.0, 6.0, 6.0, 60)
    assert f["phase_switching"] is True and f["voltage_v"] == [230.1, 230.2, 230.3]
    assert f["rfid_card"] is True and f["energy_plausible"] is True
    assert result["answers"] == {"app_mode": "default", "wired": "yes"}
    first = result["blocks"][0]
    assert first["start"] == 0x1000 and first["count"] == 0x22  # the serial number at 0x1022 is not read
    assert first["words"][0x1C] is None and first["words"][0x1D] is None  # the card number
    assert first["words"][0x16:0x1A] == [None] * 4  # the energy counters


async def test_a_charger_outside_the_modbus_mode_refuses_reads(runtime):
    charger = SimulatedFoxCharger(busy=True)
    server, port = await serve(charger)
    async with server:
        result = await WallboxCheck(Probe(runtime)).run("foxess_ac", "127.0.0.1", port, None)
    assert result["outcome"] == "busy" and result["findings"] == {}
    assert result["blocks"][0]["exception"] == 6


async def test_single_phase_model_without_phase_switching_registers(runtime):
    charger = SimulatedFoxCharger(single_phase=True)
    server, port = await serve(charger)
    async with server:
        result = await WallboxCheck(Probe(runtime)).run("foxess_ac", "127.0.0.1", port, None)
    assert result["outcome"] == "readable"
    assert [b["ok"] for b in result["blocks"]] == [True, True, False]
    assert result["blocks"][2]["exception"] == 2 and result["findings"]["phase_switching"] is None


async def test_tries_the_usual_ports(runtime, monkeypatch):
    charger = SimulatedFoxCharger()
    server, port = await serve(charger)
    monkeypatch.setattr(FoxEssCharger, "ports", (9, port))  # nothing listens on port 9
    async with server:
        result = await WallboxCheck(Probe(runtime)).run("foxess_ac", "127.0.0.1", None, None)
    assert result["tried_ports"] == [9, port] and result["outcome"] == "readable"
    monkeypatch.setattr(FoxEssCharger, "ports", (9,))
    result = await WallboxCheck(Probe(runtime)).run("foxess_ac", "127.0.0.1", None, None)
    assert result["outcome"] == "unreachable" and result["blocks"] == []


async def test_all_connections_taken(runtime, monkeypatch):
    monkeypatch.setattr(probe, "CONNECT_SETTLE_S", 0.2)
    charger = SimulatedFoxCharger()
    charger.max_connections = 0  # e.g. evcc holds the only connection
    server, port = await serve(charger)
    async with server:
        result = await WallboxCheck(Probe(runtime)).run("foxess_ac", "127.0.0.1", port, None)
    assert result["outcome"] == "connections_taken"


async def test_the_report_keeps_private_values_out(runtime):
    charger = SimulatedFoxCharger()
    server, port = await serve(charger)
    diagnostics = Diagnostics(runtime)
    async with server:
        await diagnostics.wallbox.run("foxess_ac", "127.0.0.1", port, None, "modbus_tcp", "no")
    report = await diagnostics.run()
    text = json.dumps(report, ensure_ascii=False) + report_markdown(report)
    assert "127.0.0.1" not in text and "SIMULATEDCHARGER" not in text
    words = report["wallbox"]["blocks"][0]["words"]
    assert 0x1234 not in words and 0x5678 not in words and 12345 not in words  # card number, energy counter
    assert "Wallbox-Prüfung" in report_markdown(report) and "A022KP1" in report_markdown(report)


async def test_the_inverters_address_is_named_as_such(runtime):
    """The owner entered the inverter's address: say so instead of talking about its unit id (#411)."""
    charger = SimulatedFoxCharger()
    server, port = await serve(charger)
    runtime.config.inverter.host = "127.0.0.1"
    async with server:
        with pytest.raises(probe.ProbeError, match="Adresse deines Wechselrichters"):
            await WallboxCheck(Probe(runtime)).run("foxess_ac", " 127.0.0.1 ", port, None)
    assert charger.functions == []  # nothing read


async def test_unknown_wallbox(runtime):
    with pytest.raises(probe.ProbeError, match="kennt OpenAmpere noch nicht"):
        await WallboxCheck(Probe(runtime)).run("other", "127.0.0.1", 502, None)


def test_endpoints(runtime, authed):
    client = TestClient(create_app(runtime))
    assert client.get("/api/wallbox/models").json() == {"models": [{"key": "foxess_ac", "label": "FoxESS AC EV Charger",
                                                                   "sold_by": ["EKD"]}]}
    client.headers.update({"x-openampere": "1"})
    body = {"model": "foxess_ac", "host": "127.0.0.1", "port": 9}
    assert client.post("/api/wallbox/check", json=body).status_code == 401
    authed(client)
    assert client.post("/api/wallbox/check", json=body).json()["outcome"] == "unreachable"
    assert client.post("/api/wallbox/check", json={**body, "model": "other"}).status_code == 400
    assert client.post("/api/wallbox/check", json={**body, "app_mode": "anything"}).status_code == 422
    assert client.post("/api/wallbox/check", json={**body, "host": "8.8.8.8"}).status_code == 400
