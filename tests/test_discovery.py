# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Error paths of the network search in the setup wizard, without a real network (#237)."""

import asyncio
import time
import socket
import struct
from types import SimpleNamespace

import pytest

from openampere import discovery
from openampere.drivers import registry
from openampere.drivers import modbus
from openampere.drivers.modbus import BUSY_MESSAGE, DeviceUnreachable, InverterBusy
from openampere.runtime import Runtime
from openampere.storage import Storage


@pytest.fixture
def runtime(tmp_path):
    return Runtime({}, Storage(tmp_path / "t.db"))


@pytest.fixture(autouse=True)
def no_mdns(monkeypatch):
    """No multicast from the tests: nothing announces itself unless a test says so."""
    monkeypatch.setattr(discovery.probe, "browse", lambda services, seconds: [])


def open_ports(monkeypatch, *hosts):
    async def port_open(host, port, timeout):
        return host in hosts
    monkeypatch.setattr(discovery, "_port_open", port_open)


def device(serial="SN1", **extra):
    return SimpleNamespace(model="H3-10.0", manufacturer="FoxESS", driver="foxess", unit=247, serial=serial, **extra)


def test_parse_prefix():
    assert len(discovery.parse_prefix("192.168.178")) == 254
    assert discovery.parse_prefix(" 192.168.178. ")[0] == "192.168.178.1"
    assert len(discovery.parse_prefix("10.0.0.0/22")) == 1022
    with pytest.raises(ValueError, match="z. B. 192.168.178"):
        discovery.parse_prefix("192.168")
    with pytest.raises(ValueError, match="Nur private Netze"):
        discovery.parse_prefix("8.8.8")
    with pytest.raises(ValueError, match="Nur private Netze"):
        discovery.parse_prefix("10.0.0.0/21")


def test_local_prefixes_ignore_loopback_public_and_docker(monkeypatch):
    class FakeSocket:
        def __init__(self, *args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def connect(self, address):
            pass

        def getsockname(self):
            return ("192.168.178.20", 40000)

    infos = [(socket.AF_INET, 0, 0, "", (address, 0))
             for address in ("127.0.1.1", "172.17.0.2", "8.8.8.8", "10.1.2.3", "192.168.178.20")]
    monkeypatch.setattr(discovery.socket, "socket", FakeSocket)
    monkeypatch.setattr(discovery.socket, "getaddrinfo", lambda *args: infos)
    assert discovery.local_prefixes() == ["10.1.2", "192.168.178"]


def test_local_prefixes_without_network(monkeypatch):
    def fail(*args):
        raise OSError("network is unreachable")
    monkeypatch.setattr(discovery.socket, "socket", fail)
    monkeypatch.setattr(discovery.socket, "getaddrinfo", fail)
    assert discovery.local_prefixes() == []


async def test_scan_lists_hosts_that_do_not_answer_as_inverter(monkeypatch, runtime):
    open_ports(monkeypatch, "10.0.0.5")

    async def detect(host, port, unit):
        raise ConnectionError("no Modbus answer")
    monkeypatch.setattr(registry, "detect", detect)
    found = await discovery.scan(runtime, "10.0.0")
    assert found == [{"host": "10.0.0.5", "port": 502, "busy": False, "wallbox": False, "model": None,
                      "manufacturer": None, "driver": None, "unit": None, "serial": None}]


async def test_scan_marks_an_announced_foxess_wallbox_without_connecting_to_it(monkeypatch, runtime):
    """On EKD systems the old smartbox holds the wallbox, which then looked like a busy inverter (#433)."""
    monkeypatch.setattr(discovery.ha_app, "active", lambda: False)
    monkeypatch.setattr(discovery.probe, "browse", lambda services, seconds: [
        {"service": "_modbus._tcp", "name": "evc-1", "host": "10.0.0.7", "port": 502},
        {"service": "_modbus._tcp", "name": "Inverter", "host": "10.0.0.5", "port": 502}])
    open_ports(monkeypatch, "10.0.0.5", "10.0.0.7", "10.0.0.9")
    asked = []

    async def detect(host, port, unit):
        asked.append(host)
        if host == "10.0.0.9":
            raise InverterBusy(BUSY_MESSAGE)
        return device()
    monkeypatch.setattr(registry, "detect", detect)
    found = {f["host"]: f for f in await discovery.scan(runtime, "10.0.0")}
    assert sorted(asked) == ["10.0.0.5", "10.0.0.9"]  # no connection to the wallbox
    assert (found["10.0.0.7"]["wallbox"], found["10.0.0.7"]["busy"], found["10.0.0.7"]["model"]) == (True, False, None)
    assert (found["10.0.0.5"]["wallbox"], found["10.0.0.5"]["model"]) == (False, "H3-10.0")
    assert (found["10.0.0.9"]["wallbox"], found["10.0.0.9"]["busy"]) == (False, True)  # not announced: neutral


async def test_scan_without_mdns_in_the_home_assistant_app(monkeypatch, runtime):
    monkeypatch.setattr(discovery.ha_app, "active", lambda: True)

    def browse(services, seconds):
        raise AssertionError("multicast does not reach the home network from the app")
    monkeypatch.setattr(discovery.probe, "browse", browse)
    open_ports(monkeypatch, "10.0.0.7")

    async def detect(host, port, unit):
        raise InverterBusy(BUSY_MESSAGE)
    monkeypatch.setattr(registry, "detect", detect)
    found = await discovery.scan(runtime, "10.0.0")
    assert [(f["busy"], f["wallbox"]) for f in found] == [(True, False)]


async def test_scan_goes_on_when_the_mdns_search_fails(monkeypatch, runtime):
    monkeypatch.setattr(discovery.ha_app, "active", lambda: False)

    def browse(services, seconds):
        raise OSError("no network card")
    monkeypatch.setattr(discovery.probe, "browse", browse)
    open_ports(monkeypatch, "10.0.0.5")

    async def detect(host, port, unit):
        return device()
    monkeypatch.setattr(registry, "detect", detect)
    assert [f["model"] for f in await discovery.scan(runtime, "10.0.0")] == ["H3-10.0"]


async def test_scan_reuses_the_connected_device(monkeypatch, runtime):
    open_ports(monkeypatch, "10.0.0.5")

    async def detect(*args):
        raise AssertionError("must not open a second connection")
    monkeypatch.setattr(registry, "detect", detect)
    runtime.config.inverter.host, runtime.config.inverter.port = "10.0.0.5", 502
    runtime.collector.connected, runtime.collector.device = True, device()
    found = await discovery.scan(runtime, "10.0.0")
    assert [(f["host"], f["model"], f["serial"]) for f in found] == [("10.0.0.5", "H3-10.0", "SN1")]


async def test_test_connection_errors(monkeypatch, runtime):
    async def unreachable(*args, **kwargs):
        raise DeviceUnreachable("refused")
    monkeypatch.setattr(registry, "detect_driver", unreachable)
    result = await discovery.test_connection(runtime, "10.0.0.5", 502, 0)
    assert not result["ok"] and "Keine Verbindung zu 10.0.0.5:502" in result["error"]

    async def timeout(*args, **kwargs):
        raise TimeoutError
    monkeypatch.setattr(registry, "detect_driver", timeout)
    result = await discovery.test_connection(runtime, "10.0.0.5", 502, 0)
    assert not result["ok"] and result["error"]


async def closing_server(reset: bool):
    """A Modbus TCP port whose only connection is taken: it accepts and closes at once, before any request
    (with FIN, or with RST like "Connection reset by peer"), as a FoxESS H3 does while the IQ box is connected."""
    async def handler(reader, writer):
        if reset:
            writer.get_extra_info("socket").setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
            writer.transport.abort()
        else:
            writer.close()
    return await asyncio.start_server(handler, "127.0.0.1", 0)


@pytest.mark.parametrize("reset", [False, True])
async def test_an_inverter_that_closes_every_connection_at_once_is_busy(monkeypatch, reset):
    monkeypatch.setattr(modbus, "CONNECT_SETTLE_S", 0.3)
    server = await closing_server(reset)
    try:
        with pytest.raises(InverterBusy) as err:
            await registry.detect("127.0.0.1", server.sockets[0].getsockname()[1])
    finally:
        server.close()
    assert modbus.friendly_error(err.value) == BUSY_MESSAGE
    # other programs first, then the IQ box, and why a connected box is a problem anyway
    assert BUSY_MESSAGE.index("anderes Programm") < BUSY_MESSAGE.index("IQ-Box")
    assert "Die bisherige Smartbox setzt Einstellungen zurück" in BUSY_MESSAGE
    assert "Home Assistant mit einer eigenen Modbus-Einbindung" in BUSY_MESSAGE  # the cause in #311


async def test_a_device_that_keeps_the_connection_open_is_not_busy(monkeypatch):
    """A normal Modbus server keeps the connection open and waits for a request: not "busy"."""
    monkeypatch.setattr(modbus, "CONNECT_SETTLE_S", 0.3)

    async def handler(reader, writer):
        await reader.read()  # until the client closes
        writer.close()
    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    device = modbus.ModbusDevice("127.0.0.1", server.sockets[0].getsockname()[1], 1)
    try:
        client = await device._ensure_connected()
        assert client.connected
    finally:
        await device.close()
        server.close()


async def test_scan_and_test_connection_say_when_the_inverter_is_busy(monkeypatch, runtime):
    open_ports(monkeypatch, "10.0.0.5")

    async def busy(*args, **kwargs):
        raise InverterBusy("closed right after connecting")
    monkeypatch.setattr(registry, "detect", busy)
    monkeypatch.setattr(registry, "detect_driver", busy)
    found = await discovery.scan(runtime, "10.0.0")
    assert [(f["host"], f["busy"], f["model"]) for f in found] == [("10.0.0.5", True, None)]
    result = await discovery.test_connection(runtime, "10.0.0.5", 502, 0)
    assert result == {"ok": False, "error": BUSY_MESSAGE, "busy": True}


async def test_test_connection_without_first_reading(monkeypatch, runtime):
    """The device identifies itself, but the first reading fails: still usable, just without a sample."""
    closed = []

    async def read():
        raise ConnectionError("register block not available")

    async def close():
        closed.append(True)

    async def detect_driver(host, port, unit, *, timeout, only, token="", cert_sha256=""):
        assert only is None and timeout >= 3
        return device(), SimpleNamespace(read=read, close=close)
    monkeypatch.setattr(registry, "detect_driver", detect_driver)
    result = await discovery.test_connection(runtime, "10.0.0.5", 502, 0)
    assert result["ok"] and result["sample"] is None and result["device"]["serial"] == "SN1"
    assert closed == [True]


async def test_find_by_serial_skips_hosts_that_fail(monkeypatch, runtime):
    open_ports(monkeypatch, "10.0.0.5", "10.0.0.7")

    async def detect(host, port, unit, *, timeout, only=None, quick=None):
        if host == "10.0.0.5":
            raise OSError("connection reset")
        return device("SN7")
    monkeypatch.setattr(registry, "detect", detect)
    assert await discovery.find_by_serial(runtime, "10.0.0", 502, "SN7") == "10.0.0.7"


def unreachable_since(runtime, host="10.0.0.5"):
    runtime.config.inverter.host = host
    runtime.storage.set_meta("known_device", {"serial": "SN1", "host": host})
    runtime.collector.connected, runtime.collector.disconnected_since = False, 1000.0


async def test_rediscovery_keeps_settings_when_found_at_the_same_address(monkeypatch, runtime):
    unreachable_since(runtime)

    async def find(rt, prefix, port, serial):
        return "10.0.0.5"
    monkeypatch.setattr(discovery, "find_by_serial", find)
    assert await discovery.Rediscovery(runtime).check(1000.0 + 300) is None
    assert runtime.config.inverter.host == "10.0.0.5" and runtime.storage.get_meta("relocated") is None


@pytest.mark.parametrize("change", ["locked", "other_host", "hostname", "senec", "enphase"])
async def test_rediscovery_does_not_search(monkeypatch, runtime, change):
    unreachable_since(runtime)
    if change in ("senec", "enphase"):  # read through the web interface: the Modbus search never finds it (#547)
        runtime.config.inverter.driver = change
    elif change == "locked":
        runtime.locked.add("inverter.host")  # set by an environment variable
    elif change == "other_host":
        runtime.storage.set_meta("known_device", {"serial": "SN1", "host": "10.0.0.9"})
    else:
        unreachable_since(runtime, "inverter.local")

    async def find(*args):
        raise AssertionError("must not search")
    monkeypatch.setattr(discovery, "find_by_serial", find)
    assert await discovery.Rediscovery(runtime).check(1000.0 + 300) is None


async def test_with_a_fixed_driver_the_search_detects_only_that_one(monkeypatch, runtime):
    """#547: a FoxESS owner's search does not send the probes of every other vendor to every device."""
    open_ports(monkeypatch, "10.0.0.7")
    asked = []

    async def detect(host, port, unit, *, timeout, only=None, quick=None):
        asked.append((only, quick))
        return device("SN7")
    monkeypatch.setattr(registry, "detect", detect)
    runtime.config.inverter.driver = "foxess"
    assert await discovery.find_by_serial(runtime, "10.0.0", 502, "SN7") == "10.0.0.7"
    runtime.config.inverter.driver = "auto"
    assert await discovery.find_by_serial(runtime, "10.0.0", 502, "SN7") == "10.0.0.7"
    assert asked == [("foxess", True), (None, True)]  # the shortcuts for silent hosts also with a fixed driver


async def test_the_searches_detect_several_hosts_at_a_time(monkeypatch, runtime):
    """#549: a Modbus host that never answers takes about 45 s to detect; one after another the setup's search took
    that long for every such device in the network. Still one connection per host, results in address order."""
    hosts = [f"10.0.0.{n}" for n in range(1, 13)]
    open_ports(monkeypatch, *hosts)
    running, most, per_host = 0, 0, {}

    async def detect(host, port, unit, *, timeout=None, only=None, quick=None):
        nonlocal running, most
        running, per_host[host] = running + 1, per_host.get(host, 0) + 1
        most = max(most, running)
        await asyncio.sleep(0.4 if host == "10.0.0.2" else 0.05 if host == "10.0.0.3" else 0.2)
        running -= 1
        if host.endswith((".2", ".3")):
            return device("SN7")
        raise ConnectionError("no Modbus answer")
    monkeypatch.setattr(registry, "detect", detect)
    started = time.monotonic()
    found = await discovery.scan(runtime, "10.0.0")
    assert time.monotonic() - started < 1.2  # 12 hosts × 0.2 s one after another would be 2.4 s
    assert [r["host"] for r in found] == hosts and found[1]["serial"] == "SN7"
    assert most == discovery.DETECT_PARALLEL and set(per_host.values()) == {1}
    # the address search: the first match in address order, not the first to answer (.3 matches first); the hosts
    # after a match are not asked any more, once a place is free (single-client wallboxes, #547)
    per_host.clear()
    assert await discovery.find_by_serial(runtime, "10.0.0", 502, "SN7") == "10.0.0.2"
    assert "10.0.0.12" not in per_host and set(per_host.values()) == {1}
