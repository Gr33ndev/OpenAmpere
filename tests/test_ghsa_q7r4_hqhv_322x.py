# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""A dé wallbox found by its device ID at a new address is moved there only after it answers there with its stored
local key, and only to an address in the home network that is not the inverter's; the move is written into the
control log and sent as a notification (GHSA-q7r4-hqhv-322x). A forged UDP announcement used to move it to the
sender without any proof."""

import asyncio
import contextlib
import socket

from openampere import tuya
from openampere.notify import Notifier
from openampere.runtime import Runtime
from openampere.simulator import SimulatedTuyaCharger
from openampere.storage import Storage
from openampere.wallbox import native as native_module
from openampere.wallbox.native import NativeWallboxes

KEY = "ab12cd34ef56gh78"
OTHER_KEY = "zz98yy76xx54ww32"
DEVICE_ID = "simulated0000000000"
OLD, NEW = "10.0.0.5", "10.0.0.6"


def closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.asynccontextmanager
async def device_at(key: str):
    """A Tuya device on 127.0.0.1 with this local key: the owner's wallbox, or a forger's device without its key."""
    charger = SimulatedTuyaCharger(key, plugged=True)
    server = await asyncio.start_server(charger.serve_client, "127.0.0.1", 0)
    try:
        yield server.sockets[0].getsockname()[1]
    finally:
        server.close()
        await server.wait_closed()


def setup(tmp_path, monkeypatch, new_port: int) -> tuple[Runtime, NativeWallboxes]:
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    runtime.config.control.enabled, runtime.config.control.dry_run = True, False
    wallboxes = NativeWallboxes(runtime)
    ports = {OLD: closed_port(), NEW: new_port}  # the stored address does not answer any more
    monkeypatch.setattr(wallboxes, "device",
                        lambda wb: tuya.TuyaDevice("127.0.0.1", wb.local_key, port=ports[wb.host], timeout=1))

    async def announced(*_args, **_kwargs):  # what anybody in the home network can send (public discovery key)
        return [{"device_id": DEVICE_ID, "host": NEW}]
    monkeypatch.setattr(tuya, "search", announced)
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    wallboxes.save([{"name": "Mobil", "host": OLD, "local_key": KEY, "device_id": DEVICE_ID, "mode": "pv"}])
    return runtime, wallboxes


async def away_for_a_while(wallboxes: NativeWallboxes) -> native_module.Live:
    live = native_module.Live(online=False, offline_since=0.0)  # unreachable for far more than SEARCH_AFTER_S
    await wallboxes.read(wallboxes.wallboxes[0], live)
    return live


def relocations(runtime: Runtime) -> list[dict]:
    return [e for e in runtime.storage.control_log(0) if e["action"] == "wallbox_relocated"]


async def test_a_forged_announcement_from_a_device_without_the_local_key_keeps_the_address(tmp_path, monkeypatch):
    async with device_at(OTHER_KEY) as port:
        runtime, wallboxes = setup(tmp_path, monkeypatch, port)
        await away_for_a_while(wallboxes)
    assert wallboxes.wallboxes[0].host == OLD
    assert relocations(runtime) == [] and runtime.storage.get_meta("wallbox_relocated") is None


async def test_a_forged_announcement_from_an_address_that_does_not_answer_keeps_the_address(tmp_path, monkeypatch):
    runtime, wallboxes = setup(tmp_path, monkeypatch, closed_port())
    await away_for_a_while(wallboxes)
    assert wallboxes.wallboxes[0].host == OLD
    assert relocations(runtime) == []


async def test_the_inverters_address_is_never_taken(tmp_path, monkeypatch):
    async with device_at(KEY) as port:
        runtime, wallboxes = setup(tmp_path, monkeypatch, port)
        runtime.config.inverter.host = NEW
        await away_for_a_while(wallboxes)
    assert wallboxes.wallboxes[0].host == OLD and relocations(runtime) == []


async def test_the_owners_wallbox_answering_with_its_key_is_moved_logged_and_notified(tmp_path, monkeypatch):
    async with device_at(KEY) as port:
        runtime, wallboxes = setup(tmp_path, monkeypatch, port)
        live = await away_for_a_while(wallboxes)
    wb = wallboxes.wallboxes[0]
    assert wb.host == NEW and live.retry_at == 0.0
    [entry] = relocations(runtime)
    assert entry["details"]["from"] == {"host": OLD} and entry["details"]["to"] == {"host": NEW}
    assert entry["details"]["id"] == wb.id and not entry["dry_run"] and entry["result"] == "ok"

    runtime.config.notify.ntfy_url = "https://ntfy.example/openampere-test"
    notifier = Notifier(runtime, wallboxes=wallboxes)
    pushed = []

    async def push(title, message, tags=""):
        pushed.append((title, message))
        return True
    monkeypatch.setattr(notifier, "push", push)
    now = runtime.storage.get_meta("wallbox_relocated")["ts"] + 60
    assert "Wallbox unter neuer Adresse" in await notifier.check(now=now)
    assert "Mobil" in dict(pushed)["Wallbox unter neuer Adresse"]
    assert "Wallbox unter neuer Adresse" not in await notifier.check(now=now + 60)  # only once
