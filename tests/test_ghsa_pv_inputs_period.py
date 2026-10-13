# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""The power curve per PV input reads raw readings: only one day of them per request."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from openampere.api import create_app
from openampere.drivers.base import EnergyCounters, PvInput, Snapshot
from openampere.runtime import Runtime
from openampere.storage import Storage

TZ = ZoneInfo("Europe/Berlin")


def reading(ts: float, i: int) -> Snapshot:
    s = Snapshot(timestamp=ts, pv_power=3000, house_power=500, grid_power=-2500, battery_power=0, battery_soc=50,
                 totals=EnergyCounters(pv=1000 + i, load=400 + i, grid_import=0, grid_export=600,
                                       battery_charge=0, battery_discharge=0))
    s.pv_inputs = [PvInput(power=2000), PvInput(power=1000)]
    return s


@pytest.fixture
def setup(tmp_path):
    storage = Storage(tmp_path / "t.db")
    base = datetime(2026, 6, 1, 12, 0, tzinfo=TZ).timestamp()
    for i in range(91):
        storage.add_snapshot(reading(base + i * 10, i))
    reads = []
    samples = storage.samples
    storage.samples = lambda start, end: reads.append(end - start) or samples(start, end)
    return TestClient(create_app(Runtime({}, storage))), reads


@pytest.mark.parametrize("period", ["week", "month", "year"])
def test_power_curve_for_more_than_a_day_is_refused_before_reading(setup, period):
    client, reads = setup
    response = client.get("/api/pv/inputs", params={"period": period, "date": "2026-06-01", "mode": "power"})
    assert response.status_code == 400
    assert reads == []  # no raw reading was loaded


def test_power_curve_for_a_day_still_works(setup):
    client, reads = setup
    data = client.get("/api/pv/inputs", params={"period": "day", "date": "2026-06-01", "mode": "power"}).json()
    assert data["mode"] == "power" and data["inputs"] == [1, 2]
    assert [round(v) for v in data["entries"][0]["values"]] == [2000, 1000]
    assert len(reads) == 1 and reads[0] <= 25 * 3600  # one day (25 h on the day the clocks go back)


@pytest.mark.parametrize("period", ["day", "week", "month", "year"])
def test_energy_per_input_works_for_every_period(setup, period):
    client, reads = setup
    response = client.get("/api/pv/inputs", params={"period": period, "date": "2026-06-01", "mode": "energy"})
    assert response.status_code == 200 and response.json()["mode"] == "energy"
    assert reads == []  # energy comes from the quarter-hour table
