# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from openampere.api import create_app, ratios
from openampere.runtime import Runtime
from openampere.drivers.base import EnergyCounters, Snapshot
from openampere.periods import parse_anchor, period_bounds
from openampere.storage import Storage

TZ = ZoneInfo("Europe/Berlin")


def snap(ts: float, pv_total: float, load_total: float, **extra) -> Snapshot:
    return Snapshot(
        timestamp=ts, pv_power=1000, house_power=500, grid_power=-500, battery_power=0, battery_soc=50,
        totals=EnergyCounters(pv=pv_total, load=load_total, grid_import=0, grid_export=pv_total - load_total,
                              battery_charge=0, battery_discharge=0),
        **extra,
    )


def test_counter_deltas_become_quarter_rows(tmp_path):
    storage = Storage(tmp_path / "t.db")
    base = datetime(2026, 6, 1, 12, 0, tzinfo=TZ).timestamp()
    storage.add_snapshot(snap(base + 10, 1000, 400))
    storage.add_snapshot(snap(base + 600, 1200, 500))
    storage.add_snapshot(snap(base + 905, 1260, 530))  # next quarter starts -> row for 12:00
    rows = storage.energy(base, base + 3600)
    assert len(rows) == 1
    assert rows[0]["ts"] == base and rows[0]["pv"] == 260 and rows[0]["load"] == 130


def test_a_counted_second_inverter_is_part_of_the_solar_energy(tmp_path):
    """#419: it has no counter; its power is integrated, added to pv (and so to the consumption) and kept apart."""
    storage = Storage(tmp_path / "t.db")
    base = datetime(2026, 6, 1, 12, 0, tzinfo=TZ).timestamp()
    for i in range(10):  # 1200 W from the second inverter, readings a minute apart
        storage.add_snapshot(snap(base + 10 + 60 * i, 1000 + 20 * i, 400 + 10 * i, pv_external_power=1200))
    # switched off: no more integration after this reading; 1200 W from base + 10 to base + 600 = 590 s
    storage.add_snapshot(snap(base + 600, 1200, 500, pv_external_power=None))
    wh = 1200 * 590 / 3600
    running = storage.running_quarter(snap(base + 700, 1220, 510))
    assert running["pv_external"] == pytest.approx(wh) and running["pv"] == pytest.approx(220 + wh)
    storage.add_snapshot(snap(base + 905, 1260, 530))
    row = storage.energy(base, base + 900)[0]
    assert (row["pv"], row["pv_external"]) == (pytest.approx(260 + wh), pytest.approx(wh))
    assert row["load"] == pytest.approx(130 + wh)  # the house used what the second inverter produced
    assert storage.energy_sum(base, base + 900)["pv_external"] == pytest.approx(wh)
    storage.add_snapshot(snap(base + 1805, 1300, 550))
    assert storage.energy(base + 900, base + 1800)[0]["pv_external"] is None  # not counted in this quarter
    sample = storage.samples(base, base + 20)[0]
    assert (sample["pv"], sample["pv_external"]) == (1000, 1200)
    storage.close()
    reopened = Storage(tmp_path / "t.db")  # the integrated energy survives a restart
    assert reopened._external["total"] == pytest.approx(wh)
    reopened.close()


def counters(pv, load, grid_import, grid_export, charge, discharge) -> EnergyCounters:
    return EnergyCounters(pv=pv, load=load, grid_import=grid_import, grid_export=grid_export, battery_charge=charge,
                          battery_discharge=discharge)


def test_consumption_is_the_balance_not_the_load_counter(tmp_path):
    """The inverter's load counter can count much more than the house used (#346)."""
    storage = Storage(tmp_path / "t.db")
    base = datetime(2026, 6, 1, 12, 0, tzinfo=TZ).timestamp()
    storage.add_snapshot(Snapshot(timestamp=base, totals=counters(10_000, 50_000, 2_000, 3_000, 4_000, 5_000)))
    # +1000 PV, +100 grid, +500 from the battery, -200 to the grid, -600 into the battery: 800 Wh in the house,
    # while the load counter says 1500 Wh
    storage.add_snapshot(Snapshot(timestamp=base + 900, totals=counters(11_000, 51_500, 2_100, 3_200, 4_600, 5_500)))
    row = storage.energy(base, base + 900)[0]
    assert (row["pv"], row["load"]) == (1000, 800)
    running = Snapshot(timestamp=base + 1000, totals=counters(11_100, 52_000, 2_100, 3_200, 4_650, 5_500))
    assert storage.running_quarter(running)["load"] == 50


def test_negative_quarter_is_carried_into_the_next(tmp_path):
    """0.1 kWh counters tick at different moments: a quarter can come out negative, the next pays it back."""
    storage = Storage(tmp_path / "t.db")
    base = datetime(2026, 6, 1, 12, 0, tzinfo=TZ).timestamp()
    for i, (pv, charge) in enumerate([(10_000, 4_000), (10_000, 4_100), (10_200, 4_100), (10_300, 4_100)]):
        storage.add_snapshot(Snapshot(timestamp=base + i * 900, totals=counters(pv, 0, 0, 0, charge, 0)))
    loads = [r["load"] for r in storage.energy(base, base + 3600)]
    assert loads == [0, 100, 100]  # -100, then 200 - 100, then 100: sum 200 like the balance
    assert storage.running_quarter(Snapshot(timestamp=base + 3000, totals=counters(10_300, 0, 0, 0, 4_100, 0))) \
        is not None


def test_quarter_state_of_an_older_version_continues(tmp_path):
    """The saved quarter state of a version before #346 contains the load counter and no carry."""
    storage = Storage(tmp_path / "t.db")
    base = datetime(2026, 6, 1, 12, 0, tzinfo=TZ).timestamp()
    storage.set_meta("quarter_state", {"ts": int(base), "totals": {"pv": 1000, "load": 99_999, "grid_import": 0,
                                                               "grid_export": 600, "battery_charge": 0,
                                                               "battery_discharge": 0}})
    storage.close()
    storage = Storage(tmp_path / "t.db")
    storage.add_snapshot(snap(base + 900, 1100, 450))
    assert [(r["pv"], r["load"]) for r in storage.energy(base, base + 900)] == [(100, 50)]


def test_counter_reset_only_rebases(tmp_path):
    storage = Storage(tmp_path / "t.db")
    base = datetime(2026, 6, 1, 12, 0, tzinfo=TZ).timestamp()
    storage.add_snapshot(snap(base, 1000, 400))
    storage.add_snapshot(snap(base + 900, 10, 5))  # counters went backwards (inverter reset)
    storage.add_snapshot(snap(base + 1800, 60, 25))
    rows = storage.energy(base, base + 86400)
    assert [(r["ts"] - base, r["pv"], r["load"]) for r in rows] == [(900, 50, 20)]


def test_counter_glitch_to_zero_is_ignored(tmp_path):
    storage = Storage(tmp_path / "t.db")
    base = datetime(2026, 6, 1, 12, 0, tzinfo=TZ).timestamp()
    storage.add_snapshot(snap(base, 1000, 400))
    glitch = snap(base + 905, 1100, 450)
    glitch.totals.grid_import = 0  # was 0 already in snap(); make another counter drop to 0
    glitch.totals.pv = 0
    storage.add_snapshot(glitch)
    storage.add_snapshot(snap(base + 910, 1100, 450))
    rows = storage.energy(base, base + 3600)
    assert [(r["ts"] - base, r["pv"]) for r in rows] == [(0, 100)]  # no lifetime-sized spike


def test_implausible_jump_rebases(tmp_path):
    storage = Storage(tmp_path / "t.db")
    base = datetime(2026, 6, 1, 12, 0, tzinfo=TZ).timestamp()
    storage.add_snapshot(snap(base, 1000, 400))
    storage.add_snapshot(snap(base + 900, 12_000_000, 450))  # garbage: +12,000 kWh in 15 min
    storage.add_snapshot(snap(base + 1800, 12_000_200, 500))
    rows = storage.energy(base, base + 3600)
    assert [(r["ts"] - base, r["pv"]) for r in rows] == [(900, 200)]


def test_outage_is_spread_over_missing_quarters(tmp_path):
    storage = Storage(tmp_path / "t.db")
    base = datetime(2026, 6, 1, 12, 0, tzinfo=TZ).timestamp()
    storage.add_snapshot(snap(base, 1000, 400))
    storage.add_snapshot(snap(base + 4 * 900, 1400, 600))  # 1 h without readings
    rows = storage.energy(base, base + 3600)
    assert [(r["ts"] - base, r["pv"], r["load"]) for r in rows] == [(0, 100, 50), (900, 100, 50), (1800, 100, 50), (2700, 100, 50)]


def test_quarter_state_survives_restart(tmp_path):
    base = datetime(2026, 6, 1, 12, 0, tzinfo=TZ).timestamp()
    storage = Storage(tmp_path / "t.db")
    storage.add_snapshot(snap(base, 1000, 400))
    storage.close()
    storage = Storage(tmp_path / "t.db")
    storage.add_snapshot(snap(base + 900, 1100, 450))
    assert storage.energy(base, base + 900)[0]["pv"] == 100


def test_import_never_overwrites_local(tmp_path):
    storage = Storage(tmp_path / "t.db")
    base = datetime(2026, 6, 1, 12, 0, tzinfo=TZ).timestamp()
    storage.add_snapshot(snap(base, 1000, 400))
    storage.add_snapshot(snap(base + 900, 1100, 450))
    inserted = storage.import_energy([{"ts": base, "pv": 999}, {"ts": base + 900, "pv": 50}], "cloud")
    assert inserted == 1
    rows = storage.energy(base, base + 1800)
    assert [(r["pv"], r["source"]) for r in rows] == [(100, "local"), (50, "cloud")]


def test_ratios():
    r = ratios({"load": 1000, "grid_import": 250, "pv": 2000, "grid_export": 1500})
    assert r == {"autarky": 0.75, "self_consumption": 0.25}
    assert ratios({}) == {"autarky": None, "self_consumption": None}


def test_first_day_money_follows_the_daily_counters(tmp_path):
    """Recording starts in the afternoon: the day totals come from the inverter, the savings must too (#15)."""
    storage = Storage(tmp_path / "t.db")
    base = datetime(2026, 10, 3, 17, 55, tzinfo=TZ).timestamp()
    today = EnergyCounters(pv=24_200, load=11_300, grid_import=1_000, grid_export=5_300, battery_charge=11_600,
                           battery_discharge=4_800)
    storage.add_snapshot(snap(base, 1000, 400, today=today))
    storage.add_snapshot(snap(base + 600, 1100, 450, today=today))
    runtime = Runtime({}, storage)
    runtime.collector.latest = snap(base + 700, 1110, 455, today=today).sanitize()
    client = TestClient(create_app(runtime))
    summary = client.get("/api/energy/summary", params={"period": "day", "date": "2026-10-03"}).json()
    # consumption is the balance of the daily counters (24.2 + 1.0 + 4.8 - 5.3 - 11.6), not their load counter (#346)
    assert summary["energy_wh"]["load"] == 13_100 and summary["recorded_since"] is not None
    # default tariff: 35 ct for self-used power (13.1 - 1.0 kWh), 8 ct for 5.3 kWh feed-in
    assert summary["money"]["savings_eur"] == round(12.1 * 0.35 + 5.3 * 0.08, 2)


def test_periods():
    assert parse_anchor("week", "2026-W40", TZ).isoformat() == "2026-09-28"
    assert parse_anchor("month", "2026-02", TZ).isoformat() == "2026-02-01"
    start, end = period_bounds("month", parse_anchor("month", "2026-02", TZ))
    assert (start.isoformat(), end.isoformat()) == ("2026-02-01", "2026-03-01")
    start, end = period_bounds("week", parse_anchor("day", "2026-10-02", TZ))
    assert start.isoformat() == "2026-09-28" and end.isoformat() == "2026-10-05"


def test_api_endpoints_carry_the_second_inverter(tmp_path):
    """#419: summary, timelines and the CSV export show the second PV inverter as part of the solar energy."""
    storage = Storage(tmp_path / "t.db")
    base = datetime(2026, 6, 1, 12, 0, tzinfo=TZ).timestamp()
    for i in range(16):  # 600 W for 15 minutes = 150 Wh
        storage.add_snapshot(snap(base + 60 * i, 1000 + 10 * i, 400 + 5 * i, pv_external_power=600))
    storage.add_snapshot(snap(base + 960, 1160, 480, pv_external_power=600))
    runtime = Runtime({}, storage)
    runtime.collector.latest = snap(base + 1000, 1170, 485, pv_external_power=600)
    client = TestClient(create_app(runtime))
    params = {"period": "day", "date": "2026-06-01"}
    energy = client.get("/api/energy/summary", params=params).json()["energy_wh"]
    assert energy["pv_external"] == pytest.approx(160)  # 150 Wh stored quarter + 10 Wh of the running one
    assert energy["pv"] == pytest.approx(170 + energy["pv_external"])
    entry = client.get("/api/energy/timeline", params={**params, "resolution": "60m"}).json()["entries"][0]
    assert entry["pv_external"] == pytest.approx(energy["pv_external"])
    power = client.get("/api/power/timeline", params={"date": "2026-06-01", "step": 300}).json()["entries"]
    assert power[0]["pv_external"] == 600
    csv = client.get("/api/export/csv", params={"from": "2026-06-01", "to": "2026-06-01", "resolution": "15m"})
    lines = csv.text.lstrip("\ufeff").splitlines()
    assert lines[0].endswith(";Quelle;davon zweiter Wechselrichter (kWh)")
    assert lines[1].split(";")[1:2] == ["0,300"] and lines[1].endswith(";OpenAmpere;0,150")


def test_api_endpoints(tmp_path):
    storage = Storage(tmp_path / "t.db")
    base = datetime(2026, 6, 1, 12, 0, tzinfo=TZ).timestamp()
    for i, (pv, load) in enumerate([(1000, 400), (1100, 450), (1300, 500)]):
        storage.add_snapshot(snap(base + i * 900, pv, load))
    runtime = Runtime({}, storage)
    # the quarter hour from 12:30 is still running: 50 Wh so far, not stored yet
    runtime.collector.latest = snap(base + 1900, 1350, 520)
    app = create_app(runtime)
    client = TestClient(app)

    summary = client.get("/api/energy/summary", params={"period": "day", "date": "2026-06-01"}).json()
    assert summary["energy_wh"]["pv"] == 350 and summary["quarters"] == 3  # stored + running quarter
    timeline = client.get("/api/energy/timeline",
                          params={"period": "day", "date": "2026-06-01", "resolution": "60m"}).json()
    assert len(timeline["entries"]) == 1 and timeline["entries"][0]["pv"] == 350
    assert client.get("/api/energy/summary", params={"period": "decade"}).status_code == 400
    # savings with the default tariff
    assert summary["money"]["savings_eur"] >= 0

    csv = client.get("/api/export/csv", params={"from": "2026-06-01", "to": "2026-06-01", "resolution": "15m"})
    assert csv.status_code == 200 and "attachment" in csv.headers["content-disposition"]
    lines = csv.text.lstrip("\ufeff").splitlines()
    assert lines[0].startswith("Zeit;Erzeugung (kWh)")
    assert lines[1].split(";")[:2] == ["01.06.2026 12:00", "0,100"] and lines[1].endswith("OpenAmpere")
    assert client.get("/api/export/csv", params={"from": "2026-06-02", "to": "2026-06-01"}).status_code == 400

    installation = client.get("/api/v1/customer/installation").json()[0]["uuid"]
    now = client.get(f"/api/v1/installation/{installation}/now/all/power").json()
    assert now == {"pvPower": 1000, "housePower": -500, "gridPower": -500, "batteryPower": 0, "batterySoc": 50}
    assert client.get("/api/v1/installation/other/now/all/power").status_code == 404


def test_second_instance_is_refused(tmp_path):
    import pytest
    from openampere.storage import DatabaseInUse

    first = Storage(tmp_path / "t.db")
    with pytest.raises(DatabaseInUse):
        Storage(tmp_path / "t.db")
    first.close()
    Storage(tmp_path / "t.db").close()  # free again after closing


def test_pv_inputs_are_integrated_per_quarter(tmp_path):
    from openampere.drivers.base import PvInput

    storage = Storage(tmp_path / "t.db")
    base = datetime(2026, 6, 1, 12, 0, tzinfo=TZ).timestamp()
    for i in range(91):  # 15 min + one sample of the next quarter, every 10 s
        s = snap(base + i * 10, 1000 + i, 400 + i)
        s.pv_inputs = [PvInput(power=2000), PvInput(power=1000)]
        s.temperatures = {"inverter": 40.0, "battery": 25.0}
        storage.add_snapshot(s)
    rows = storage.pv_input_energy(base, base + 900)
    assert [(r["input"], round(r["wh"])) for r in rows] == [(1, 500), (2, 250)]  # 2 kW and 1 kW for 15 min
    sample = storage.samples(base, base + 1)[0]
    assert sample["pv1"] == 2000 and sample["t_inverter"] == 40.0


async def test_hidden_pv_inputs_are_left_out(tmp_path):
    """An unused MPPT can be hidden; its readings are kept (#38)."""
    from openampere.drivers.base import PvInput

    storage = Storage(tmp_path / "t.db")
    base = datetime(2026, 6, 1, 12, 0, tzinfo=TZ).timestamp()
    for i in range(91):
        s = snap(base + i * 10, 1000 + i, 400 + i)
        s.pv_inputs = [PvInput(power=2000), PvInput(power=0), PvInput(power=1000)]
        storage.add_snapshot(s)
    runtime = Runtime({}, storage)
    client = TestClient(create_app(runtime))
    params = {"period": "day", "date": "2026-06-01", "mode": "energy", "resolution": "60m"}
    # input 2 never produced anything: it is not connected and left out by itself (#58)
    assert client.get("/api/pv/inputs", params=params).json()["labels"] == ["Modulfeld 1", "Modulfeld 3"]
    await runtime.update_settings({"pv.input_names": ["Süd", "", "West"]})
    data = client.get("/api/pv/inputs", params=params).json()
    assert data["inputs"] == [1, 3] and data["labels"] == ["Süd", "West"]
    assert [round(v) for v in data["totals_wh"]] == [500, 250]
    assert all(len(e["values"]) == 2 for e in data["entries"])
    await runtime.update_settings({"pv.hidden_inputs": ["3"]})  # hidden by hand
    assert client.get("/api/pv/inputs", params=params).json()["labels"] == ["Süd"]
    assert len(storage.pv_input_energy(base, base + 900)) == 3  # still recorded


def test_old_database_is_migrated(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE samples (ts REAL PRIMARY KEY, pv REAL, house REAL, grid REAL, battery REAL, soc REAL)")
    db.execute("INSERT INTO samples VALUES (1, 2, 3, 4, 5, 6)")
    db.commit()
    db.close()
    storage = Storage(path)
    assert storage.samples(0, 10)[0]["pv"] == 2 and storage.samples(0, 10)[0]["pv1"] is None


def test_concurrent_reads_and_writes_from_threads(tmp_path):
    """Web requests (thread pool) read while the collector writes. Unsynchronised access to one SQLite
    connection crashed the process with a segmentation fault."""
    import threading

    storage = Storage(tmp_path / "t.db")
    base = datetime(2026, 6, 1, 12, 0, tzinfo=TZ).timestamp()
    errors = []

    def writer():
        try:
            for i in range(1500):
                storage.add_snapshot(snap(base + i * 10, 1000 + i, 400 + i))
        except Exception as err:  # pragma: no cover - reported below
            errors.append(err)

    def reader():
        try:
            for _ in range(300):
                storage.samples(base, base + 86400)
                storage.energy_sum(base, base + 86400)
                storage.pv_input_energy(base, base + 86400)
                storage.get_meta("installation_id")
        except Exception as err:  # pragma: no cover
            errors.append(err)

    threads = [threading.Thread(target=writer)] + [threading.Thread(target=reader) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert len(storage.samples(base, base + 86400)) == 1500


def test_cache_headers_make_updates_visible(tmp_path):
    from openampere import api as api_module

    if not (api_module.WEB_DIST / "index.html").is_file():
        import pytest
        pytest.skip("web app not built")
    client = TestClient(create_app(Runtime({}, Storage(tmp_path / "t.db"))))
    index = client.get("/")
    assert index.headers["cache-control"] == "no-cache"
    import re
    asset = re.search(r'/assets/[^"]+\.js', index.text).group(0)
    assert "immutable" in client.get(asset).headers["cache-control"]
    assert client.get("/manifest.webmanifest").headers["cache-control"] == "no-cache"
    assert "cache-control" not in client.get("/api/status").headers


def test_wrong_clock_is_not_stored(tmp_path):
    storage = Storage(tmp_path / "t.db")
    base = datetime(2026, 6, 1, 12, 0, tzinfo=TZ).timestamp()
    storage.add_snapshot(snap(base, 1000, 400))
    storage.add_snapshot(snap(86400 * 3, 1000, 400))  # Raspberry Pi booted without time: 1970
    storage.add_snapshot(snap(base - 86400, 1000, 400))  # clock jumped back by a day
    assert [s["ts"] for s in storage.samples(0, base + 1)] == [base]
    assert storage._clock_warned
    storage.add_snapshot(snap(base + 10, 1000, 400))
    assert not storage._clock_warned


def test_sanitize_derives_consumption_from_the_balance():
    snap_ = Snapshot(timestamp=0, totals=counters(10_000, 50_000, 2_000, 3_000, 4_000, 5_000),
                     today=EnergyCounters(pv=1_000, load=900)).sanitize()
    assert snap_.totals.consumption == 10_000 and snap_.totals.load == 50_000  # the load counter stays as read
    assert snap_.today.consumption is None  # a counter is missing


def test_sanitize_drops_sentinels_and_impossible_values():
    s = snap(0, 0, 0)
    s.battery_soc, s.battery_temperature, s.temperatures = 32767, -3276.8, {"inverter": 45.0, "ambient": 6553.5}
    s.grid_power = 65535
    s.sanitize()
    assert s.battery_soc is None and s.battery_temperature is None and s.grid_power is None
    assert s.temperatures == {"inverter": 45.0}


def test_retention_forever_and_storage_usage(tmp_path):
    """Detail readings can be kept forever; the app shows what that costs (#22)."""
    storage = Storage(tmp_path / "t.db")
    old = datetime(2025, 1, 10, 12, 0, tzinfo=TZ).timestamp()
    storage.add_snapshot(snap(old, 1000, 400))
    storage.prune(0)  # 0 = keep forever
    assert len(storage.samples(old - 1, old + 1)) == 1
    storage.prune(30)
    assert storage.samples(old - 1, old + 1) == []
    runtime = Runtime({}, storage)
    usage = TestClient(create_app(runtime)).get("/api/storage").json()
    assert usage["db_bytes"] > 0 and usage["free_bytes"] > 0
    assert usage["bytes_per_year"] == round(86400 / 10 * 130 * 365)  # one reading every 10 s


def test_power_timeline_shows_the_imported_state_of_charge(tmp_path):
    """#468: a day imported from the cloud has no local readings; its state of charge per quarter hour is drawn in the
    "Leistung" view too, and a quarter hour with local readings keeps them."""
    storage = Storage(tmp_path / "t.db")
    base = datetime(2026, 6, 1, 12, 0, tzinfo=TZ).timestamp()
    storage.import_energy([{"ts": int(base + i * 900), "pv_wh": 100.0} for i in range(3)], "cloud")
    storage.import_soc([(int(base), 40.0), (int(base + 900), 42.0), (int(base + 1800), 44.0)])
    storage.add_snapshot(snap(base + 1800 + 60, 1000, 400))  # local readings from the third quarter hour on
    client = TestClient(create_app(Runtime({}, storage)))
    entries = client.get("/api/power/timeline", params={"date": "2026-06-01", "step": 300}).json()["entries"]
    imported = [e for e in entries if e["pv"] is None]
    assert [(e["ts"], e["soc"]) for e in imported] == [(base, 40.0), (base + 900, 42.0)]
    assert [e["ts"] for e in entries] == sorted(e["ts"] for e in entries)
    assert all(e["ts"] >= base + 1800 for e in entries if e["pv"] is not None)  # the local quarter hour: readings only
