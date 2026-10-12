# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Reading any device in the home network (#324): only reads, stays within its limits, keeps the report private."""

import asyncio
import json
import socket
import struct
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from openampere import probe
from openampere.api import create_app
from openampere.diagnostics import Diagnostics, report_markdown
from openampere.drivers.foxess.registers import H3_NEW, MODEL_ADDRESS
from openampere.drivers.modbus import ModbusIllegalError
from openampere.privacy import text_runs
from openampere.probe import Probe, ProbeError, parse_mdns
from openampere.runtime import Runtime
from openampere.simulator import SimulatedInverter
from openampere.storage import Storage

SERIAL = "SN0123456789ABCD"


@pytest.fixture
def runtime(tmp_path):
    rt = Runtime({}, Storage(tmp_path / "t.db"))
    yield rt
    rt.storage.close()


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(probe, "REQUEST_GAP_S", 0)
    monkeypatch.setattr(probe, "CONNECT_SETTLE_S", 0)


async def serve(sim):
    sim.energy.step(datetime(2026, 6, 21, 13, 0), 600)
    sim.update_registers()
    server = await asyncio.start_server(sim.serve_client, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


def recording(sim) -> list[int]:
    """The function codes the simulated device receives."""
    functions: list[int] = []
    handle = sim.handle
    sim.handle = lambda pdu: functions.append(pdu[0]) or handle(pdu)
    return functions


def test_blocks_of():
    assert probe.blocks_of(100, 130) == [(100, 60), (160, 60), (220, 10)]


async def test_reads_blocks_with_function_3_only_and_reports_rejections(runtime):
    sim = SimulatedInverter(H3_NEW, "H3-10.0-Smart", SERIAL, strict_function=False, max_connections=1)
    functions = recording(sim)
    server, port = await serve(sim)
    async with server:
        result = await Probe(runtime).read("127.0.0.1", port, 247, 3, MODEL_ADDRESS, 32)
        missing = await Probe(runtime).read("127.0.0.1", port, 247, 4, 1, 2)  # nothing there
    assert set(functions) == {3, 4}  # never a writing function code
    assert [b.ok for b in result.blocks] == [True]
    texts = [t for _, _, t in text_runs(result.blocks[0].words)]
    assert texts == ["H3-10.0-Smart", SERIAL]
    assert missing.blocks[0].error == "rejected" and missing.blocks[0].exception == 2
    assert sim.connections == 0  # the connection is closed again


async def test_limits(runtime):
    p = Probe(runtime)
    with pytest.raises(ProbeError, match="Funktionscodes 3 und 4"):
        await p.read("127.0.0.1", 502, 1, 6, 0, 1)
    with pytest.raises(ProbeError, match="höchstens 600"):
        await p.read("127.0.0.1", 502, 1, 3, 0, 601)
    with pytest.raises(ProbeError, match="höchstens 600"):
        await p.read("127.0.0.1", 502, 1, 3, 65530, 10)
    for host in ("8.8.8.8", "http://192.168.1.5/", "192.168.1.5; rm"):  # the internet, a URL, anything else
        with pytest.raises(ProbeError, match="Heimnetz"):
            await p.read(host, 502, 1, 3, 0, 1)
    with pytest.raises(ProbeError, match="Heimnetz"):
        await p.identify("8.8.8.8")


async def test_one_run_at_a_time(runtime):
    p = Probe(runtime)
    async with p._lock:
        with pytest.raises(ProbeError, match="schon eine Abfrage"):
            await p.read("127.0.0.1", 9, 1, 3, 0, 1)


async def test_unreachable_device(runtime):
    with pytest.raises(ProbeError, match="Keine Verbindung zu 127.0.0.1:9"):
        await Probe(runtime).read("127.0.0.1", 9, 1, 3, 0, 1)


async def test_the_inverter_is_read_over_the_existing_connection(runtime):
    """The inverter allows only a few connections: no second one, and only with its own unit id."""
    class Driver:
        _unit = 247
        calls: list = []

        async def _read_once(self, address, count, function):
            self.calls.append((address, count, function))
            if address == 1:
                err = ModbusIllegalError("read 1+1: Modbus exception 2")
                err.code = 2
                raise err
            return list(range(count))

    runtime.config.inverter.host, runtime.config.inverter.port = "127.0.0.1", 50200
    runtime.collector.driver, runtime.collector.connected = Driver(), True
    p = Probe(runtime)
    result = await p.read("127.0.0.1", 50200, 0, 3, 30000, 70)
    assert result.shared_connection and result.unit == 247
    assert Driver.calls == [(30000, 60, 3), (30060, 10, 3)]
    rejected = await p.read("127.0.0.1", 50200, 247, 3, 1, 1)
    assert (rejected.blocks[0].error, rejected.blocks[0].exception) == ("rejected", 2)
    with pytest.raises(ProbeError, match="Geräteadresse 247"):
        await p.read("127.0.0.1", 50200, 1, 3, 30000, 1)


async def test_the_report_shortens_text_and_hides_addresses(runtime):
    sim = SimulatedInverter(H3_NEW, "H3-10.0-Smart", SERIAL, strict_function=False, max_connections=1)
    server, port = await serve(sim)
    diagnostics = Diagnostics(runtime)
    async with server:
        await diagnostics.probe.read("127.0.0.1", port, 247, 3, MODEL_ADDRESS, 32)
    diagnostics.probe.last_search = {"available": True, "devices": [
        {"service": "_modbus._tcp", "name": "EVC-60BA022KP1E0042", "host": "192.168.1.77", "port": 502}]}
    report = await diagnostics.run()
    text = json.dumps(report, ensure_ascii=False) + report_markdown(report)
    for private in (SERIAL, "127.0.0.1", "192.168.1.77", "EVC-60BA022KP1E0042"):
        assert private not in text, private
    block = report["probe"]["read"]["blocks"][0]
    assert block["text"][1]["text"] == "SN01…CD" and block["words"][16] is None  # the serial's registers
    assert "EVC-…42" in text and "Geräte-Abfrage" in report_markdown(report)

    full = await diagnostics.run(include_serial=True)  # the owner chose to include it
    assert full["probe"]["read"]["blocks"][0]["text"][1]["text"] == SERIAL


def _name(name: str) -> bytes:
    return b"".join(bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\0"


def mdns_answer() -> bytes:
    """An answer like a FoxESS wallbox's: PTR to the instance, SRV with port and target, A with the address; the
    names after the first are compressed."""
    header = struct.pack(">HHHHHH", 0, 0x8400, 0, 1, 0, 2)
    ptr_name = _name("_modbus._tcp.local")  # at offset 12
    instance = b"\x0fEVC-SIMULATED01\xc0\x0c"  # "EVC-SIMULATED01" + pointer to the service name
    ptr = ptr_name + struct.pack(">HHIH", 12, 1, 120, len(instance)) + instance
    instance_at = 12 + len(ptr_name) + 10
    target = _name("evc.local")
    srv_data = struct.pack(">HHH", 0, 0, 502) + target
    srv = struct.pack(">H", 0xC000 | instance_at) + struct.pack(">HHIH", 33, 0x8001, 120, len(srv_data)) + srv_data
    target_at = 12 + len(ptr) + 2 + 10 + 6
    a = struct.pack(">H", 0xC000 | target_at) + struct.pack(">HHIH", 1, 0x8001, 120, 4) + socket.inet_aton("192.168.1.77")
    return header + ptr + srv + a


def test_parse_mdns_answer():
    records = parse_mdns(mdns_answer())
    assert records == [("_modbus._tcp.local", 12, "EVC-SIMULATED01._modbus._tcp.local"),
                       ("EVC-SIMULATED01._modbus._tcp.local", 33, (502, "evc.local")),
                       ("evc.local", 1, "192.168.1.77")]
    with pytest.raises((ValueError, struct.error)):
        parse_mdns(struct.pack(">HHHHHH", 0, 0, 0, 1, 0, 0) + b"\xc0\x0c")  # a name pointing to itself


def test_browse_collects_the_announced_devices(monkeypatch):
    sent, bound = [], []

    class Socket:
        def __init__(self, *args):
            self.answers = [(mdns_answer(), ("192.168.1.77", 5353)), (b"garbage", ("192.168.1.9", 5353))]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def connect(self, address):  # only to learn the local address, nothing is sent
            pass

        def getsockname(self):
            return ("192.168.1.10", 40000)

        def setsockopt(self, *args):
            pass

        def bind(self, address):
            bound.append(address)

        def settimeout(self, seconds):
            pass

        def sendto(self, data, address):
            sent.append((data, address))

        def recvfrom(self, size):
            if not self.answers:
                raise TimeoutError
            return self.answers.pop(0)

    monkeypatch.setattr(probe.socket, "socket", Socket)
    devices = probe.browse(probe.MDNS_SERVICES, 1.0)
    assert devices == [{"service": "_modbus._tcp", "name": "EVC-SIMULATED01", "host": "192.168.1.77", "port": 502}]
    assert sent == [(probe.mdns_query("_modbus._tcp.local"), probe.MDNS)]
    assert bound == [("192.168.1.10", 0)]  # the home network's card, not all interfaces


async def test_no_search_in_the_home_assistant_app(runtime, monkeypatch):
    monkeypatch.setattr(probe.ha_app, "active", lambda: True)
    assert await Probe(runtime).search() == {"available": False, "devices": []}


async def test_identify_reads_the_title_and_the_vendor_part_of_the_mac(runtime, monkeypatch, tmp_path):
    class Page(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            body = "<html><head><title> Fox&amp;Charger\n Web </title></head></html>".encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Page)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    arp = tmp_path / "arp"
    arp.write_text("IP address       HW type     Flags       HW address            Mask     Device\n"
                   "127.0.0.1        0x1         0x2         aa:bb:cc:dd:ee:ff     *        eth0\n")
    monkeypatch.setattr(probe, "PORTS", (port, 9))
    monkeypatch.setattr(probe, "WEB_PORTS", ((port, "http"),))
    monkeypatch.setattr(probe, "ARP", str(arp))
    monkeypatch.setattr(probe.ha_app, "active", lambda: False)
    try:
        result = await Probe(runtime).identify("127.0.0.1")
    finally:
        server.shutdown()
    assert result["open_ports"] == [port] and result["title"] == "Fox&Charger Web"
    assert result["mac_vendor"] == "AA:BB:CC"  # never the full address


def test_endpoints_need_the_login(runtime, authed):
    client = TestClient(create_app(runtime))
    client.headers.update({"x-openampere": "1"})
    for path, body in (("/api/diagnostics/read", {"host": "127.0.0.1", "start": 0, "count": 1}),
                       ("/api/diagnostics/identify", {"host": "127.0.0.1"}), ("/api/diagnostics/search", {})):
        assert client.post(path, json=body).status_code == 401
    authed(client)
    answer = client.post("/api/diagnostics/read", json={"host": "8.8.8.8", "start": 0, "count": 1})
    assert answer.status_code == 400 and "Heimnetz" in answer.json()["detail"]
    assert client.post("/api/diagnostics/read", json={"host": "127.0.0.1", "start": 0, "count": 601}).status_code == 422
    assert client.post("/api/diagnostics/read",
                       json={"host": "127.0.0.1", "function": 6, "start": 0, "count": 1}).status_code == 422
