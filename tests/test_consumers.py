# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
import asyncio
import logging
import os
import sqlite3

import pytest
from fastapi.testclient import TestClient

from openampere import consumers as consumers_module
from openampere.api import create_app
from openampere.consumers import SurplusControl, switch_url, validate
from openampere.drivers.base import Snapshot
from openampere.runtime import Runtime
from openampere.secretbox import SecretBox
from openampere.storage import Storage

from conftest import login

ROD = "rod.local"
PUMP = "pump.local"


def runtime_with(tmp_path, monkeypatch, calls):
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    runtime.config.control.enabled = True
    runtime.config.control.dry_run = False
    monkeypatch.setattr(consumers_module, "_call", calls.append)
    monkeypatch.setattr(type(runtime.collector), "stale", property(lambda self: False))
    return runtime


def reading(runtime, grid, battery=0.0, soc=90.0):
    runtime.collector.latest = Snapshot(timestamp=0, grid_power=grid, battery_power=battery, battery_soc=soc)


async def test_surplus_switches_by_priority_with_hysteresis(tmp_path, monkeypatch):
    calls = []
    runtime = runtime_with(tmp_path, monkeypatch, calls)
    control = SurplusControl(runtime)
    control.save([{"name": "Heizstab", "kind": "shelly2", "host": ROD, "power_w": 2000, "min_on_min": 10},
                  {"name": "Wärmepumpe", "kind": "shelly1", "host": PUMP, "power_w": 1000, "min_on_min": 0}])

    reading(runtime, grid=-2500)  # 2.5 kW export
    await control.tick(now=1000)
    assert calls == []  # needs two checks in a row (clouds)
    await control.tick(now=1030)
    assert calls == [f"http://{ROD}/rpc/Switch.Set?id=0&on=true"]

    reading(runtime, grid=-1300)  # heating rod runs, still 1.3 kW export -> heat pump too
    await control.tick(now=1060)
    await control.tick(now=1090)
    assert calls[-1] == f"http://{PUMP}/relay/0?turn=on"

    reading(runtime, grid=800)  # clouds: import
    await control.tick(now=1120)
    await control.tick(now=1150)
    assert calls[-1] == f"http://{PUMP}/relay/0?turn=off"  # lowest priority first
    await control.tick(now=1180)
    await control.tick(now=1210)
    assert len(calls) == 3  # heating rod keeps its minimum on time of 10 minutes
    await control.tick(now=1000 + 30 + 600)
    await control.tick(now=1000 + 60 + 600)
    assert calls[-1].endswith("on=false")


async def test_battery_first_and_test_mode(tmp_path, monkeypatch):
    calls = []
    runtime = runtime_with(tmp_path, monkeypatch, calls)
    control = SurplusControl(runtime)
    control.save([{"name": "Heizstab", "kind": "http", "url_on": f"http://{ROD}/on", "url_off": f"http://{ROD}/off",
                   "power_w": 2000, "battery_min_soc": 80}])
    reading(runtime, grid=0, battery=-3000, soc=50)  # battery takes everything, below 80 %
    await control.tick(now=0)
    await control.tick(now=30)
    assert calls == []
    reading(runtime, grid=0, battery=-3000, soc=85)  # battery full enough: its charging power may be used
    runtime.config.control.dry_run = True
    await control.tick(now=60)
    await control.tick(now=90)
    assert calls == [] and runtime.storage.control_log()[0]["dry_run"]


def test_validation():
    with pytest.raises(ValueError):
        validate([{"name": "x", "kind": "http", "url_on": "file:///etc/passwd", "url_off": "http://a/"}])
    with pytest.raises(ValueError):
        validate([{"name": "", "host": PUMP}])
    for unit in (0, 248):  # 0 is the broadcast address: every device behind a gateway
        with pytest.raises(ValueError, match="Geräteadresse"):
            validate([{"name": "Heizstab", "kind": "mypv", "host": "192.168.178.30", "unit": unit}])
    with pytest.raises(ValueError, match="Wechselrichters"):
        validate([{"name": "Heizstab", "kind": "mypv", "host": "192.168.178.20", "port": 502}], ("192.168.178.20", 502))
    assert validate([{"name": "Heizstab", "kind": "mypv", "host": "192.168.178.30"}], ("192.168.178.20", 502))
    c = validate([{"name": "Pumpe", "kind": "shelly1", "host": PUMP, "channel": 1}])[0]
    assert switch_url(c, False) == f"http://{PUMP}/relay/1?turn=off"


# #217: a relay that OpenAmpere switched on must not stay on when it should be off
PUMP_ON, PUMP_OFF = f"http://{PUMP}/relay/0?turn=on", f"http://{PUMP}/relay/0?turn=off"
PUMP_CONFIG = {"name": "Pumpe", "kind": "shelly1", "host": PUMP, "power_w": 1000, "min_on_min": 0, "min_off_min": 0}


async def pump_on(control, runtime, calls):
    reading(runtime, grid=-2000)
    await control.tick(now=1000)
    await control.tick(now=1030)
    assert calls == [PUMP_ON]


@pytest.mark.parametrize("change", ["disabled", "removed"])
async def test_a_device_switched_on_is_switched_off_when_disabled_or_removed(tmp_path, monkeypatch, change):
    calls = []
    runtime = runtime_with(tmp_path, monkeypatch, calls)
    control = SurplusControl(runtime)
    control.save([PUMP_CONFIG])
    await pump_on(control, runtime, calls)
    control.save([{**PUMP_CONFIG, "enabled": False}] if change == "disabled" else [])
    reading(runtime, grid=2000)
    await control.tick(now=1060)
    assert calls == [PUMP_ON, PUMP_OFF]
    await control.tick(now=1090)
    assert calls == [PUMP_ON, PUMP_OFF]  # once, then forgotten


async def test_a_device_that_was_not_reachable_is_switched_off_on_the_next_tick(tmp_path, monkeypatch):
    calls = []
    runtime = runtime_with(tmp_path, monkeypatch, calls)
    control = SurplusControl(runtime)
    control.save([PUMP_CONFIG])
    await pump_on(control, runtime, calls)
    control.save([])

    def offline(url):
        raise OSError("timed out")
    monkeypatch.setattr(consumers_module, "_call", offline)
    await control.tick(now=1060)
    monkeypatch.setattr(consumers_module, "_call", calls.append)
    await control.tick(now=1090)
    assert calls == [PUMP_ON, PUMP_OFF]


async def test_test_mode_still_switches_off_what_openampere_switched_on(tmp_path, monkeypatch):
    calls = []
    runtime = runtime_with(tmp_path, monkeypatch, calls)
    control = SurplusControl(runtime)
    control.save([PUMP_CONFIG])
    await pump_on(control, runtime, calls)
    runtime.config.control.dry_run = True
    reading(runtime, grid=2000)  # no surplus any more
    for now in (1060, 1090, 1120):
        await control.tick(now=now)
    assert calls == [PUMP_ON, PUMP_OFF]
    entry = runtime.storage.control_log()[0]
    assert not entry["dry_run"] and "trotz Testmodus" in entry["result"]
    # in test mode nothing is switched on, and nothing switched on only "on paper" is switched off for real
    reading(runtime, grid=-2000)
    for now in (1150, 1180, 1210):
        await control.tick(now=now)
    reading(runtime, grid=2000)
    for now in (1240, 1270, 1300):
        await control.tick(now=now)
    assert calls == [PUMP_ON, PUMP_OFF]


@pytest.mark.parametrize("situation", ["no surplus", "control off", "no readings", "switched off by hand"])
async def test_after_a_restart_a_device_switched_on_is_still_switched_off(tmp_path, monkeypatch, situation):
    calls = []
    runtime = runtime_with(tmp_path, monkeypatch, calls)
    control = SurplusControl(runtime)
    control.save([PUMP_CONFIG])
    await pump_on(control, runtime, calls)

    restarted = SurplusControl(runtime)  # same database, nothing in memory
    reading(runtime, grid=2000)
    if situation == "control off":
        runtime.config.control.enabled = False
    elif situation == "no readings":
        runtime.collector.latest = None
    elif situation == "switched off by hand":
        reading(runtime, grid=-2000)  # surplus, but the owner wants it off
        restarted.set_override(restarted.consumers[0].id, "off", now=2000)
    for now in (2000, 2030, 2060):
        await restarted.tick(now=now)
    assert calls == [PUMP_ON, PUMP_OFF]


async def test_a_device_never_switched_on_by_openampere_is_left_alone_after_a_restart(tmp_path, monkeypatch):
    calls = []
    runtime = runtime_with(tmp_path, monkeypatch, calls)
    SurplusControl(runtime).save([PUMP_CONFIG])
    restarted = SurplusControl(runtime)
    runtime.config.control.enabled = False
    await restarted.tick(now=2000)
    assert calls == []


# #243: further ways a device could stay on
ROD_CONFIG = {"name": "Heizstab", "kind": "mypv", "host": ROD, "power_w": 3000, "min_power_w": 500, "min_on_min": 0,
              "min_off_min": 0}


class FakeRod:
    def __init__(self, calls):
        self.calls = calls

    async def set_power(self, watts):
        self.calls.append(("rod", watts))

    async def read(self):
        raise ConnectionError("no reading")

    def close(self):
        pass


def offline(url):
    raise OSError("timed out")


@pytest.mark.parametrize("change", [{"host": ROD}, {"channel": 1}, {"kind": "mypv", "min_power_w": 500}])
async def test_the_old_relay_is_switched_off_when_the_device_is_changed(tmp_path, monkeypatch, change):
    calls = []
    runtime = runtime_with(tmp_path, monkeypatch, calls)
    monkeypatch.setattr(consumers_module, "make_heating_rod", lambda c: FakeRod(calls))
    control = SurplusControl(runtime)
    control.save([PUMP_CONFIG])
    await pump_on(control, runtime, calls)
    control.save([{**PUMP_CONFIG, "id": control.consumers[0].id, **change}])
    runtime.config.control.enabled = False
    await control.tick(now=1060)
    await control.tick(now=1090)
    assert calls[1:] == [PUMP_OFF]  # the old relay, once; the new one was never switched on
    assert runtime.storage.get_meta(consumers_module.SWITCHED_ON) == {}


async def test_a_changed_device_whose_old_relay_is_offline_is_switched_off_after_a_restart(tmp_path, monkeypatch):
    calls = []
    runtime = runtime_with(tmp_path, monkeypatch, calls)
    control = SurplusControl(runtime)
    control.save([PUMP_CONFIG])
    await pump_on(control, runtime, calls)
    control.save([{**PUMP_CONFIG, "id": control.consumers[0].id, "host": ROD}])
    monkeypatch.setattr(consumers_module, "_call", offline)
    runtime.config.control.enabled = False
    await control.tick(now=1060)

    monkeypatch.setattr(consumers_module, "_call", calls.append)
    restarted = SurplusControl(runtime)
    await restarted.tick(now=2000)
    assert calls == [PUMP_ON, PUMP_OFF]


async def test_a_backup_restore_keeps_which_relays_openampere_switched_on(tmp_path, monkeypatch):
    calls = []
    runtime = runtime_with(tmp_path, monkeypatch, calls)
    backup = tmp_path / "backup.db"
    Storage(backup).close()  # an older backup without this device
    control = SurplusControl(runtime)
    control.save([PUMP_CONFIG])
    await pump_on(control, runtime, calls)
    runtime.storage.stage_restore(backup)
    runtime.storage.close()

    runtime = runtime_with(tmp_path, monkeypatch, calls)
    restarted = SurplusControl(runtime)
    assert restarted.consumers == []  # the device is not in the backup ...
    await restarted.tick(now=2000)
    assert calls == [PUMP_ON, PUMP_OFF]  # ... but its relay is switched off with the configuration it was switched on with


async def test_a_relay_remembered_by_an_older_version_is_still_switched_off(tmp_path, monkeypatch):
    calls = []
    runtime = runtime_with(tmp_path, monkeypatch, calls)
    control = SurplusControl(runtime)
    control.save([{**PUMP_CONFIG, "enabled": False}])
    runtime.storage.set_meta(consumers_module.SWITCHED_ON, {control.consumers[0].id: 1000.0})  # before #243: a number
    await SurplusControl(runtime).tick(now=2000)
    assert calls == [PUMP_OFF]


async def test_after_a_power_cut_a_relay_is_switched_on_again(tmp_path, monkeypatch):
    calls = []
    runtime = runtime_with(tmp_path, monkeypatch, calls)
    control = SurplusControl(runtime)
    control.save([PUMP_CONFIG])
    await pump_on(control, runtime, calls)
    restarted = SurplusControl(runtime)  # the relay may have come back off
    reading(runtime, grid=-3000)
    for now in range(5000, 5300, 30):
        await restarted.tick(now=now)
    assert calls == [PUMP_ON, PUMP_ON]  # sent once more, then not on every tick


async def test_an_unreachable_device_is_tried_less_often_logged_once_and_given_up_after_a_day(tmp_path, monkeypatch):
    calls = []
    runtime = runtime_with(tmp_path, monkeypatch, calls)
    control = SurplusControl(runtime)
    control.save([PUMP_CONFIG])
    await pump_on(control, runtime, calls)
    control.save([{**PUMP_CONFIG, "id": control.consumers[0].id, "enabled": False}])
    tries = []
    monkeypatch.setattr(consumers_module, "_call", tries.append)

    def failing(url):
        tries.append(url)
        raise OSError("timed out")
    monkeypatch.setattr(consumers_module, "_call", failing)
    for now in range(2000, 2000 + 3600, 10):  # an hour of fast ticks
        await control.tick(now=now)
    assert len(tries) < 20  # not every 10 seconds
    restarted = SurplusControl(runtime)
    for now in range(10_000, 10_000 + 26 * 3600, 600):  # still offline after a restart, for more than a day
        await restarted.tick(now=now)
    results = [e["result"] for e in runtime.storage.control_log(0)]
    assert sum(r.startswith("Fehler") for r in results) == 2  # once before and once after the restart
    assert sum("nicht erreichbar" in r for r in results) == 1
    assert runtime.storage.get_meta(consumers_module.SWITCHED_ON) == {}
    count = len(tries)
    await restarted.tick(now=200_000)
    assert len(tries) == count  # given up


async def test_enabling_a_device_again_while_it_is_switched_off_does_not_fail(tmp_path, monkeypatch):
    calls = []
    runtime = runtime_with(tmp_path, monkeypatch, calls)
    control = SurplusControl(runtime)
    control.save([PUMP_CONFIG])
    await pump_on(control, runtime, calls)
    c_id = control.consumers[0].id
    control.save([{**PUMP_CONFIG, "id": c_id, "enabled": False}])

    def enabled_meanwhile(url):  # the owner enables it again while the "off" is on its way
        calls.append(url)
        control.save([{**PUMP_CONFIG, "id": c_id}])
    monkeypatch.setattr(consumers_module, "_call", enabled_meanwhile)
    await control.tick(now=2000)
    assert calls == [PUMP_ON, PUMP_OFF]


async def test_two_ticks_at_once_switch_once(tmp_path, monkeypatch):
    calls = []
    runtime = runtime_with(tmp_path, monkeypatch, calls)
    control = SurplusControl(runtime)
    control.save([PUMP_CONFIG])
    reading(runtime, grid=-2000)
    await control.tick(now=1000)
    await asyncio.gather(control.tick(now=1030), control.tick(now=1031))
    assert calls == [PUMP_ON]


async def test_a_heating_rod_gets_0_w_when_test_mode_is_switched_on(tmp_path, monkeypatch):
    calls = []
    runtime = runtime_with(tmp_path, monkeypatch, calls)
    monkeypatch.setattr(consumers_module, "make_heating_rod", lambda c: FakeRod(calls))
    control = SurplusControl(runtime)
    control.save([ROD_CONFIG])
    reading(runtime, grid=-3500)
    for now in (1000, 1030, 1060):
        await control.tick(now=now)
    assert calls[-1][1] > 0
    runtime.config.control.dry_run = True
    runtime.config.control.enabled = False
    await control.tick(now=1090)
    assert calls[-1] == ("rod", 0)
    assert "trotz Testmodus" in runtime.storage.control_log()[0]["result"]
    count = len(calls)
    await control.stop()
    assert len(calls) == count  # already at 0 W: nothing more to send in test mode


async def test_a_relay_is_switched_off_although_nothing_can_be_written_to_the_database(tmp_path, monkeypatch):
    calls = []
    runtime = runtime_with(tmp_path, monkeypatch, calls)
    control = SurplusControl(runtime)
    control.save([PUMP_CONFIG])

    def full(*args, **kwargs):
        raise sqlite3.OperationalError("database or disk is full")
    monkeypatch.setattr(runtime.storage, "log_control", full)
    monkeypatch.setattr(runtime.storage, "set_meta", full)
    await pump_on(control, runtime, calls)
    runtime.config.control.enabled = False
    await control.tick(now=1060)
    assert calls == [PUMP_ON, PUMP_OFF]


async def test_shutdown_stops_grid_charging_even_when_another_step_fails(tmp_path, monkeypatch):
    from openampere import charging as charging_module
    from openampere.api import create_app
    stopped = []

    async def failing(self):
        raise sqlite3.OperationalError("database or disk is full")

    async def record(self, reason, *args, **kwargs):
        stopped.append(reason)
    monkeypatch.setattr(consumers_module.SurplusControl, "stop", failing)
    monkeypatch.setattr(charging_module.GridCharging, "stop", record)
    app = create_app(Runtime({}, Storage(tmp_path / "t.db")))
    async with app.router.lifespan_context(app):
        pass
    assert stopped == ["OpenAmpere wird beendet"]


# GHSA-3jrg-5v7g-9f96: switch addresses often hold the device's password; they were stored unencrypted and were in
# every backup
HTTP_CONFIG = {"name": "Heizstab", "kind": "http", "power_w": 1000, "min_on_min": 0, "min_off_min": 0,
               "url_on": "http://heater.local/on?password=geheim-4711",
               "url_off": "http://heater.local/off?password=geheim-4711"}


def database_bytes(runtime) -> bytes:
    path = runtime.storage.path
    return b"".join(open(f"{path}{s}", "rb").read() for s in ("", "-wal") if os.path.exists(f"{path}{s}"))


async def test_switch_addresses_are_stored_encrypted_and_not_readable_in_a_backup(tmp_path, monkeypatch):
    calls = []
    runtime = runtime_with(tmp_path, monkeypatch, calls)
    control = SurplusControl(runtime)
    control.save([HTTP_CONFIG])
    reading(runtime, grid=-2000)
    await control.tick(now=1000)
    await control.tick(now=1030)
    assert calls == [HTTP_CONFIG["url_on"]]  # still switches with the real address
    assert runtime.storage.get_meta("consumer_switched_on")  # remembered with the address, encrypted as well
    assert b"geheim-4711" not in database_bytes(runtime)
    runtime.storage.backup(tmp_path / "backup.db")
    assert b"geheim-4711" not in (tmp_path / "backup.db").read_bytes()
    assert SurplusControl(runtime).consumers[0].url_on == HTTP_CONFIG["url_on"]


def test_switch_addresses_of_older_versions_are_encrypted_on_the_next_start(tmp_path, monkeypatch):
    runtime = runtime_with(tmp_path, monkeypatch, [])
    control = SurplusControl(runtime)
    runtime.storage.set_meta("consumers", [{**HTTP_CONFIG, "id": "old1"}])
    assert control.consumers[0].url_off == HTTP_CONFIG["url_off"]
    assert runtime.storage.get_meta("consumers")[0]["url_off"].startswith("enc:v1:")


# #392: a backup restored on another server holds the switch addresses encrypted with the old secret.key
def test_a_stored_device_may_stay_without_its_switch_addresses_a_new_one_may_not():
    lost = {**HTTP_CONFIG, "id": "ab12cd34", "url_on": "", "url_off": ""}
    assert validate([lost], unreadable=("ab12cd34",))[0].addresses_lost
    with pytest.raises(ValueError, match="vollständige Adresse"):
        validate([lost])
    with pytest.raises(ValueError, match="vollständige Adresse"):  # one of the two entered again is not enough
        validate([{**lost, "url_on": HTTP_CONFIG["url_on"]}], unreadable=("ab12cd34",))


@pytest.mark.parametrize("situation", ["surplus", "control off", "no readings", "switched by hand"])
async def test_a_device_whose_switch_addresses_cannot_be_read_stays_and_is_never_switched(
        tmp_path, monkeypatch, caplog, situation):
    calls = []
    runtime = runtime_with(tmp_path, monkeypatch, calls)
    control = SurplusControl(runtime)
    control.save([{**HTTP_CONFIG, "battery_min_soc": 20}])
    reading(runtime, grid=-2000)
    await control.tick(now=1000)
    await control.tick(now=1030)
    assert calls == [HTTP_CONFIG["url_on"]]  # switched on before the backup
    device_id = control.consumers[0].id

    runtime.secrets = SecretBox(tmp_path / "other" / "secret.key")  # the new server
    caplog.set_level(logging.WARNING)
    restored = SurplusControl(runtime)
    kept = restored.consumers[0]
    assert (kept.id, kept.name, kept.kind, kept.power_w, kept.battery_min_soc) == (device_id, "Heizstab", "http", 1000,
                                                                                   20)
    assert (kept.url_on, kept.url_off) == ("", "") and kept.addresses_lost
    assert restored.view()["consumers"][0]["addresses_lost"]
    assert restored.view(hide_urls=True)["consumers"][0]["addresses_lost"]
    if situation == "control off":
        runtime.config.control.enabled = False
    elif situation == "no readings":
        runtime.collector.latest = None
    elif situation == "switched by hand":
        await restored.switch(kept, False, "von Hand geschaltet", 2000)
        restored.set_override(device_id, "boost", now=2000)
    for now in (2000, 2030, 2060):
        await restored.tick(now=now)
    assert calls == [HTTP_CONFIG["url_on"]]  # nothing sent to an empty address, also not to switch it off
    assert restored.consumers[0].id == device_id  # still there
    assert sum("switch addresses cannot be read" in r.message for r in caplog.records) == 1  # once, not on every read


async def test_a_device_with_unreadable_switch_addresses_works_again_with_new_ones(tmp_path, monkeypatch):
    calls = []
    runtime = runtime_with(tmp_path, monkeypatch, calls)
    control = SurplusControl(runtime)
    control.save([HTTP_CONFIG])
    reading(runtime, grid=-2000)
    await control.tick(now=1000)
    await control.tick(now=1030)
    device_id = control.consumers[0].id

    runtime.secrets = SecretBox(tmp_path / "other" / "secret.key")
    restored = SurplusControl(runtime)
    reading(runtime, grid=2000)  # no surplus: it is meant to be off now
    await restored.tick(now=2000)
    # changed in the app without the addresses (logged in: empty, not logged in: null): it stays and keeps waiting
    restored.save([{**restored.view()["consumers"][0], "power_w": 1200}])
    restored.save([{**restored.view(hide_urls=True)["consumers"][0], "min_on_min": 5}])
    kept = restored.consumers[0]
    assert (kept.power_w, kept.min_on_min, kept.addresses_lost) == (1200, 5, True)
    await restored.tick(now=2030)
    assert calls == [HTTP_CONFIG["url_on"]]

    # entered again: switched under the same id, also off what the old server had switched on
    new_on, new_off = "http://heater.local/on?password=neu-0815", "http://heater.local/off?password=neu-0815"
    restored.save([{**restored.view()["consumers"][0], "url_on": new_on, "url_off": new_off}])
    assert restored.consumers[0].id == device_id and not restored.consumers[0].addresses_lost
    assert not restored.view()["consumers"][0]["addresses_lost"]
    await restored.tick(now=2060)
    assert calls == [HTTP_CONFIG["url_on"], new_off]
    assert SurplusControl(runtime).consumers[0].url_on == new_on  # stored with the new secret.key


async def test_a_device_removed_while_its_switch_addresses_cannot_be_read_is_forgotten(tmp_path, monkeypatch):
    calls = []
    runtime = runtime_with(tmp_path, monkeypatch, calls)
    control = SurplusControl(runtime)
    control.save([HTTP_CONFIG])
    reading(runtime, grid=-2000)
    await control.tick(now=1000)
    await control.tick(now=1030)

    runtime.secrets = SecretBox(tmp_path / "other" / "secret.key")
    restored = SurplusControl(runtime)
    await restored.tick(now=2000)
    restored.save([])
    await restored.tick(now=2030)
    await restored.tick(now=2060)
    assert calls == [HTTP_CONFIG["url_on"]]
    assert runtime.storage.get_meta(consumers_module.SWITCHED_ON) == {}
    assert "nicht lesbar" in runtime.storage.control_log()[0]["result"]


def test_the_api_shows_unreadable_switch_addresses_and_refuses_to_switch_by_hand(tmp_path, monkeypatch):
    calls = []
    runtime = runtime_with(tmp_path, monkeypatch, calls)
    old = SecretBox(tmp_path / "old" / "secret.key")  # the server the backup comes from
    runtime.storage.set_meta("consumers", [{**HTTP_CONFIG, "id": "web1", "url_on": old.encrypt("consumer.url_on", "x"),
                                            "url_off": old.encrypt("consumer.url_off", "y")}])
    web = login(TestClient(create_app(runtime), base_url="https://testserver"))
    assert [c["addresses_lost"] for c in web.get("/api/consumers").json()["consumers"]] == [True]
    devices = web.get("/api/devices").json()["devices"]
    assert [d["addresses_lost"] for d in devices if d["id"] == "web1"] == [True]
    answer = web.post("/api/consumers/web1/switch?on=false")
    assert answer.status_code == 409 and "neu ein" in answer.json()["detail"]
    assert calls == []
