# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""The work mode of the FoxESS AC EV Charger (#464): the one register OpenAmpere writes to it, e.g. to unlock a charger
that went "locked". Only with the master switch on, not sent in test mode, written with function code 16, read back,
logged; the poll and every other write path still never write."""

import asyncio
import contextlib

import pytest
from fastapi.testclient import TestClient

from conftest import login
from openampere.runtime import Runtime
from openampere.simulator import SimulatedFoxCharger
from openampere.storage import Storage
from openampere.wallbox import modbus
from openampere.wallbox.base import WallboxUnreachable
from openampere.wallbox.modbus import ModbusWallbox
from openampere.wallbox.native import NativeWallboxes


@pytest.fixture(autouse=True)
def no_gap(monkeypatch):
    monkeypatch.setattr(modbus, "REQUEST_GAP_S", 0)


@contextlib.asynccontextmanager
async def simulated(**kwargs):
    charger = SimulatedFoxCharger(**kwargs)
    server = await asyncio.start_server(charger.serve_client, "127.0.0.1", 0)
    try:
        yield charger, server.sockets[0].getsockname()[1]
    finally:
        server.close()
        await server.wait_closed()


def setup(tmp_path, port: int, *, enabled: bool = True, dry_run: bool = False) -> tuple[Runtime, NativeWallboxes]:
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    runtime.config.control.enabled, runtime.config.control.dry_run = enabled, dry_run
    wallboxes = NativeWallboxes(runtime)
    wallboxes.save([{"name": "Garage", "model": "foxess_ac", "host": "127.0.0.1", "port": port}])
    return runtime, wallboxes


def work_mode_entries(runtime: Runtime) -> list[dict]:
    return [e for e in runtime.storage.control_log(50) if e["details"].get("to", {}).get("action") == "work_mode"]


async def test_a_locked_charger_is_unlocked_with_function_code_16_read_back_and_logged(tmp_path):
    async with simulated() as (charger, port):
        charger.handle(bytes([16, 0x30, 0x00, 0, 1, 2, 0, 2]))  # locked, as owners found their chargers
        charger.writes.clear()
        runtime, wallboxes = setup(tmp_path, port)
        wb = wallboxes.wallboxes[0]
        await wallboxes.read(wb)
        assert wallboxes.fresh(wb).work_mode == "locked" and charger.regs[0x1003] == 8
        result = await wallboxes.set_work_mode(wb.id, "controlled", source="admin")
        assert result == {"dry_run": False, "work_mode": "controlled"}
        assert charger.writes == [(0x3000, 0)] and charger.regs[0x1003] == 0
        assert set(charger.functions) == {3, 16}  # nothing but reads and this one write
        entry = work_mode_entries(runtime)[0]
        assert (entry["result"], entry["dry_run"]) == ("ok", False)
        assert entry["details"]["from"]["value"] == "locked" and entry["details"]["to"]["value"] == "controlled"


async def test_test_mode_logs_without_sending_and_the_master_switch_off_refuses(tmp_path):
    async with simulated() as (charger, port):
        runtime, wallboxes = setup(tmp_path, port, dry_run=True)
        wb = wallboxes.wallboxes[0]
        assert (await wallboxes.set_work_mode(wb.id, "locked"))["dry_run"] is True
        assert charger.writes == [] and 16 not in charger.functions
        assert work_mode_entries(runtime)[0]["result"] == "nicht gesendet (Testmodus)"

        runtime.config.control.enabled = False
        with pytest.raises(ValueError, match="Änderungen an Geräten sind ausgeschaltet"):
            await wallboxes.set_work_mode(wb.id, "controlled")
        assert charger.writes == []


async def test_unknown_modes_and_wallboxes_are_refused_before_connecting(tmp_path):
    async with simulated() as (charger, port):
        _runtime, wallboxes = setup(tmp_path, port)
        wb = wallboxes.wallboxes[0]
        with pytest.raises(ValueError, match="Unbekannter Betriebsmodus"):
            await wallboxes.set_work_mode(wb.id, "3")
        with pytest.raises(ValueError, match="gibt es nicht"):
            await wallboxes.set_work_mode("nope", "controlled")
        assert charger.functions == []


async def test_a_refused_write_is_logged_and_said(tmp_path, monkeypatch):
    async with simulated() as (charger, port):
        runtime, wallboxes = setup(tmp_path, port)
        wb = wallboxes.wallboxes[0]
        monkeypatch.setattr(charger, "handle", lambda pdu: bytes([pdu[0] | 0x80, 1]))  # illegal function
        with pytest.raises(ValueError, match="nicht angenommen"):
            await wallboxes.set_work_mode(wb.id, "controlled")
        assert work_mode_entries(runtime)[0]["result"].startswith("Fehler: Die Wallbox hat die Einstellung nicht")


async def test_a_value_that_does_not_read_back_is_reported(tmp_path, monkeypatch):
    async with simulated() as (charger, port):
        runtime, wallboxes = setup(tmp_path, port)
        wb = wallboxes.wallboxes[0]
        handle = charger.handle

        def ignore_writes(pdu: bytes) -> bytes:  # answers the write but keeps the old value
            return pdu[:5] if pdu[0] == 16 else handle(pdu)
        monkeypatch.setattr(charger, "handle", ignore_writes)
        with pytest.raises(ValueError, match="nicht übernommen"):
            await wallboxes.set_work_mode(wb.id, "controlled")
        assert work_mode_entries(runtime)[0]["result"] == "Rücklesen abweichend: 1"


async def test_a_reader_cannot_write_and_a_writer_only_its_register():
    async with simulated() as (charger, port):
        reader = ModbusWallbox("127.0.0.1", port, 1, ((0x3000, 7),))
        with pytest.raises(ValueError):
            await reader.write(0x3000, 0)
        writer = ModbusWallbox("127.0.0.1", port, 1, ((0x3000, 7),), writable=frozenset({0x3000}))
        with pytest.raises(ValueError):
            await writer.write(0x3001, 60)
        assert charger.functions == []


async def test_an_unreachable_charger_says_so():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    writer = ModbusWallbox("127.0.0.1", port, 1, (), writable=frozenset({0x3000}))
    with pytest.raises(WallboxUnreachable, match="Smartbox"):
        await writer.write(0x3000, 0)


def test_the_app_sets_it_and_offers_it_only_for_the_foxess_charger(tmp_path, monkeypatch):
    from openampere import api as api_module
    from openampere.api import create_app

    class Wallboxes(NativeWallboxes):
        def refresh(self, now=None):  # no background reads from a test
            pass
    monkeypatch.setattr(api_module, "NativeWallboxes", Wallboxes)

    async def run():
        async with simulated() as (charger, port):
            runtime = Runtime({}, Storage(tmp_path / "t.db"))
            runtime.config.control.enabled, runtime.config.control.dry_run = True, False
            web = login(TestClient(create_app(runtime), base_url="https://testserver"))
            saved = await asyncio.to_thread(web.put, "/api/wallboxes", json={"wallboxes": [
                {"name": "Garage", "model": "foxess_ac", "host": "127.0.0.1", "port": port}]})
            wb = saved.json()["wallboxes"][0]
            assert wb["work_modes"] == ["controlled", "plug_and_charge", "locked"]
            r = await asyncio.to_thread(web.post, f"/api/wallboxes/{wb['id']}/work-mode", json={"mode": "locked"})
            assert r.status_code == 200, r.text
            assert r.json()["work_mode"] == "locked" and charger.regs[0x3000] == 2
            r = await asyncio.to_thread(web.post, f"/api/wallboxes/{wb['id']}/work-mode", json={"mode": "x"})
            assert r.status_code == 400
            guest = TestClient(create_app(runtime), base_url="https://testserver")
            r = await asyncio.to_thread(guest.post, f"/api/wallboxes/{wb['id']}/work-mode",
                                        json={"mode": "controlled"})
            assert r.status_code in (401, 403) and charger.regs[0x3000] == 2
    asyncio.run(run())
