import asyncio
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from openampere.api import create_app
from openampere.config import build_config, validate
from openampere.drivers.foxess.registers import H3_NEW
from openampere.runtime import Runtime
from openampere.simulator import SimulatedInverter
from openampere.storage import Storage


def test_precedence_and_locking(monkeypatch):
    monkeypatch.setenv("OPENAMPERE_INVERTER_PORT", "1502")
    config, locked = build_config({"inverter": {"host": "a", "port": 1}}, {"inverter.host": "b", "inverter.port": 2})
    assert config.inverter.host == "b"  # saved settings beat the file
    assert config.inverter.port == 1502  # environment beats everything
    assert locked == {"inverter.port"}


def test_validate():
    assert validate({"inverter.port": "502", "control.enabled": "true"}) == {"inverter.port": 502, "control.enabled": True}
    for bad in ({"inverter.port": 70000}, {"inverter.register_map": "x"}, {"storage.path": "/etc"}):
        with pytest.raises(ValueError):
            validate(bad)


async def start_sim():
    sim = SimulatedInverter(H3_NEW, "H3-10.0-Smart", "SN1", strict_function=False, max_connections=3)
    sim.energy.step(datetime(2026, 6, 21, 13, 0), 600)
    sim.update_registers()
    server = await asyncio.start_server(sim.serve_client, "127.0.0.1", 0)
    return sim, server, server.sockets[0].getsockname()[1]


async def wait_connected(runtime, seconds=10):
    for _ in range(seconds * 10):
        if runtime.collector.connected and runtime.collector.latest:
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"not connected: {runtime.collector.last_error}")


async def test_settings_reconnect_and_battery_control(tmp_path):
    sim, server, port = await start_sim()
    async with server:
        runtime = Runtime({}, Storage(tmp_path / "t.db"))
        try:
            await _reconnect_and_control(runtime, sim, port)
        finally:
            await runtime.collector.stop()


async def _reconnect_and_control(runtime, sim, port):
    """Body of test_settings_reconnect_and_battery_control (collector is stopped by the caller)."""
    assert not runtime.collector.configured
    await runtime.update_settings({"inverter.host": "127.0.0.1", "inverter.port": port, "inverter.poll_interval": 2})
    await wait_connected(runtime)
    assert runtime.storage.get_settings()["inverter.host"] == "127.0.0.1"

    from openampere.control import BatteryControl, ControlDisabled
    battery = BatteryControl(runtime)
    assert (await battery.read())["work_mode"] == "self_use"
    with pytest.raises(ControlDisabled):
        await battery.write({"min_soc_on_grid": 30})

    await runtime.update_settings({"control.enabled": True})  # dry run stays on by default
    result = await battery.write({"min_soc_on_grid": 30})
    assert result["dry_run"] and sim.energy.min_soc_on_grid == 10

    await runtime.update_settings({"control.dry_run": False})
    result = await battery.write({"min_soc_on_grid": 30, "work_mode": "backup"})
    assert result["result"] == "ok"
    assert sim.energy.min_soc_on_grid == 30 and sim.energy.work_mode.value == "backup"
    with pytest.raises(ValueError):
        await battery.write({"max_soc": 25})  # below the reserve
    log = [e for e in runtime.storage.control_log() if e["action"] == "battery_settings"]
    assert [e["dry_run"] for e in log] == [False, True]
    switches = [e["details"]["to"] for e in runtime.storage.control_log() if e["action"] == "control_switches"]
    assert switches == [{"control.dry_run": False}, {"control.enabled": True}]  # newest first


async def test_setup_endpoints(tmp_path):
    sim, server, port = await start_sim()
    async with server:
        runtime = Runtime({}, Storage(tmp_path / "t.db"))
        from openampere import discovery
        ok = await discovery.test_connection(runtime, "127.0.0.1", port, 247)
        assert ok["ok"] and ok["device"]["model"] == "H3-10.0-Smart"
        bad = await discovery.test_connection(runtime, "127.0.0.1", 1, 247)
        assert not bad["ok"] and "Keine Verbindung" in bad["error"]
        found = await discovery.scan(runtime, "127.0.0", port=port)
        hit = next(d for d in found if d["host"] == "127.0.0.1")
        assert (hit["model"], hit["driver"], hit["unit"]) == ("H3-10.0-Smart", "foxess", 247)
        with pytest.raises(ValueError):
            discovery.parse_prefix("8.8.8")


def test_settings_api(tmp_path, authed):
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    client = TestClient(create_app(runtime))
    assert client.get("/api/status").json()["configured"] is False
    authed(client)
    view = client.get("/api/settings").json()
    assert view["values"]["control.enabled"] is False
    assert client.put("/api/settings", json={"tariff.feed_in_ct": 7.9}).json()["values"]["tariff.feed_in_ct"] == 7.9
    assert client.put("/api/settings", json={"storage.path": "/x"}).status_code == 400
    assert client.put("/api/battery/settings", json={"min_soc": 20}).status_code in (403, 503)
    assert client.get("/api/backup").status_code == 200
