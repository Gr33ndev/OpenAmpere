# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""More than one inverter in one installation (#619): settings, combined values, battery settings per inverter."""

import asyncio
import time

import httpx
import pytest

from openampere import privacy
from openampere.api import create_app
from openampere.config import ExtraInverter, build_config, validate
from openampere.control import BatteryControl
from openampere.drivers.base import EnergyCounters, PvInput, Snapshot
from openampere.inverters import ExtraCounters, combine, combined_soc
from openampere.runtime import Runtime
from openampere.simulator import SajSimulatedInverter
from openampere.storage import Storage

from test_detection import serve


class Extra:
    """What combine needs of an ExtraCollector."""

    def __init__(self, entry: ExtraInverter, latest: Snapshot | None, fresh: bool = True, gone: bool = False) -> None:
        self.entry, self.latest, self._fresh, self._gone = entry, latest, fresh, gone

    @property
    def name(self) -> str:
        return self.entry.name

    def fresh(self, now=None) -> bool:
        return self._fresh and self.latest is not None

    def gone(self, now=None) -> bool:
        return self._gone


def reading(pv, battery, soc, grid=0.0, *, totals=None, inputs=()) -> Snapshot:
    return Snapshot(timestamp=time.time(), pv_power=pv, battery_power=battery, battery_soc=soc, grid_power=grid,
                    house_power=999.0, pv_inputs=[PvInput(power=p) for p in inputs],
                    totals=totals or EnergyCounters())


def counters(pv, grid_import, grid_export, charge, discharge) -> EnergyCounters:
    return EnergyCounters(pv=pv, grid_import=grid_import, grid_export=grid_export, battery_charge=charge,
                          battery_discharge=discharge)


# ---- settings ---------------------------------------------------------------


def test_further_inverters_are_checked_and_get_an_id():
    clean = validate({"inverters": [{"name": " Garage ", "host": "192.168.1.31", "driver": "saj"},
                                    {"id": "keller", "host": "192.168.1.32", "unit": 2, "own_grid": True}]})
    first, second = clean["inverters"]
    assert first["id"] == "wr2" and first["name"] == "Garage" and first["driver"] == "saj"
    assert second["id"] == "keller" and second["unit"] == 2 and second["own_grid"] is True
    config, _ = build_config({}, clean)
    assert [e.id for e in config.inverters] == ["wr2", "keller"]
    assert isinstance(config.inverters[0], ExtraInverter)


@pytest.mark.parametrize("entry", [
    {"host": ""},  # no address
    {"host": "192.168.1.31", "port": 0},
    {"host": "192.168.1.31", "driver": "enphase"},  # needs an access token, only the first inverter keeps one
    {"host": "192.168.1.31", "id": "main"},  # the first inverter's id
    {"host": "192.168.1.31", "id": "Not ok!"},
    {"host": "192.168.1.31", "rated_power_w": 500},
    "192.168.1.31",
])
def test_an_unusable_further_inverter_is_refused(entry):
    with pytest.raises(ValueError, match="weiteren Wechselrichter"):
        validate({"inverters": [entry]})


def test_at_most_five_further_inverters_with_ids_of_their_own():
    with pytest.raises(ValueError, match="höchstens 5"):
        validate({"inverters": [{"host": f"192.168.1.{i}"} for i in range(6)]})
    with pytest.raises(ValueError, match="dieselbe Kennung"):
        validate({"inverters": [{"id": "a", "host": "192.168.1.1"}, {"id": "a", "host": "192.168.1.2"}]})


def test_a_new_inverter_gets_an_id_no_other_has():
    clean = validate({"inverters": [{"id": "wr3", "host": "192.168.1.1"}, {"host": "192.168.1.2"},
                                    {"host": "192.168.1.3"}]})
    assert [e["id"] for e in clean["inverters"]] == ["wr3", "wr2", "wr4"]


async def test_the_same_inverter_cannot_be_added_twice(tmp_path):
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    await runtime.update_settings({"inverter.host": "192.168.1.30"})
    try:
        with pytest.raises(ValueError, match="schon ein Wechselrichter"):
            await runtime.update_settings({"inverters": [{"host": "192.168.1.30"}]})
        assert runtime.config.inverters == [] and runtime.collector.extras == []
    finally:
        await runtime.collector.stop()


async def test_further_inverters_need_the_first_one(tmp_path):
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    with pytest.raises(ValueError, match="zuerst den ersten"):
        await runtime.update_settings({"inverters": [{"host": "192.168.1.31"}]})
    await runtime.update_settings({"inverter.host": "192.168.1.30", "inverters": [{"host": "192.168.1.31"}]})
    try:
        with pytest.raises(ValueError, match="zuerst den ersten"):
            await runtime.update_settings({"inverter.host": ""})
    finally:
        await runtime.collector.stop()


async def test_name_and_capacity_do_not_reconnect_the_first_inverter(tmp_path):
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    await runtime.update_settings({"inverter.host": "192.168.1.30"})
    try:
        driver = runtime.collector.driver
        await runtime.update_settings({"inverter.name": "Haus", "inverter.capacity_kwh": 10})
        assert runtime.collector.driver is driver
        assert (runtime.collector.main_name, runtime.collector.main_capacity) == ("Haus", 10)
    finally:
        await runtime.collector.stop()


# ---- combined values -------------------------------------------------------


def test_pv_and_battery_add_up_the_grid_is_the_first_inverters():
    own = reading(3000, -1000, 40, grid=-500, inputs=(1500, 1500))
    extra = Extra(ExtraInverter(id="wr2", name="Garage", host="x"), reading(2000, 500, 80, grid=-480, inputs=(2000,)))
    snap = combine(own, [extra], "Haus")
    assert snap.pv_power == 5000 and snap.battery_power == -500 and snap.grid_power == -500
    assert snap.house_power == 4000  # grid + PV + battery, not what the inverters report (999)
    assert snap.battery_soc == 60  # capacities unknown: the average
    assert [i.power for i in snap.pv_inputs] == [1500, 1500, 2000]
    assert [p["id"] for p in snap.inverters] == ["main", "wr2"]
    assert snap.inverters[1] | {"alarms": []} == {"id": "wr2", "name": "Garage", "connected": True, "pv_power": 2000,
                                                  "pv_external_power": None, "battery_power": 500,
                                                  "battery_soc": 80, "grid_power": -480, "alarms": [],
                                                  "has_pv": None, "has_battery": None}
    assert own.pv_power == 3000 and own.inverters == []  # the first inverter's own reading is left as it was


def test_an_inverter_with_its_own_grid_connection_adds_its_grid_power():
    own = reading(1000, 0, 50, grid=200)
    extra = Extra(ExtraInverter(id="wr2", host="x", own_grid=True), reading(500, 0, 50, grid=300))
    snap = combine(own, [extra])
    assert snap.grid_power == 500 and snap.house_power == 2000


def test_the_state_of_charge_is_weighted_by_capacity_when_all_are_known():
    assert combined_soc([(reading(0, 0, 20), 5), (reading(0, 0, 80), 15)]) == 65
    assert combined_soc([(reading(0, 0, 20), 5), (reading(0, 0, 80), 0)]) == 50
    assert combined_soc([(reading(0, 0, None), 5), (reading(0, 0, 80), 15)]) == 80  # a battery without a reading
    assert combined_soc([(reading(0, 0, None), 5)]) is None
    own = reading(0, 0, 20)
    snap = combine(own, [Extra(ExtraInverter(id="wr2", host="x", capacity_kwh=15), reading(0, 0, 80))], "", 5)
    assert snap.battery_soc == 65


def test_further_counters_count_from_when_the_inverter_was_set_up():
    """A further inverter's lifetime is not counted, only what it counts from then on (else one quarter hour would
    get all of it)."""
    sums = ExtraCounters()
    entry = ExtraInverter(id="wr2", host="x")
    own = reading(0, 0, 50, totals=counters(1000, 500, 300, 200, 100))
    first = combine(own, [Extra(entry, reading(0, 0, 50, totals=counters(400_000, 9999, 9999, 50_000, 30_000)))],
                    counters=sums).totals
    assert (first.pv, first.battery_charge, first.battery_discharge) == (1000, 200, 100)
    own = reading(0, 0, 50, totals=counters(1100, 520, 310, 210, 105))
    later = Extra(entry, reading(0, 0, 50, totals=counters(400_050, 99_999, 99_999, 50_020, 30_010)))
    later.latest.timestamp += 60
    totals = combine(own, [later], counters=sums).totals
    assert (totals.pv, totals.grid_import, totals.grid_export) == (1150, 520, 310)  # the grid: the first one's
    assert (totals.battery_charge, totals.battery_discharge) == (230, 115)
    assert totals.consumption == 1150 + 520 + 115 - 310 - 230


def test_a_further_counter_that_drops_to_zero_or_back_counts_no_energy(tmp_path):
    storage = Storage(tmp_path / "t.db")
    sums = ExtraCounters(storage)
    ts = time.time()
    for value in (5000, 5010, 0, 5020, 100, 130):  # a restart (0), then a reset to 100
        sums.add("wr2", EnergyCounters(pv=value), ts, own_grid=False)
        ts += 30
    assert sums.total("pv") == 10 + 10 + 30
    sums.add("wr2", EnergyCounters(pv=10_000_000), ts, own_grid=False)  # garbage: counted from there on
    assert sums.total("pv") == 50
    # kept in the database, also for an inverter removed since: the combined counter never goes back
    assert ExtraCounters(storage).total("pv") == 50
    assert ExtraCounters(storage).total("grid_import") == 0  # not its own grid connection: never counted


def test_a_further_inverter_without_a_recent_reading_leaves_the_sums_missing():
    own = reading(3000, -1000, 40, grid=-500, totals=counters(1000, 500, 300, 200, 100))
    extra = Extra(ExtraInverter(id="wr2", name="Garage", host="x"), reading(2000, 500, 80), fresh=False)
    snap = combine(own, [extra])
    assert snap.pv_power is None and snap.battery_power is None and snap.battery_soc is None
    assert snap.house_power is None
    assert snap.grid_power == -500  # still the first one's: the further one does not measure the grid
    assert snap.totals.pv is None and snap.totals.consumption is None and snap.totals.grid_import == 500
    assert snap.inverters[1] == {"id": "wr2", "name": "Garage", "connected": False, "has_pv": None, "has_battery": None}


def test_a_pv_inverter_without_battery_adds_only_pv():
    own = reading(1000, -200, 50, grid=0, totals=counters(1000, 500, 300, 200, 100))
    pv_only = reading(800, None, None, totals=EnergyCounters(pv=600))
    snap = combine(own, [Extra(ExtraInverter(id="wr2", host="x"), pv_only)])
    assert snap.pv_power == 1800 and snap.battery_power == -200 and snap.battery_soc == 50
    assert snap.totals.pv == 1000 and snap.totals.battery_charge == 200  # from now on


def test_a_battery_without_power_in_one_reading_leaves_the_sum_missing():
    own = reading(1000, -200, 50, grid=0)
    no_power = reading(500, None, 60)  # a sentinel dropped by sanitize, the battery is still there
    snap = combine(own, [Extra(ExtraInverter(id="wr2", host="x"), no_power)])
    assert snap.battery_power is None and snap.house_power is None and snap.pv_power == 1500


# ---- with two simulated inverters ---------------------------------------------


async def two_inverters(tmp_path, *, control: bool = True):
    first, second = SajSimulatedInverter(max_connections=3), SajSimulatedInverter(max_connections=3,
                                                                                 serial="HST2103SIM00002")
    (server1, port1), (server2, port2) = await serve(first), await serve(second)
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    await runtime.update_settings({"inverter.host": "127.0.0.1", "inverter.port": port1, "inverter.poll_interval": 2,
                                   "inverter.name": "Haus", "control.enabled": control, "control.dry_run": False,
                                   "inverters": [{"id": "wr2", "name": "Garage", "host": "127.0.0.1", "port": port2,
                                                  "driver": "saj"}]})
    for _ in range(150):
        latest = runtime.collector.latest
        if latest and len(latest.inverters) == 2 and latest.inverters[1]["connected"] and latest.pv_power is not None:
            break
        await asyncio.sleep(0.1)
    return (first, second), (server1, server2), runtime


async def test_two_inverters_are_read_and_combined(tmp_path):
    sims, servers, runtime = await two_inverters(tmp_path)
    async with servers[0], servers[1]:
        try:
            collector = runtime.collector
            latest, own = collector.latest, collector.own
            assert [p["name"] for p in latest.inverters] == ["Haus", "Garage"]
            assert own is not latest and own.inverters == []
            extra = runtime.extra("wr2")
            assert extra.connected and extra.device.serial == "HST2103SIM00002"
            parts = latest.inverters
            assert latest.pv_power == pytest.approx(parts[0]["pv_power"] + parts[1]["pv_power"])
            assert latest.grid_power == parts[0]["grid_power"]
            # the serial number of the further inverter is stored with its address, not as the first one's
            assert runtime.storage.get_meta("known_device:wr2")["serial"] == "HST2103SIM00002"
            assert collector.known_serial == "HST2103SIM00001"
        finally:
            await runtime.collector.stop()
    assert all(e._task is None for e in runtime.collector.extras)


async def test_battery_settings_go_to_the_inverter_they_are_meant_for(tmp_path):
    (first, second), servers, runtime = await two_inverters(tmp_path)
    async with servers[0], servers[1]:
        try:
            garage = BatteryControl(runtime, runtime.extra("wr2"))
            result = await garage.write({"min_soc_on_grid": 35})
            assert result["result"] == "ok"
            assert second.regs[0x3644] == 35 and first.regs[0x3644] == 20
            entry = runtime.storage.control_log(1)[0]
            assert entry["details"]["inverter"] == "Garage" and entry["details"]["to"] == {"min_soc_on_grid": 35}
            await BatteryControl(runtime).write({"min_soc_on_grid": 25})
            assert first.regs[0x3644] == 25 and second.regs[0x3644] == 35
            assert runtime.storage.control_log(1)[0]["details"]["inverter"] == "Haus"
        finally:
            await runtime.collector.stop()


async def test_removing_a_further_inverter_stops_it(tmp_path):
    sims, servers, runtime = await two_inverters(tmp_path)
    async with servers[0], servers[1]:
        try:
            extra = runtime.extra("wr2")
            await runtime.update_settings({"inverters": []})
            assert runtime.collector.extras == [] and extra._task is None
            for _ in range(50):
                if runtime.collector.latest.inverters == []:
                    break
                await asyncio.sleep(0.1)
            assert runtime.collector.latest.inverters == []
        finally:
            await runtime.collector.stop()


async def test_the_app_lists_the_inverters_and_controls_each(tmp_path):
    (first, second), servers, runtime = await two_inverters(tmp_path)
    async with servers[0], servers[1]:
        try:
            # in this event loop, which the collectors run in (no lifespan: they already run)
            transport = httpx.ASGITransport(app=create_app(runtime))
            async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
                status = (await client.get("/api/status")).json()
                items = {i["id"]: i for i in status["inverters"]}
                assert items["main"]["name"] == "Haus" and items["wr2"]["name"] == "Garage"
                assert items["wr2"]["connected"]
                assert items["wr2"]["device"]["serial"] != "HST2103SIM00002"  # shortened without login
                settings = await client.get("/api/battery/settings", params={"inverter": "wr2"})
                assert settings.json()["min_soc_on_grid"] == 20
                assert (await client.get("/api/battery/settings", params={"inverter": "nope"})).status_code == 404
                live = (await client.get("/api/live")).json()
                assert [p["id"] for p in live["inverters"]] == ["main", "wr2"]
        finally:
            await runtime.collector.stop()


async def test_reports_mask_the_further_inverters_address_and_serial(tmp_path):
    sims, servers, runtime = await two_inverters(tmp_path)
    async with servers[0], servers[1]:
        try:
            text = privacy.for_report(runtime).apply("Seriennummer HST2103SIM00002")
            assert "HST2103SIM00002" not in text
        finally:
            await runtime.collector.stop()


def test_the_readings_keep_up_to_twelve_pv_inputs(tmp_path):
    storage = Storage(tmp_path / "t.db")
    snap = reading(6000, 0, 50, inputs=[1000, 900, 800, 700, 600, 500])
    storage.add_snapshot(snap)
    row = storage.samples(snap.timestamp - 1, snap.timestamp + 1)[0]
    assert [row[f"pv{i}"] for i in range(1, 8)] == [1000, 900, 800, 700, 600, 500, None]


def test_an_existing_database_gets_the_columns_of_more_pv_inputs(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    Storage(path).close()
    db = sqlite3.connect(path)
    for i in range(5, 13):  # as version 5 had it
        db.execute(f"ALTER TABLE samples DROP COLUMN pv{i}")
    db.execute("PRAGMA user_version=5")
    db.commit()
    db.close()
    storage = Storage(path)
    snap = reading(1500, 0, 50, inputs=[100, 200, 300, 400, 500])
    storage.add_snapshot(snap)
    assert storage.samples(snap.timestamp - 1, snap.timestamp + 1)[0]["pv5"] == 500
    assert (tmp_path / "old.db.schema-5").exists()  # the copy for a rollback


def test_an_id_used_again_counts_from_the_new_inverters_first_reading():
    """Removing wr2 and adding another device (it gets wr2 again) must not count the difference of their counters."""
    sums = ExtraCounters()
    ts = time.time()
    sums.add("wr2", EnergyCounters(pv=5000), ts, own_grid=False, host="192.168.1.31")
    sums.add("wr2", EnergyCounters(pv=5100), ts + 60, own_grid=False, host="192.168.1.31")
    sums.forget(set())  # removed
    sums.add("wr2", EnergyCounters(pv=9_000_000), ts + 86400 * 20, own_grid=False, host="192.168.1.31")
    sums.add("wr2", EnergyCounters(pv=9_000_040), ts + 86400 * 20 + 60, own_grid=False, host="192.168.1.31")
    assert sums.total("pv") == 140
    # another device at the same id without a removal in between (address changed in the settings)
    sums.add("wr2", EnergyCounters(pv=300), ts + 86400 * 21, own_grid=False, host="192.168.1.40")
    sums.add("wr2", EnergyCounters(pv=310), ts + 86400 * 21 + 60, own_grid=False, host="192.168.1.40")
    assert sums.total("pv") == 150


def test_combine_forgets_the_counters_of_inverters_no_longer_set_up():
    sums = ExtraCounters()
    entry = ExtraInverter(id="wr2", host="x")
    own = reading(0, 0, 50, totals=counters(1000, 500, 300, 200, 100))
    combine(own, [Extra(entry, reading(0, 0, 50, totals=counters(5000, 0, 0, 0, 0)))], counters=sums)
    combine(own, [], counters=sums)  # removed in the settings
    assert sums.state["wr2"]["last"] == {}


def test_an_inverter_away_for_long_is_left_out_and_the_others_are_recorded():
    own = reading(3000, -1000, 40, grid=-500, totals=counters(1000, 500, 300, 200, 100))
    away = Extra(ExtraInverter(id="wr2", name="Garage", host="x"), reading(2000, 500, 80), fresh=False, gone=True)
    sums = ExtraCounters()
    sums.add("wr2", EnergyCounters(pv=700), time.time() - 3600, own_grid=False, host="x")
    sums.add("wr2", EnergyCounters(pv=750), time.time() - 3500, own_grid=False, host="x")
    snap = combine(own, [away], counters=sums)
    assert snap.pv_power == 3000 and snap.battery_power == -1000 and snap.battery_soc == 40
    assert snap.house_power == 1500
    assert snap.totals.pv == 1050  # its part counted so far stays
    assert snap.inverters[1] == {"id": "wr2", "name": "Garage", "connected": False, "left_out": True,
                                 "has_pv": None, "has_battery": None}
    assert sums.state["wr2"]["last"] == {}  # counts again from its next reading


def test_battery_health_counts_the_size_of_all_batteries(tmp_path):
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    runtime.config.battery.capacity_kwh = 10
    app = create_app(runtime)
    battery_health = next(r.endpoint for r in app.routes if getattr(r, "path", "") == "/api/battery/health")
    seen = []
    import openampere.health as health_module
    original = health_module.battery
    health_module.battery = lambda storage, snap, capacity: seen.append(capacity) or {}
    try:
        battery_health()
        runtime.collector.extras = [Extra(ExtraInverter(id="wr2", host="x", capacity_kwh=10), None)]
        battery_health()  # the first one's size unknown: the size entered for the battery
        runtime.config.inverter.capacity_kwh = 10
        battery_health()
    finally:
        health_module.battery = original
        runtime.collector.extras = []
    assert seen == [10, 10, 20]


def test_the_pv_inputs_of_each_inverter_keep_their_places():
    """A further inverter left out or without a reading keeps its places: the next one's inputs do not move into them
    (the owner names inputs by place, the history keeps them so)."""
    own = reading(1000, 0, 50, inputs=(500, 500))
    middle = Extra(ExtraInverter(id="wr2", host="a"), reading(800, 0, 50, inputs=(400, 400)), fresh=False, gone=True)
    middle.input_count = 2
    last = Extra(ExtraInverter(id="wr3", host="b"), reading(300, 0, 50, inputs=(300,)))
    snap = combine(own, [middle, last])
    assert [i.power for i in snap.pv_inputs] == [500, 500, None, None, 300]
    middle._gone = False  # a short gap: missing sums, but the same places
    assert [i.power for i in combine(own, [middle, last]).pv_inputs] == [500, 500, None, None, 300]


async def test_the_number_of_inputs_is_kept_over_a_restart(tmp_path):
    """The places of a further inverter that does not answer after a restart stay its own (#619)."""
    from openampere.inverters import ExtraCollector

    storage = Storage(tmp_path / "t.db")
    entry = ExtraInverter(id="wr2", host="192.168.1.31")
    first = ExtraCollector(entry, None, storage, 10)
    await first._reading(reading(800, 0, 50, inputs=(400, 400)))
    assert ExtraCollector(entry, None, storage, 10).input_count == 2  # after a restart, before it answers
    moved = ExtraInverter(id="wr2", host="192.168.1.40")  # another device at the id
    assert ExtraCollector(moved, None, storage, 10).input_count == 0


def test_an_inverter_whose_values_the_first_already_has_is_only_shown():
    """A parallel system: the first inverter reports the values of all, the other is not added again."""
    own = reading(5000, -1000, 50, grid=-300, totals=counters(1000, 500, 300, 200, 100), inputs=(2500, 2500))
    follower = ExtraInverter(id="wr2", name="Garage", host="x", in_first=True)
    part = Extra(follower, reading(2000, -400, 60, totals=counters(400, 0, 0, 50, 30), inputs=(2000,)))
    sums = ExtraCounters()
    snap = combine(own, [part], counters=sums)
    assert (snap.pv_power, snap.battery_power, snap.battery_soc, snap.house_power) == (5000, -1000, 50, 3700)
    assert snap.totals.pv == 1000 and "wr2" not in sums.state
    assert snap.inverters[1]["in_first"] is True and snap.inverters[1]["pv_power"] == 2000
    assert [i.power for i in snap.pv_inputs] == [2500, 2500, 2000]  # its inputs are shown, not added to PV
    # away: nothing goes missing, it is not part of the sums anyway
    away = Extra(follower, None, fresh=False)
    snap = combine(own, [away], counters=sums)
    assert snap.pv_power == 5000 and snap.inverters[1].get("left_out") is None


def test_the_parallel_setting_is_kept():
    clean = validate({"inverters": [{"host": "192.168.1.31", "in_first": True}]})
    assert clean["inverters"][0]["in_first"] is True
    assert build_config({}, clean)[0].inverters[0].in_first is True


async def test_what_an_inverter_has_is_kept_for_when_it_does_not_answer(tmp_path):
    """#619: a PV inverter that is off at night must not be drawn with a battery; kept over a restart."""
    from openampere.inverters import ExtraCollector

    storage = Storage(tmp_path / "t.db")
    entry = ExtraInverter(id="wr2", host="192.168.1.31")
    pv_only = ExtraCollector(entry, None, storage, 10)
    await pv_only._reading(reading(800, None, None))
    assert (pv_only.has_pv, pv_only.has_battery) == (True, False)
    again = ExtraCollector(entry, None, storage, 10)  # a restart, before it answers
    assert (again.has_pv, again.has_battery) == (True, False)
    part = combine(reading(1000, 0, 50), [Extra(entry, None, fresh=False)])
    assert part.inverters[0]["has_battery"] is True  # the first inverter's from its reading
    again.latest = None
    snap = combine(reading(1000, 0, 50), [again])
    assert snap.inverters[1]["has_battery"] is False and snap.inverters[1]["has_pv"] is True
