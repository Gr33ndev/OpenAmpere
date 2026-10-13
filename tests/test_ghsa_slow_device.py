# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""A switched device or evcc that answers byte by byte: a request ends after a total deadline, and a slow device does
not hold back switching off the others (GHSA-wxgh-j9mh-32j6)."""

import socketserver
import threading
import time

import pytest

from openampere import consumers as consumers_module
from openampere import evcc as evcc_module
from openampere.consumers import SurplusControl
from openampere.evcc import Evcc
from openampere.runtime import Runtime
from openampere.storage import Storage

DEADLINE_S = 1.0
TRICKLE_S = 8.0  # how long the slow peer keeps sending; far longer than the deadline


class Peer:
    """A loopback HTTP peer: answers at once, or for the paths in `slow` sends its answer one byte at a time, each byte
    well within the socket timeout."""

    def __init__(self, slow: tuple[str, ...] = ()) -> None:
        self.requests: list[tuple[str, float]] = []
        self.stop = threading.Event()
        outer = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self) -> None:
                line = self.rfile.readline().decode(errors="replace").split()
                while self.rfile.readline() not in (b"\r\n", b"\n", b""):
                    pass
                path = line[1] if len(line) > 1 else ""
                outer.requests.append((path, time.monotonic()))
                try:
                    if path in slow:
                        self.wfile.write(b"HTTP/1.1 200 OK\r\n")
                        end = time.monotonic() + TRICKLE_S
                        while time.monotonic() < end and not outer.stop.is_set():
                            self.wfile.write(b"X")
                            outer.stop.wait(0.1)
                        self.wfile.write(b": 1\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}")
                    else:
                        self.wfile.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}")
                except OSError:
                    pass  # the client gave up

        socketserver.ThreadingTCPServer.daemon_threads = True
        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def first(self, path: str) -> float | None:
        return next((at for p, at in self.requests if p == path), None)

    def close(self) -> None:
        self.stop.set()
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def peers():
    slow, fast = Peer(slow=("/off",)), Peer()
    try:
        yield slow, fast
    finally:
        slow.close()
        fast.close()


@pytest.fixture(autouse=True)
def short_deadline(monkeypatch):
    monkeypatch.setattr(consumers_module, "SWITCH_DEADLINE_S", DEADLINE_S, raising=False)
    monkeypatch.setattr(evcc_module, "DEADLINE_S", DEADLINE_S, raising=False)


def test_a_device_answering_byte_by_byte_is_given_up_after_the_deadline(peers):
    slow, _fast = peers
    started = time.monotonic()
    with pytest.raises(OSError):
        consumers_module._call(slow.url + "/off")
    assert time.monotonic() - started < DEADLINE_S + 1.5


async def _two_relays_switched_on(tmp_path, slow: Peer, fast: Peer) -> tuple[Runtime, SurplusControl, list]:
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    runtime.config.control.enabled = True
    runtime.config.control.dry_run = False
    control = SurplusControl(runtime)
    devices = [{"name": "Langsam", "kind": "http", "url_on": slow.url + "/on", "url_off": slow.url + "/off",
                "power_w": 1000},
               {"name": "Schnell", "kind": "http", "url_on": fast.url + "/on", "url_off": fast.url + "/off",
                "power_w": 1000}]
    for c in control.save(devices):
        await control.switch(c, True, "von Hand", now=1000)
    assert all(control.states[c.id].switched_on for c in control.consumers)
    return runtime, control, devices


async def test_a_slow_device_does_not_delay_switching_off_the_others(tmp_path, peers):
    slow, fast = peers
    runtime, control, _devices = await _two_relays_switched_on(tmp_path, slow, fast)
    runtime.config.control.enabled = False
    started = time.monotonic()
    await control.tick(now=1060)
    took = time.monotonic() - started
    off = fast.first("/off")
    assert off is not None and off - started < DEADLINE_S  # not after the slow device's answer
    assert took < DEADLINE_S + 1.5  # the tick (and with it the lock) is not held while the slow device trickles
    slow_one, fast_one = control.consumers
    assert not control.states[fast_one.id].switched_on and control.states[fast_one.id].on is False
    assert control.states[slow_one.id].switched_on and control.states[slow_one.id].failures == 1  # tried again later
    log = runtime.storage.control_log()
    assert any(e["details"]["to"] == {"consumer": "Schnell", "on": False} and not e["dry_run"] for e in log)


async def test_a_slow_device_does_not_delay_switching_off_disabled_devices(tmp_path, peers):
    slow, fast = peers
    _runtime, control, devices = await _two_relays_switched_on(tmp_path, slow, fast)
    control.save([{**d, "id": c.id, "enabled": False} for d, c in zip(devices, control.consumers, strict=True)])
    started = time.monotonic()
    await control.tick(now=1060)
    off = fast.first("/off")
    assert off is not None and off - started < DEADLINE_S
    assert time.monotonic() - started < DEADLINE_S + 1.5


async def test_evcc_answering_byte_by_byte_is_given_up_after_the_deadline(tmp_path):
    peer = Peer(slow=("/api/state",))
    try:
        runtime = Runtime({}, Storage(tmp_path / "t.db"))
        await runtime.update_settings({"evcc.url": peer.url})
        evcc = Evcc(runtime)
        started = time.monotonic()
        await evcc.refresh()
        assert time.monotonic() - started < DEADLINE_S + 1.5
        assert evcc.error
    finally:
        peer.close()
