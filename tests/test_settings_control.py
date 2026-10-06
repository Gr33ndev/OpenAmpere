import asyncio
import time
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

    # the battery may be emptied completely during an outage, and a floor of 0 % must not block other changes (#69)
    assert (await battery.write({"min_soc": 0}))["result"] == "ok" and sim.energy.min_soc == 0
    assert (await battery.write({"min_soc_on_grid": 24}))["result"] == "ok" and sim.energy.min_soc_on_grid == 24
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


def test_soc_limits():
    from openampere.control import check_limits
    check_limits({"min_soc": 0, "min_soc_on_grid": 24, "max_soc": 100})  # empty during an outage is allowed (#69)
    for values, message in (({"min_soc": -1}, "zwischen 0 und 100"), ({"min_soc_on_grid": 5}, "zwischen 10 und 100"),
                            ({"min_soc": 30, "min_soc_on_grid": 20}, "nicht unter der Untergrenze"),
                            ({"min_soc_on_grid": 50, "max_soc": 50}, "über der Notstrom-Reserve")):
        with pytest.raises(ValueError, match=message):
            check_limits(values)


def test_write_order_keeps_every_step_valid():
    from openampere.control import write_order
    current = {"min_soc": 10, "min_soc_on_grid": 20, "max_soc": 30}
    # raising everything: the upper limit has to move first
    assert write_order(current, {"min_soc": 40, "min_soc_on_grid": 50, "max_soc": 90}) == ["max_soc", "min_soc_on_grid", "min_soc"]
    # lowering everything: the lower limit first
    current = {"min_soc": 40, "min_soc_on_grid": 50, "max_soc": 90}
    assert write_order(current, {"min_soc": 10, "min_soc_on_grid": 15, "max_soc": 20}) == ["min_soc", "min_soc_on_grid", "max_soc"]


async def test_battery_write_detects_second_master_and_partial_writes(tmp_path, monkeypatch):
    from openampere import control as control_module
    from openampere.control import BatteryControl, WriteFailed
    monkeypatch.setattr(control_module, "VERIFY_AFTER_S", 0.3)
    sim, server, port = await start_sim()
    async with server:
        runtime = Runtime({}, Storage(tmp_path / "t.db"))
        try:
            await runtime.update_settings({"inverter.host": "127.0.0.1", "inverter.port": port,
                                           "inverter.poll_interval": 2, "control.enabled": True,
                                           "control.dry_run": False})
            await wait_connected(runtime)
            battery = BatteryControl(runtime)
            assert (await battery.write({"min_soc_on_grid": 40}))["result"] == "ok"

            # another energy manager writes its own reserve back
            sim.regs[sim.map.settings["min_soc_on_grid"].address] = 15
            sim.on_write(sim.map.settings["min_soc_on_grid"].address)
            await asyncio.sleep(0.6)
            state = await battery.read()
            assert state["external_change"] == {"expected": {"min_soc_on_grid": 40}, "found": {"min_soc_on_grid": 15}}
            assert runtime.storage.control_log()[0]["action"] == "battery_settings_check"

            # the work mode gets through, the SoC write is refused: the log records what the inverter holds
            original = sim.handle
            def refuse_soc(pdu):
                if pdu[0] in (6, 16) and int.from_bytes(pdu[1:3], "big") == sim.map.settings["max_soc"].address:
                    return bytes([pdu[0] | 0x80, 4])
                return original(pdu)
            sim.handle = refuse_soc
            with pytest.raises(WriteFailed):
                await battery.write({"work_mode": "backup", "max_soc": 90})
            entry = runtime.storage.control_log()[0]
            assert entry["action"] == "battery_settings" and "Fehler" in entry["result"]
            assert "Rücklesen abweichend" in entry["result"] and "max_soc" in entry["result"]
            assert sim.energy.work_mode.value == "backup"
        finally:
            await runtime.collector.stop()


async def test_rediscovery_finds_inverter_after_ip_change(tmp_path, monkeypatch):
    """The router hands out a new address: the app finds the device by its serial number."""
    from openampere import discovery
    sim, server, port = await start_sim()
    async with server:
        runtime = Runtime({}, Storage(tmp_path / "t.db"))
        try:
            await runtime.update_settings({"inverter.host": "127.0.0.1", "inverter.port": port,
                                           "inverter.poll_interval": 2})
            await wait_connected(runtime)
            rediscovery = discovery.Rediscovery(runtime)
            rediscovery.remember()
            assert runtime.storage.get_meta("known_device") == {"serial": "SN1", "host": "127.0.0.1"}

            # simulate: device gone from the old address since 5 minutes, found at 127.0.0.2
            runtime.collector.connected = False
            runtime.collector.disconnected_since = 1000.0
            async def fake_find(rt, prefix, p, serial):
                assert (prefix, p, serial) == ("127.0.0", port, "SN1")
                return "127.0.0.2"
            monkeypatch.setattr(discovery, "find_by_serial", fake_find)
            assert await rediscovery.check(1000.0 + 60) is None  # too early
            assert await rediscovery.check(1000.0 + 300) == "127.0.0.2"
            assert runtime.config.inverter.host == "127.0.0.2"
            assert runtime.storage.get_meta("relocated")["from"] == "127.0.0.1"
        finally:
            await runtime.collector.stop()


async def test_find_by_serial(tmp_path):
    from openampere import discovery
    sim, server, port = await start_sim()
    async with server:
        runtime = Runtime({}, Storage(tmp_path / "t.db"))
        assert await discovery.find_by_serial(runtime, "127.0.0", port, "SN1") == "127.0.0.1"
        assert await discovery.find_by_serial(runtime, "127.0.0", port, "OTHER") is None


def test_settings_conflict_between_two_devices(tmp_path, authed):
    client = authed(TestClient(create_app(Runtime({}, Storage(tmp_path / "t.db")))))
    revision = client.get("/api/settings").json()["revision"]
    assert client.put("/api/settings", json={"tariff.feed_in_ct": 7.5, "_revision": revision}).status_code == 200
    # a second device still shows the old form
    stale = client.put("/api/settings", json={"tariff.feed_in_ct": 9, "_revision": revision})
    assert stale.status_code == 409 and "anderen Gerät" in stale.json()["detail"]
    assert client.put("/api/settings", json={"timezone": "Mars/Olympus"}).status_code == 400


async def test_diagnostics_report(tmp_path):
    from openampere.diagnostics import Diagnostics, report_markdown
    sim, server, port = await start_sim()
    async with server:
        runtime = Runtime({}, Storage(tmp_path / "t.db"))
        try:
            await runtime.update_settings({"inverter.host": "127.0.0.1", "inverter.port": port, "inverter.poll_interval": 2})
            await wait_connected(runtime)
            report = await Diagnostics(runtime).run(connection_test=True)
            checks = {c["id"]: c for c in report["checks"]}
            assert checks["blocks"]["status"] == "ok"
            assert checks["block_37609"]["status"] == "ok"
            assert checks["export_limit"]["summary"] == "6000 W"
            assert checks["connections"]["details"]["main_connection_survived"]
            assert report["device"]["serial"] != "SN1"  # masked unless asked
            assert "OpenAmpere-Diagnose" in report_markdown(report)
            # readable texts (#19): yes/no instead of a raw value, temperature in °C, plural only for several
            assert checks["bms1"]["summary"] == "ja"
            assert checks["temp_scale"]["status"] == "ok" and "°C" in checks["temp_scale"]["summary"]
            assert checks["night"]["summary"].startswith(("0 Abbrüche", "1 Abbruch,", "2 Abbrüche"))
            warn = {**report, "checks": report["checks"] + [{"id": "x", "title": "Beispiel (1)", "status": "warn",
                                                             "summary": "bitte prüfen"}]}
            assert report_markdown(warn).index("### Zu prüfen") < report_markdown(warn).index("### Alle Prüfungen")
        finally:
            await runtime.collector.stop()


async def test_diagnostics_explain_who_uses_remote_control(tmp_path):
    """#126: the check says whether nobody, OpenAmpere itself or another device controls the battery, with values."""
    from openampere.diagnostics import Diagnostics
    sim, server, port = await start_sim()
    async with server:
        runtime = Runtime({}, Storage(tmp_path / "t.db"))
        try:
            await runtime.update_settings({"inverter.host": "127.0.0.1", "inverter.port": port, "inverter.poll_interval": 2})
            await wait_connected(runtime)

            async def remote():
                return next(c for c in (await Diagnostics(runtime).run())["checks"] if c["id"] == "remote")

            check = await remote()
            assert check["status"] == "ok" and check["summary"].startswith("aus")

            # another device (e.g. the previous smartbox) commands 3000 W charging
            settings = sim.map.settings
            sim._put("remote_timeout", 180, settings)
            sim._put("remote_power", -3000, settings)
            sim._put("remote_enable", 1, settings)
            check = await remote()
            assert check["status"] == "warn" and "anderes Gerät" in check["summary"]
            assert "3000 W Laden" in check["summary"] and "180 s" in check["summary"]
            assert "laufend erneuert" in check["summary"] and "Seit mindestens" not in check["summary"]
            assert "Smartbox" in check["hint"] and check["details"]["power_w"] == -3000
            assert "hat das hier noch nie getan" in check["hint"]

            # #135: held at 0 W for a while, seen by the background job; OpenAmpere charged once, long ago
            sim._put("remote_power", 0, settings)
            diagnostics = Diagnostics(runtime)
            await diagnostics.watch_remote(now=time.time() - 3 * 3600)
            await diagnostics.watch_remote(now=time.time())
            runtime.storage.log_control("grid_charging", {}, False, "Laden gestartet (im Ladefenster)")
            runtime.storage.log_control("grid_charging", {}, True, "würde laden – Testmodus")
            check = await remote()
            assert check["status"] == "warn" and "weder Laden noch Entladen" in check["summary"]
            assert "Seit mindestens 3 Stunden durchgehend an" in check["summary"]
            assert "zuletzt am" in check["hint"] and "dauerhaft bei 0 W" in check["hint"]

            # a leftover of OpenAmpere's own charging from before a restart: ends by itself
            runtime.storage.set_meta("remote_command", {"ts": time.time() - 30, "power_w": -3000})
            check = await remote()
            assert check["status"] == "ok" and "Rest des Ladens aus dem Netz von OpenAmpere" in check["summary"]
            runtime.storage.set_meta("remote_command", {"ts": time.time() - 3600, "power_w": -3000})
            assert (await remote())["status"] == "warn"  # an hour ago: the watchdog has long ended it

            # the same, but OpenAmpere started it (charging from the grid)
            driver = getattr(runtime.collector.driver, "_driver", runtime.collector.driver)
            driver._remote_owned = True
            check = await remote()
            assert check["status"] == "ok" and "OpenAmpere lädt aus dem Netz" in check["summary"]

            # switched off: the background job forgets since when it was on
            sim._put("remote_enable", 0, settings)
            await diagnostics.watch_remote(now=time.time() + 600)
            assert runtime.storage.get_meta("remote_seen")["on_since"] is None
        finally:
            await runtime.collector.stop()
