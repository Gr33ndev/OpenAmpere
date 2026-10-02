import asyncio
from datetime import datetime

import pytest

from openampere.charging import GridCharging
from openampere.runtime import Runtime
from openampere.storage import QUARTER, Storage

from test_settings_control import start_sim, wait_connected


async def connected(tmp_path):
    sim, server, port = await start_sim()
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    await runtime.update_settings({"inverter.host": "127.0.0.1", "inverter.port": port, "inverter.poll_interval": 2,
                                   "control.enabled": True})
    await wait_connected(runtime)
    return sim, server, runtime


async def test_window_charging_respects_test_mode_and_stops(tmp_path):
    sim, server, runtime = await connected(tmp_path)
    async with server:
        try:
            charging = GridCharging(runtime)
            with pytest.raises(ValueError, match="rechtlichen"):
                charging.save({"enabled": True, "mode": "window"})
            charging.save({"enabled": True, "legal_confirmed": True, "mode": "window", "window_start": 0,
                           "window_end": 0, "target_soc": 100, "power_w": 3000})
            # window 0-0 covers the whole day; test mode: only logged
            await charging.tick()
            assert not sim.energy.remote_enabled
            assert runtime.storage.control_log()[0]["dry_run"]

            await runtime.update_settings({"control.dry_run": False})
            await charging.tick()
            assert charging.active and sim.energy.remote_enabled and sim.energy.remote_power_w == -3000

            charging.save({**charging.view()["settings"], "enabled": False})
            await charging.tick()
            assert not charging.active and not sim.energy.remote_enabled
            results = [e["result"] for e in runtime.storage.control_log() if e["action"] == "grid_charging"]
            assert results[0].startswith("Laden beendet") and results[1].startswith("Laden gestartet")
        finally:
            await runtime.collector.stop()


async def test_cheapest_quarters_are_chosen(tmp_path):
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    runtime.tariffs.save([{"valid_from": "2020-01-01", "kind": "dynamic", "surcharge_ct": 20, "feed_in_ct": 8}])
    now = datetime(2026, 6, 1, 20, 0, tzinfo=runtime.tz).timestamp()
    # prices for the night: cheapest at 2-3 o'clock
    rows = [(int(now) + i * QUARTER, 100.0 - (40 if 24 <= i < 28 else 0)) for i in range(40)]
    runtime.storage.save_prices(rows)
    charging = GridCharging(runtime)
    charging.save({"enabled": False, "mode": "cheapest", "ready_by": 6, "target_soc": 60, "battery_kwh": 10,
                   "power_w": 4000})
    plan = charging.plan(now, 50)  # 1 kWh / 0.92 -> 1087 Wh -> 2 quarters at 4 kW
    assert plan["quarters"] == [int(now) + 24 * QUARTER, int(now) + 25 * QUARTER]
    assert charging.plan(now, 60)["quarters"] == []
    charging.save({**charging.view(now)["settings"], "max_price_ct": 10})
    assert charging.plan(now, 50)["reason"] == "Kein Zeitraum unter deinem Höchstpreis"
