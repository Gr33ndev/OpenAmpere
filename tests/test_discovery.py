# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Error paths of the network search in the setup wizard, without a real network (#237)."""

import socket
from types import SimpleNamespace

import pytest

from openampere import discovery
from openampere.drivers import registry
from openampere.drivers.modbus import DeviceUnreachable
from openampere.runtime import Runtime
from openampere.storage import Storage


@pytest.fixture
def runtime(tmp_path):
    return Runtime({}, Storage(tmp_path / "t.db"))


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
    assert found == [{"host": "10.0.0.5", "port": 502, "model": None, "manufacturer": None, "driver": None,
                      "unit": None, "serial": None}]


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


async def test_test_connection_without_first_reading(monkeypatch, runtime):
    """The device identifies itself, but the first reading fails: still usable, just without a sample."""
    closed = []

    async def read():
        raise ConnectionError("register block not available")

    async def close():
        closed.append(True)

    async def detect_driver(host, port, unit, *, timeout, only):
        assert only is None and timeout >= 3
        return device(), SimpleNamespace(read=read, close=close)
    monkeypatch.setattr(registry, "detect_driver", detect_driver)
    result = await discovery.test_connection(runtime, "10.0.0.5", 502, 0)
    assert result["ok"] and result["sample"] is None and result["device"]["serial"] == "SN1"
    assert closed == [True]


async def test_find_by_serial_skips_hosts_that_fail(monkeypatch, runtime):
    open_ports(monkeypatch, "10.0.0.5", "10.0.0.7")

    async def detect(host, port, unit, *, timeout):
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


@pytest.mark.parametrize("change", ["locked", "other_host", "hostname"])
async def test_rediscovery_does_not_search(monkeypatch, runtime, change):
    unreachable_since(runtime)
    if change == "locked":
        runtime.locked.add("inverter.host")  # set by an environment variable
    elif change == "other_host":
        runtime.storage.set_meta("known_device", {"serial": "SN1", "host": "10.0.0.9"})
    else:
        unreachable_since(runtime, "inverter.local")

    async def find(*args):
        raise AssertionError("must not search")
    monkeypatch.setattr(discovery, "find_by_serial", find)
    assert await discovery.Rediscovery(runtime).check(1000.0 + 300) is None
