import asyncio
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from openampere.api import create_app
from openampere.control import ConfirmationRequired, ControlDisabled, ExportLimitControl
from openampere.drivers.foxess.registers import H3_LEGACY, H3_NEW
from openampere.runtime import Runtime
from openampere.simulator import SimulatedInverter
from openampere.storage import Storage


async def start(register_map=H3_NEW, model="H3-10.0-Smart"):
    sim = SimulatedInverter(register_map, model, "SN1", strict_function=False, max_connections=3)
    sim.energy.step(datetime(2026, 6, 21, 13, 0), 600)
    sim.update_registers()
    server = await asyncio.start_server(sim.serve_client, "127.0.0.1", 0)
    return sim, server, server.sockets[0].getsockname()[1]


async def connected_runtime(tmp_path, port):
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    await runtime.update_settings({"inverter.host": "127.0.0.1", "inverter.port": port, "inverter.poll_interval": 2})
    for _ in range(100):
        if runtime.collector.connected:
            break
        await asyncio.sleep(0.1)
    return runtime


async def test_export_limit_rules(tmp_path):
    sim, server, port = await start()
    async with server:
        runtime = await connected_runtime(tmp_path, port)
        control = ExportLimitControl(runtime)
        assert await control.read() == {"supported": True, "limit_w": 6000, "rated_power_w": 10000,
                                        "rule": "unknown", "installed_kwp": 0.0, "legal_max_w": None}

        with pytest.raises(ControlDisabled):
            await control.write(10000, confirmed=True, reference="Schreiben vom 01.10.2026")
        await runtime.update_settings({"control.enabled": True, "control.dry_run": False})

        # raising without the grid operator's confirmation is refused
        with pytest.raises(ConfirmationRequired):
            await control.write(10000)
        with pytest.raises(ConfirmationRequired):
            await control.write(10000, confirmed=True, reference="")
        with pytest.raises(ValueError):
            await control.write(12000, confirmed=True, reference="Az. 4711")  # above rated power
        assert sim.energy.export_limit_w == 6000

        result = await control.write(10000, confirmed=True, reference="Netzbetreiber, Schreiben 01.10.2026, Az. 4711")
        assert result["result"] == "ok" and sim.energy.export_limit_w == 10000

        # lowering is always allowed
        result = await control.write(4000)
        assert result["result"] == "ok" and sim.energy.export_limit_w == 4000

        log = runtime.storage.control_log()
        assert [e["action"] for e in log[:2]] == ["export_limit", "export_limit"]
        assert log[1]["details"]["grid_operator_confirmation"].endswith("Az. 4711")
        assert log[0]["details"]["grid_operator_confirmation"] is None
        await runtime.collector.stop()



async def test_export_limit_follows_declared_rule_and_kwp(tmp_path):
    sim, server, port = await start()
    async with server:
        runtime = await connected_runtime(tmp_path, port)
        control = ExportLimitControl(runtime)
        try:
            await runtime.update_settings({"control.enabled": True, "control.dry_run": False,
                                           "pv.installed_kwp": 8, "grid.feed_in_rule": "limit_60"})
            # 60 % refer to the module power: 8 kWp -> 4800 W, not 60 % of the 10 kW inverter
            assert (await control.read())["legal_max_w"] == 4800
            with pytest.raises(ValueError, match="4800"):
                await control.write(7000, confirmed=True, reference="Az. 4711")  # consent does not override the law
            await control.write(3000)
            result = await control.write(4800)  # raising within the declared rule needs no further consent
            assert result["result"] == "ok" and sim.energy.export_limit_w == 4800

            # a fixed value from the grid operator: every increase needs the written consent again
            await runtime.update_settings({"grid.feed_in_rule": "operator"})
            with pytest.raises(ConfirmationRequired):
                await control.write(5000)

            # declared "no limit": raising is the operator's own responsibility
            await runtime.update_settings({"grid.feed_in_rule": "none"})
            assert (await control.write(10000))["result"] == "ok"

            rules = [e["details"]["to"].get("grid.feed_in_rule") for e in runtime.storage.control_log()
                     if e["action"] == "control_switches"]
            assert rules[:2] == ["none", "operator"]  # changing the rule is audited
        finally:
            await runtime.collector.stop()


async def test_export_limit_dry_run_and_unsupported(tmp_path):
    sim, server, port = await start()
    async with server:
        runtime = await connected_runtime(tmp_path, port)
        await runtime.update_settings({"control.enabled": True})  # dry run stays on
        result = await ExportLimitControl(runtime).write(10000, confirmed=True, reference="Az. 1")
        assert result["dry_run"] and sim.energy.export_limit_w == 6000
        await runtime.collector.stop()

    sim, server, port = await start(H3_LEGACY, " H3-10.0-E")
    async with server:
        runtime = await connected_runtime(tmp_path / "legacy", port)
        control = ExportLimitControl(runtime)
        assert (await control.read())["supported"] is False
        await runtime.update_settings({"control.enabled": True, "control.dry_run": False})
        with pytest.raises(ValueError):
            await control.write(5000)
        await runtime.collector.stop()


def test_export_limit_api_requires_confirmation(tmp_path, authed):
    # control switch is checked before anything else; invalid values are rejected by validation
    runtime = Runtime({}, Storage(tmp_path / "api.db"))
    client = TestClient(create_app(runtime))
    client.headers.update({"x-openampere": "1"})
    assert client.put("/api/grid/export-limit", json={"limit_w": 5000}).status_code == 401  # no password yet
    authed(client)
    assert client.put("/api/grid/export-limit", json={"limit_w": 5000}).status_code == 403  # control disabled
    assert client.put("/api/grid/export-limit", json={"limit_w": -1}).status_code == 422
