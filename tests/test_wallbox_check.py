# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""The read-only wallbox check of the setup (#325): reads in the wallbox's current mode, never writes, and keeps the
card number, the energy counters and the serial number out of the report."""

import asyncio
import json
import socket

import pytest
from fastapi.testclient import TestClient

from openampere import probe
from openampere.api import create_app
from openampere.diagnostics import Diagnostics, report_markdown
from openampere.probe import Probe
from openampere.runtime import Runtime
from openampere.simulator import SimulatedFoxCharger
from openampere.storage import Storage
from openampere.wallbox import WallboxCheck, check_markdown
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


def free_port() -> int:
    """A port nothing listens on: the host answers a connection with a reset."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def test_reads_the_charger_in_its_current_mode(runtime, monkeypatch):
    async def no_extra_connection(host, port, timeout=probe.TIMEOUT_S):
        raise AssertionError("only after the Modbus client failed")
    monkeypatch.setattr(probe, "_connect_failure", no_extra_connection)
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
    closed = free_port()
    monkeypatch.setattr(FoxEssCharger, "ports", (closed, port))
    async with server:
        result = await WallboxCheck(Probe(runtime)).run("foxess_ac", "127.0.0.1", None, None)
    assert result["tried_ports"] == [closed, port] and result["outcome"] == "readable"
    assert result["ports"] == {str(closed): "refused"}


async def test_a_refused_connection_points_to_another_client(runtime, monkeypatch):
    """#427: the address answers, but no port takes a Modbus connection (another device, or the previous smartbox or
    evcc holds the only one)."""
    first, second = free_port(), free_port()
    monkeypatch.setattr(FoxEssCharger, "ports", (first, second))
    check = WallboxCheck(Probe(runtime))
    result = await check.run("foxess_ac", "127.0.0.1", None, None)
    assert result["outcome"] == "connection_refused" and result["blocks"] == []
    assert result["ports"] == {str(first): "refused", str(second): "refused"}
    text = "\n".join(check_markdown(result))
    assert "nimmt aber keine Modbus-Verbindung an" in text
    assert f"Verbindung je Port: {first} abgelehnt, {second} abgelehnt" in text

    async def silent_on_second(host, port, timeout=probe.TIMEOUT_S):  # one refuses, nothing answers on the other
        return "refused" if port == first else "timeout"
    monkeypatch.setattr(probe, "_connect_failure", silent_on_second)
    result = await check.run("foxess_ac", "127.0.0.1", None, None)
    assert result["outcome"] == "connection_refused"
    assert result["ports"] == {str(first): "refused", str(second): "timeout"}


async def test_no_device_answers(runtime, monkeypatch):
    """#427: only timeouts: nothing at this address."""
    async def silent(host, port, timeout=probe.TIMEOUT_S):
        return "timeout"
    monkeypatch.setattr(probe, "_connect_failure", silent)
    port = free_port()
    check = WallboxCheck(Probe(runtime))
    result = await check.run("foxess_ac", "127.0.0.1", port, None)
    assert result["outcome"] == "unreachable" and result["ports"] == {str(port): "timeout"}
    text = "\n".join(check_markdown(result))
    assert "kein Gerät antwortet unter dieser Adresse" in text and f"Verbindung je Port: {port} keine Antwort" in text


async def test_a_name_that_cannot_be_resolved(runtime, monkeypatch):
    """#427: owners copy the charger's .local name from the previous smartbox; it never resolves in the Home
    Assistant app, and before it failed like a device that does not answer."""
    lookup = socket.getaddrinfo

    def getaddrinfo(host, *args, **kwargs):
        if host == "EVC-example.local":
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        return lookup(host, *args, **kwargs)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(probe.ha_app, "active", lambda: False)
    check = WallboxCheck(Probe(runtime))
    result = await check.run("foxess_ac", "EVC-example.local", None, None)
    assert result["outcome"] == "unresolved" and result["tried_ports"] == [] and result["ports"] == {}
    assert result["local_name_in_app"] is False
    assert "Name nicht auflösbar" in "\n".join(check_markdown(result))
    monkeypatch.setattr(probe.ha_app, "active", lambda: True)
    result = await check.run("foxess_ac", "EVC-example.local", None, None)
    assert result["outcome"] == "unresolved" and result["local_name_in_app"] is True
    assert "(Name auf .local in der Home-Assistant-App)" in "\n".join(check_markdown(result))


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
    runtime.config.inverter.host, runtime.config.inverter.port = "127.0.0.1", port
    async with server:
        with pytest.raises(probe.ProbeError, match="Adresse deines Wechselrichters"):
            await WallboxCheck(Probe(runtime)).run("foxess_ac", " 127.0.0.1 ", port, None)
        runtime.config.inverter.port = 502
        with pytest.raises(probe.ProbeError, match="Adresse deines Wechselrichters"):  # among the usual ports
            await WallboxCheck(Probe(runtime)).run("foxess_ac", "127.0.0.1", None, None)
    assert charger.functions == []  # nothing read


async def test_another_port_of_the_inverters_address_is_checked(runtime):
    """E.g. a Modbus gateway with several devices behind one address (#417)."""
    charger = SimulatedFoxCharger()
    server, port = await serve(charger)
    runtime.config.inverter.host, runtime.config.inverter.port = "127.0.0.1", 9
    async with server:
        result = await WallboxCheck(Probe(runtime)).run("foxess_ac", "127.0.0.1", port, None)
    assert result["outcome"] == "readable"


async def test_unknown_wallbox(runtime):
    with pytest.raises(probe.ProbeError, match="kennt OpenAmpere noch nicht"):
        await WallboxCheck(Probe(runtime)).run("other", "127.0.0.1", 502, None)


def test_endpoints(runtime, authed):
    client = TestClient(create_app(runtime))
    assert client.get("/api/wallbox/models").json() == {"models": [{"key": "foxess_ac", "label": "FoxESS AC EV Charger",
                                                                   "sold_by": ["EKD"]}]}
    client.headers.update({"x-openampere": "1"})
    body = {"model": "foxess_ac", "host": "127.0.0.1", "port": free_port()}
    assert client.post("/api/wallbox/check", json=body).status_code == 401
    authed(client)
    assert client.post("/api/wallbox/check", json=body).json()["outcome"] == "connection_refused"
    assert client.post("/api/wallbox/check", json={**body, "model": "other"}).status_code == 400
    assert client.post("/api/wallbox/check", json={**body, "app_mode": "anything"}).status_code == 422
    assert client.post("/api/wallbox/check", json={**body, "host": "8.8.8.8"}).status_code == 400
