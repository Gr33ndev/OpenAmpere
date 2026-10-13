# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Hardening of grid charging and the surplus control (#351)."""

import asyncio
import math
import shutil
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi import HTTPException

from openampere import consumers as consumers_module
from openampere import discovery, external
from openampere import notify as notify_module
from openampere.charging import MIN_CHARGE_W, GridCharging, validate
from openampere.consumers import SurplusControl
from openampere.evcc import min_power_w, normalize_loadpoint
from openampere.notify import Notifier
from openampere.runtime import Runtime
from openampere.storage import Storage

from test_consumers import PUMP_CONFIG, PUMP_OFF, ROD_CONFIG, pump_on, runtime_with
from test_ghsa_compose_helper_binds import COMPOSE, enabled, install_compose

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def runtime(tmp_path):
    return Runtime({}, Storage(tmp_path / "t.db"))


# ---- grid charging ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("raw", [{"max_price_ct": math.nan}, {"max_price_ct": math.inf}, {"max_price_ct": "nan"},
                                 {"max_price_ct": -math.inf}, {"battery_kwh": math.nan}, {"battery_kwh": math.inf}])
def test_charging_refuses_nan_and_infinity(raw):
    with pytest.raises(ValueError, match="Ungültige Einstellungen"):
        validate(raw, None)


def test_stored_charging_power_is_kept_below_the_battery_and_inverter_limit(runtime):
    """A restored backup (or a battery limit set lower later) must not lead to a command above the limits."""
    runtime.storage.set_meta("grid_charging", {"enabled": True, "legal_confirmed": True, "power_w": 12_000})
    charging = GridCharging(runtime)
    assert charging.settings.power_w == 12_000  # nothing known about the limits yet
    runtime.config.battery.max_charge_kw = 5.5
    assert charging.settings.power_w == 5_500
    assert charging.settings.enabled  # lowered, not switched off
    runtime.collector.device = SimpleNamespace(rated_power_w=4_000)
    assert charging.settings.power_w == 4_000
    assert charging.view()["settings"]["power_w"] == 4_000  # the app shows what is used
    # the stored value stays: a bigger battery limit later allows more again
    assert runtime.storage.get_meta("grid_charging")["power_w"] == 12_000


def test_stored_charging_is_off_when_the_allowed_power_is_below_the_minimum(runtime):
    runtime.storage.set_meta("grid_charging", {"enabled": True, "legal_confirmed": True, "power_w": 3_000})
    runtime.config.battery.max_charge_kw = 0.1
    settings = GridCharging(runtime).settings
    assert not settings.enabled and settings.power_w >= MIN_CHARGE_W


# ---- evcc --------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("raw, current, phases", [
    ({"minCurrent": -16, "phasesActive": -3}, 6.0, 1),
    ({"effectiveMinCurrent": -1, "minCurrent": 10}, 6.0, 1),
    ({"minCurrent": 100, "phasesActive": 5}, 32.0, 3),
    ({"minCurrent": 0.5, "phasesActive": 2}, 1.0, 2),
    ({"minCurrent": math.nan, "phasesActive": math.nan}, 6.0, 1),
    ({"minCurrent": math.inf, "phasesActive": math.inf}, 6.0, 1),
    ({"minCurrent": 8, "phasesActive": 3}, 8.0, 3),
])
def test_evcc_minimum_current_and_phases_are_limited(raw, current, phases):
    lp = normalize_loadpoint(1, raw)
    assert (lp["min_current_a"], lp["phases"]) == (current, phases)
    assert min_power_w(lp) > 0  # the room kept for a car can never be negative (phantom surplus)


# ---- my-PV address check ------------------------------------------------------------------------------------------

def test_heating_rod_at_the_inverters_address_is_refused_also_by_another_name():
    rod = {"name": "Heizstab", "kind": "mypv", "host": "localhost", "port": 502}
    with pytest.raises(ValueError, match="Adresse des Wechselrichters"):
        consumers_module.validate([rod], ("127.0.0.1", 502))
    with pytest.raises(ValueError, match="Adresse des Wechselrichters"):
        consumers_module.validate([{**rod, "host": "[::1]"}], ("::1", 502))
    assert consumers_module.validate([rod], ("127.0.0.1", 1502))  # another port: another device
    assert consumers_module.validate([{**rod, "host": "192.0.2.30"}], ("192.0.2.20", 502))


def test_an_unresolvable_name_is_compared_as_written(monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("no name server")
    monkeypatch.setattr(consumers_module.socket, "getaddrinfo", fail)
    rod = {"name": "Heizstab", "kind": "mypv", "host": " Inverter.local ", "port": 502}
    with pytest.raises(ValueError, match="Adresse des Wechselrichters"):
        consumers_module.validate([rod], ("inverter.local", 502))
    assert consumers_module.validate([{**rod, "host": "rod.local"}], ("inverter.local", 502))


# ---- surplus control: switching off first ---------------------------------------------------------------------------

class SlowRod:
    def __init__(self, calls):
        self.calls = calls

    async def read(self):
        await asyncio.sleep(0.3)
        self.calls.append("rod read")
        raise ConnectionError("no reading")

    async def set_power(self, watts):
        self.calls.append(("rod", watts))

    def close(self):
        pass


@pytest.mark.parametrize("situation", ["control off", "no readings"])
async def test_relays_are_switched_off_before_slow_heating_rods_are_read(tmp_path, monkeypatch, situation):
    calls = []
    runtime = runtime_with(tmp_path, monkeypatch, calls)
    control = SurplusControl(runtime)
    control.save([PUMP_CONFIG])
    await pump_on(control, runtime, calls)
    monkeypatch.setattr(consumers_module, "make_heating_rod", lambda c: SlowRod(calls))
    control.save([{**PUMP_CONFIG, "id": control.consumers[0].id}, {**ROD_CONFIG, "host": "rod1.local"},
                  {**ROD_CONFIG, "name": "Heizstab 2", "host": "rod2.local"}])
    if situation == "control off":
        runtime.config.control.enabled = False
    else:
        runtime.collector.latest = None
    started = time.monotonic()
    await control.tick(now=1060)
    assert calls[1:] == [PUMP_OFF, "rod read", "rod read"]  # switched off first, then the heating rods are read
    assert time.monotonic() - started < 0.55  # both heating rods at once, not one after the other


# ---- app API: write limit beyond a restart ----------------------------------------------------------------------------

def test_battery_write_limit_survives_a_restart(runtime):
    limit = external.RateLimit(runtime.storage, now=10_000)
    for i in range(external.BATTERY_WRITES_PER_HOUR):
        limit.check("battery:min_soc", external.BATTERY_WRITES_PER_HOUR, now=10_000 + i)
    limit.check("grid_charging", 1, now=10_000)  # only the battery settings are kept

    restarted = external.RateLimit(runtime.storage, now=10_100)
    with pytest.raises(HTTPException) as refused:
        restarted.check("battery:min_soc", external.BATTERY_WRITES_PER_HOUR, now=10_100)
    assert refused.value.status_code == 429
    restarted.check("battery:max_soc", external.BATTERY_WRITES_PER_HOUR, now=10_100)  # counted per setting
    restarted.check("grid_charging", 1, now=10_100)
    # an hour later the old writes are gone, also from storage
    later = external.RateLimit(runtime.storage, now=10_000 + 3_610)
    for _ in range(external.BATTERY_WRITES_PER_HOUR):
        later.check("battery:min_soc", external.BATTERY_WRITES_PER_HOUR, now=10_000 + 3_610)
    assert runtime.storage.get_meta(external.KEPT_META)["battery:min_soc"] == [10_000 + 3_610] * 6


def test_stored_write_times_from_the_future_or_broken_are_ignored(runtime):
    runtime.storage.set_meta(external.KEPT_META, {"battery:min_soc": [99_999, "x", True, None] + [10_000] * 2,
                                                  "grid_charging": [10_000] * 99, "battery:max_soc": "broken"})
    limit = external.RateLimit(runtime.storage, now=10_100)
    for _ in range(external.BATTERY_WRITES_PER_HOUR - 2):
        limit.check("battery:min_soc", external.BATTERY_WRITES_PER_HOUR, now=10_100)
    with pytest.raises(HTTPException):
        limit.check("battery:min_soc", external.BATTERY_WRITES_PER_HOUR, now=10_100)
    limit.check("grid_charging", 1, now=10_100)
    limit.check("battery:max_soc", 1, now=10_100)


def test_a_failing_storage_does_not_break_the_write_limit(runtime, monkeypatch):
    limit = external.RateLimit(runtime.storage)

    def full(*args):
        raise OSError("disk full")
    monkeypatch.setattr(runtime.storage, "set_meta", full)
    limit.check("battery:min_soc", 1, now=1000)
    with pytest.raises(HTTPException):
        limit.check("battery:min_soc", 1, now=1001)


# ---- rediscovery: the owner is told --------------------------------------------------------------------------------

async def test_a_new_inverter_address_is_logged_before_it_is_used(monkeypatch, runtime):
    runtime.config.inverter.host = "192.0.2.5"
    runtime.storage.set_meta("known_device", {"serial": "SN1", "host": "192.0.2.5"})
    runtime.collector.connected, runtime.collector.disconnected_since = False, 1000.0

    async def find(rt, prefix, port, serial):
        return "192.0.2.7"
    monkeypatch.setattr(discovery, "find_by_serial", find)
    seen = {}
    update = runtime.update_settings

    async def update_settings(changes, source=None):
        seen["log"] = runtime.storage.control_log()
        return await update(changes, source)
    monkeypatch.setattr(runtime, "update_settings", update_settings)

    assert await discovery.Rediscovery(runtime).check(1000.0 + 300) == "192.0.2.7"
    entry = seen["log"][0]
    assert entry["action"] == "inverter_relocated" and not entry["dry_run"]
    assert entry["details"] == {"from": {"host": "192.0.2.5"}, "to": {"host": "192.0.2.7"}}
    assert runtime.config.inverter.host == "192.0.2.7"
    assert "SN1" not in str(entry)


def notifier_with_posts(runtime, monkeypatch):
    runtime.config.notify.ntfy_url = "https://ntfy.example/openampere-test"
    posts = []

    class Response:
        def __init__(self, request):
            posts.append(request)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, n):
            return b""

    monkeypatch.setattr(notify_module, "open_without_redirect", lambda request, timeout: Response(request))
    return Notifier(runtime), posts


async def test_a_new_inverter_address_is_notified_once_without_the_addresses(monkeypatch, runtime):
    notifier, posts = notifier_with_posts(runtime, monkeypatch)
    runtime.collector.connected = True
    assert await notifier.check(now=5000) == []
    runtime.storage.set_meta("relocated", {"from": "192.0.2.5", "to": "192.0.2.7", "ts": 5000.0})
    assert await notifier.check(now=5030) == ["Wechselrichter unter neuer Adresse"]
    assert await notifier.check(now=5060) == []  # once
    assert b"192.0.2" not in posts[0].data  # addresses of the home network do not go to the ntfy server
    runtime.config.notify.on_unreachable = False
    runtime.storage.set_meta("relocated", {"from": "192.0.2.7", "to": "192.0.2.9", "ts": 6000.0})
    assert await notifier.check(now=6030) == []  # switched off with the messages about the connection


async def test_an_old_address_change_is_not_notified_when_notifications_are_switched_on_later(monkeypatch, runtime):
    runtime.storage.set_meta("relocated", {"from": "192.0.2.5", "to": "192.0.2.7", "ts": 5000.0})
    notifier, _ = notifier_with_posts(runtime, monkeypatch)
    runtime.collector.connected = True
    assert await notifier.check(now=5000 + notify_module.RELOCATED_RECENT_S + 1) == []


# ---- stopping the container --------------------------------------------------------------------------------------

def test_home_assistant_waits_long_enough_for_the_shutdown():
    config = yaml.safe_load((ROOT / "ha-app" / "config.yaml").read_text())
    assert config["timeout"] >= 30


def test_docker_waits_long_enough_for_the_shutdown():
    assert enabled(COMPOSE.read_text())["services"]["openampere"]["stop_grace_period"] == "30s"


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_the_installer_lets_docker_wait_long_enough_for_the_shutdown(tmp_path):
    assert install_compose(tmp_path)["services"]["openampere"]["stop_grace_period"] == "30s"
