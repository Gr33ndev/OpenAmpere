# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Step of the inverter's energy counters (#194): driver hint, detection from the recorded quarters, /api/status."""

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from openampere.api import create_app
from openampere.drivers.base import DeviceInfo, EnergyCounters, Snapshot
from openampere.drivers.foxess.registers import H3_LEGACY, H3_NEW
from openampere.drivers.regs import counter_step_wh
from openampere.drivers.saj.registers import VALUES as SAJ_VALUES
from openampere.runtime import Runtime
from openampere.storage import FLOWS, QUARTER, Storage

BASE = datetime(2026, 6, 1, 0, 0, tzinfo=ZoneInfo("Europe/Berlin")).timestamp()


def record(storage: Storage, quarters: int, unit: float, start: float = BASE, gap_after: int | None = None) -> None:
    """One reading per quarter hour; the counters grow by varying multiples of `unit` Wh."""
    totals = dict.fromkeys(FLOWS, 1_000_000.0)
    ts = start
    for i in range(quarters + 1):
        storage.add_snapshot(Snapshot(timestamp=ts + 10, battery_soc=50, totals=EnergyCounters(**totals)))
        for n, flow in enumerate(FLOWS):
            totals[flow] += unit * ((i * 7 + n * 3) % 13 + 1)
        ts += QUARTER * (8 if i == gap_after else 1)  # an outage of two hours: its difference gets spread


def test_driver_hint_follows_the_register_map():
    assert counter_step_wh(H3_LEGACY.values) == 100  # classic FoxESS map: 0.1 kWh
    assert counter_step_wh(H3_NEW.values) == 10  # newer map: 0.01 kWh
    assert counter_step_wh(SAJ_VALUES) == 10
    assert counter_step_wh({}) is None


def test_counters_in_tenths_of_a_kwh_are_detected(tmp_path):
    storage = Storage(tmp_path / "t.db")
    record(storage, 30, 100)
    assert storage.energy_step_wh(now=BASE) == 100


def test_counters_in_hundredths_of_a_kwh_are_detected(tmp_path):
    storage = Storage(tmp_path / "t.db")
    record(storage, 30, 10)
    assert storage.energy_step_wh(now=BASE) == 10


def test_spread_outage_does_not_hide_the_coarse_step(tmp_path):
    storage = Storage(tmp_path / "t.db")
    record(storage, 30, 100, gap_after=10)
    spread = storage.energy(BASE + 10 * QUARTER, BASE + 18 * QUARTER)
    assert len(spread) == 8 and spread[0]["pv"] % 100  # shares of the difference, no multiples of 0.1 kWh
    assert storage.energy_step_wh(now=BASE) == 100


def test_too_little_data_decides_nothing(tmp_path):
    storage = Storage(tmp_path / "t.db")
    record(storage, 2, 100)  # 2 quarters, 12 differences
    assert storage.energy_step_wh(now=BASE) is None


def test_imported_history_is_ignored(tmp_path):
    storage = Storage(tmp_path / "t.db")
    storage.import_energy([{"ts": int(BASE) - (i + 1) * QUARTER, **dict.fromkeys(FLOWS, 123.4), "soc": None}
                           for i in range(40)], "cloud")
    assert storage.energy_step_wh(now=BASE) is None


def test_result_is_cached_for_an_hour(tmp_path):
    storage = Storage(tmp_path / "t.db")
    record(storage, 30, 100)
    assert storage.energy_step_wh(now=BASE) == 100
    record(storage, 30, 10, start=BASE + 40 * QUARTER)  # now the finer counters dominate
    assert storage.energy_step_wh(now=BASE + 600) == 100
    assert storage.energy_step_wh(now=BASE + 3600) == 10


def test_status_reports_the_effective_step(tmp_path):
    storage = Storage(tmp_path / "t.db")
    runtime = Runtime({}, storage)
    client = TestClient(create_app(runtime))
    assert client.get("/api/status").json()["energy_step_wh"] == 10  # nothing known: two decimals
    runtime.collector.device = DeviceInfo("FoxESS", "H3", register_map="foxess_h3", energy_step_wh=100)
    assert client.get("/api/status").json()["energy_step_wh"] == 100  # the driver's hint

    storage = Storage(tmp_path / "u.db")
    record(storage, 30, 10)  # the data shows finer steps than the hint
    runtime = Runtime({}, storage)
    runtime.collector.device = DeviceInfo("FoxESS", "H3", energy_step_wh=100)
    assert TestClient(create_app(runtime)).get("/api/status").json()["energy_step_wh"] == 10
