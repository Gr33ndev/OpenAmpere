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
            assert runtime.storage.last_control("grid_charging", "Laden gestartet") is None  # test mode does not count
            assert runtime.storage.get_meta("remote_command") is None

            await runtime.update_settings({"control.dry_run": False})
            await charging.tick()
            assert charging.active and sim.energy.remote_enabled and sim.energy.remote_power_w == -3000
            # remembered beyond a restart, so the diagnostics can tell OpenAmpere's command from another device's (#135)
            assert runtime.storage.get_meta("remote_command")["power_w"] == -3000
            assert runtime.storage.last_control("grid_charging", "Laden gestartet") is not None

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


async def test_charging_power_is_limited_by_the_battery(tmp_path):
    """A 6.6 kWh battery allows 5.5 kW even on a 12 kW inverter (datasheet, #23)."""
    from openampere.charging import validate
    with pytest.raises(ValueError, match="5500 W"):
        validate({"power_w": 6000}, 12_000, 5_500)
    assert validate({"power_w": 5500}, 12_000, 5_500).power_w == 5500
    assert validate({"power_w": 12000}, 12_000, None).power_w == 12000  # battery limit unknown: the inverter's
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    await runtime.update_settings({"battery.max_charge_kw": 5.5})
    with pytest.raises(ValueError, match="zulässige Ladeleistung"):
        GridCharging(runtime).save({"power_w": 8000})


async def test_cheapest_quarters_with_a_time_tariff(tmp_path):
    """A night tariff without exchange prices: charging picks the night window (#24)."""
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    runtime.tariffs.save([{"valid_from": "2020-01-01", "kind": "time", "price_ct": 32, "feed_in_ct": 8,
                           "windows": [{"from": "02:00", "to": "04:00", "price_ct": 10}]}])
    now = datetime(2026, 6, 1, 20, 0, tzinfo=runtime.tz).timestamp()
    charging = GridCharging(runtime)
    charging.save({"enabled": False, "mode": "cheapest", "ready_by": 6, "target_soc": 60, "battery_kwh": 10,
                   "power_w": 4000})
    night = datetime(2026, 6, 2, 2, 0, tzinfo=runtime.tz).timestamp()
    assert charging.plan(now, 50)["quarters"] == [int(night), int(night) + QUARTER]


async def test_leftover_remote_control_is_switched_off_later(tmp_path):
    """#141: the watchdog may leave the remote control on. A switch-off that was skipped (restart, new driver object
    after an IP change) or failed is done later, but only for OpenAmpere's own values, never the smartbox's."""
    from openampere.drivers.modbus import ModbusTransientError
    sim, server, runtime = await connected(tmp_path)
    async with server:
        try:
            await runtime.update_settings({"control.dry_run": False})
            charging = GridCharging(runtime)
            charging.save({"enabled": True, "legal_confirmed": True, "mode": "window", "window_start": 0,
                           "window_end": 0, "target_soc": 100, "power_w": 3000})
            await charging.tick()
            assert charging.active and sim._get_setting("remote_enable") == 1
            driver = getattr(runtime.collector.driver, "_driver", runtime.collector.driver)

            # switching off fails once: logged, and done on the next tick
            write = driver._write
            async def broken(reg, value):
                raise ModbusTransientError("timeout")
            driver._write = broken
            charging.save({**charging.view()["settings"], "enabled": False})
            await charging.tick()
            assert not charging.active and sim._get_setting("remote_enable") == 1
            assert runtime.storage.control_log()[0]["result"].startswith("Beenden fehlgeschlagen")
            driver._write = write
            await charging.tick()
            assert sim._get_setting("remote_enable") == 0
            assert runtime.storage.get_meta("remote_command")["released"]

            # restart (or a new driver object): the in-memory flag is gone, the inverter still has 1 / 180 s
            charging.save({**charging.view()["settings"], "enabled": True})
            await charging.tick()
            assert sim._get_setting("remote_enable") == 1 and sim._get_setting("remote_timeout") == 180
            driver._remote_owned = None
            charging = GridCharging(runtime)
            charging.save({**charging.view()["settings"], "enabled": False})
            await charging.tick()
            assert sim._get_setting("remote_enable") == 0 and not sim.energy.remote_enabled
            assert runtime.storage.control_log()[0]["result"].startswith("Fernsteuerung nachträglich abgeschaltet")
            await charging.tick()  # nothing left to do: no further entries
            assert runtime.storage.control_log()[0]["result"].startswith("Fernsteuerung nachträglich abgeschaltet")

            # the smartbox took over (on = 5, 30 s): never switched off by OpenAmpere
            runtime.storage.set_meta("remote_command", {**runtime.storage.get_meta("remote_command"), "released": None})
            settings = sim.map.settings
            sim._put("remote_timeout", 30, settings)
            sim._put("remote_enable", 5, settings)
            await charging.tick()
            assert sim._get_setting("remote_enable") == 5
            assert runtime.storage.get_meta("remote_command")["released"]
        finally:
            await runtime.collector.stop()


async def test_values_brought_back_after_an_inverter_restart_are_switched_off(tmp_path):
    """#143: the inverter may restore OpenAmpere's values (1 / 180 s) after its own restart, after OpenAmpere had
    switched them off. For a few minutes after a new connection they are switched off again, others' never."""
    import time

    from openampere.charging import AFTER_CONNECT_S
    sim, server, runtime = await connected(tmp_path)
    async with server:
        try:
            await runtime.update_settings({"control.dry_run": False})
            charging = GridCharging(runtime)
            charging.save({"enabled": True, "legal_confirmed": True, "mode": "window", "window_start": 0,
                           "window_end": 0, "target_soc": 100, "power_w": 3000})
            await charging.tick()
            charging.save({**charging.view()["settings"], "enabled": False})
            await charging.tick()
            assert sim._get_setting("remote_enable") == 0 and runtime.storage.get_meta("remote_command")["released"]
            entries = len(runtime.storage.control_log())
            settings, collector = sim.map.settings, runtime.collector

            def restore(enable, timeout):
                sim._put("remote_power", 0, settings)
                sim._put("remote_timeout", timeout, settings)
                sim._put("remote_enable", enable, settings)

            # long-standing connection: read only, nothing written
            collector.connected_at = time.time() - 2 * AFTER_CONNECT_S
            restore(1, 180)
            await charging.tick()
            assert sim._get_setting("remote_enable") == 1

            # new connection (the inverter restarted): OpenAmpere's values are switched off and logged
            collector.connected_at = time.time()
            await charging.tick()
            assert sim._get_setting("remote_enable") == 0
            assert len(runtime.storage.control_log()) == entries + 1
            assert "wieder eingeschaltet" in runtime.storage.control_log()[0]["result"]
            await charging.tick()  # nothing left to do: no further entries
            assert len(runtime.storage.control_log()) == entries + 1

            # the smartbox's values are never touched
            restore(5, 30)
            await charging.tick()
            assert sim._get_setting("remote_enable") == 5
            # OpenAmpere never sent a command: not its values, even with 1 / 180 s
            runtime.storage.set_meta("remote_command", None)
            restore(1, 180)
            await charging.tick()
            assert sim._get_setting("remote_enable") == 1
        finally:
            await runtime.collector.stop()
