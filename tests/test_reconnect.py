# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Connecting again after the inverter was gone (#640): the waiting time is capped, and "Connect now" ends it."""

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from openampere import collector as collector_module
from openampere.api import create_app
from openampere.collector import Collector
from openampere.drivers.base import DeviceInfo, Snapshot
from openampere.runtime import Runtime
from openampere.storage import Storage

from conftest import login


class GoneDriver:
    """Refuses connections until `back` is set, like an inverter whose network cable is unplugged."""

    MIN_POLL_S = 0.0

    def __init__(self) -> None:
        self.back = False
        self.attempts = 0
        self.expected_serial = None

    async def connect(self) -> DeviceInfo:
        self.attempts += 1
        if not self.back:
            raise ConnectionError("cannot connect to inverter")
        return DeviceInfo(manufacturer="Test", model="T1")

    async def read(self) -> Snapshot:
        return Snapshot(timestamp=time.time())

    async def close(self) -> None:
        pass


async def wait_for(condition, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "condition not reached"
        await asyncio.sleep(0.01)


async def test_waiting_time_doubles_up_to_a_minute(tmp_path, monkeypatch):
    collector = Collector(GoneDriver(), Storage(tmp_path / "t.db"), 10, 30)
    delays: list[float] = []

    async def wait_retry(delay: float) -> bool:
        delays.append(delay)
        if len(delays) == 8:
            raise asyncio.CancelledError
        return False

    monkeypatch.setattr(collector, "_wait_retry", wait_retry)
    with pytest.raises(asyncio.CancelledError):
        await collector._run()
    assert delays == [5, 10, 20, 40, 60, 60, 60, 60]


async def test_connect_now_ends_the_wait_and_starts_over(tmp_path, monkeypatch):
    monkeypatch.setattr(collector_module, "RETRY_FIRST_S", 600.0)  # without the button the test would time out
    driver = GoneDriver()
    collector = Collector(driver, Storage(tmp_path / "t.db"), 10, 30)
    assert not collector.retry_now()  # not running yet
    collector.start()
    try:
        await wait_for(lambda: collector.last_error is not None)
        assert driver.attempts == 1
        driver.back = True
        assert collector.retry_now()
        await wait_for(lambda: collector.connected)
        assert driver.attempts == 2
        assert not collector.retry_now()  # connected: nothing to do
        events = [e["event"] for e in collector.storage.get_meta("connection_events")]
        assert events == ["getrennt", "jetzt_verbinden", "verbunden"]
    finally:
        await collector.stop()


async def test_connect_now_keeps_the_device_minimum_gap(tmp_path, monkeypatch):
    driver = GoneDriver()
    driver.MIN_POLL_S = 0.3  # e.g. a Senec, which restarts when asked too often (#521)
    collector = Collector(driver, Storage(tmp_path / "t.db"), 10, 30)
    collector._task = asyncio.current_task()  # counts as running
    collector.retry_now()
    started = time.monotonic()
    assert await collector._wait_retry(600)
    assert time.monotonic() - started >= 0.3
    assert not collector._retry_now.is_set()  # used up: the next wait takes its full time
    assert not await collector._wait_retry(0.01)


def test_reconnect_endpoint(tmp_path, monkeypatch):
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    client = login(TestClient(create_app(runtime)))
    response = client.post("/api/inverter/reconnect", json={})
    assert response.status_code == 409  # no inverter set up yet

    assert client.post("/api/inverter/reconnect?inverter=nope", json={}).status_code == 404

    runtime.collector.driver = GoneDriver()
    asked = []
    monkeypatch.setattr(runtime.collector, "retry_now", lambda: asked.append(True) or True)
    assert client.post("/api/inverter/reconnect", json={}).json() == {"ok": True, "retrying": True}
    assert client.post("/api/inverter/reconnect?inverter=main", json={}).json()["retrying"]
    assert len(asked) == 2


def test_reconnect_needs_login(tmp_path):
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    login(TestClient(create_app(runtime)))  # sets the password
    client = TestClient(create_app(runtime))
    client.headers.update({"x-openampere": "1"})
    assert client.post("/api/inverter/reconnect", json={}).status_code == 401


async def test_a_press_during_an_attempt_that_works_is_not_kept_for_the_next_outage(tmp_path, monkeypatch):
    monkeypatch.setattr(collector_module, "RETRY_FIRST_S", 600.0)
    driver = GoneDriver()
    gate = asyncio.Event()
    connect = driver.connect

    async def slow_connect() -> DeviceInfo:
        await gate.wait()
        return await connect()

    collector = Collector(driver, Storage(tmp_path / "t.db"), 10, 30)
    collector.start()
    try:
        await wait_for(lambda: collector.last_error is not None)
        driver.back, driver.connect = True, slow_connect
        assert collector.retry_now()  # ends the wait: the next attempt waits at the gate
        await wait_for(lambda: not collector._retry_now.is_set())
        assert collector.retry_now()  # pressed again while that attempt runs
        gate.set()
        await wait_for(lambda: collector.connected)
        assert not collector._retry_now.is_set()
    finally:
        await collector.stop()


def test_presses_are_noted_at_most_once_a_minute(tmp_path, monkeypatch):
    collector = Collector(GoneDriver(), Storage(tmp_path / "t.db"), 10, 30)
    collector._task = object()  # counts as running
    clock = [1000.0]
    monkeypatch.setattr(collector_module.time, "monotonic", lambda: clock[0])
    for _ in range(50):
        assert collector.retry_now()
    clock[0] += 61
    assert collector.retry_now()
    events = [e["event"] for e in collector.storage.get_meta("connection_events")]
    assert events == ["jetzt_verbinden", "jetzt_verbinden"]


def test_diagnostics_show_how_long_outages_lasted_and_whether_connect_now_was_pressed():
    from openampere.diagnostics import Diagnostics
    events = [{"ts": 1000, "event": "verbunden"},
              {"ts": 2000, "event": "getrennt"}, {"ts": 2600, "event": "verbunden"},
              {"ts": 5000, "event": "getrennt"}, {"ts": 5100, "event": "getrennt"},
              {"ts": 5400, "event": "jetzt_verbinden"}, {"ts": 5430, "event": "verbunden"},
              {"ts": 9000, "event": "getrennt"}]
    assert Diagnostics._outages(events) == [{"minutes": 10.0, "connect_now": False},
                                            {"minutes": 7.2, "connect_now": True},
                                            {"minutes": None, "connect_now": False}]
