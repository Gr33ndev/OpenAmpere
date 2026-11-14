# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Energy counters integrated from the powers of a device that has none, e.g. a Solarmax without "Connectivity+"
(#636): the energy history and the day values must not stay at 0."""

import pytest

from openampere.collector import Collector
from openampere.drivers.base import DeviceInfo, EnergyCounters, Snapshot
from openampere.drivers.solarmax.driver import to_snapshot
from openampere.power_counters import PowerCounters
from openampere.storage import Storage

T0 = 1_780_000_200.0  # a quarter hour's start, after the plausible-clock limit


def snap(ts: float, pv: float = 3000.0, grid: float = -1000.0, battery: float = -1000.0, house: float = 1000.0,
         totals: EnergyCounters | None = None) -> Snapshot:
    """Snapshot signs: grid + = import, battery + = discharging."""
    return Snapshot(timestamp=ts, pv_power=pv, house_power=house, grid_power=grid, battery_power=battery,
                    battery_soc=50, totals=totals or EnergyCounters())


def test_the_powers_become_lifetime_counters():
    counters = PowerCounters(None, "power_counters")
    first = snap(T0)
    counters.add(first)
    assert first.totals.pv == 0 and first.totals.consumption == 0  # nothing counted yet, but counters from now on
    later = snap(T0 + 60, pv=0, grid=500, battery=200)  # the minute before still had the first powers
    counters.add(later)
    assert later.totals.pv == pytest.approx(50)  # 3000 W for a minute
    assert later.totals.grid_export == pytest.approx(1000 / 60, abs=0.01)
    assert later.totals.battery_charge == pytest.approx(1000 / 60, abs=0.01)
    assert later.totals.grid_import == 0 and later.totals.battery_discharge == 0
    assert later.totals.consumption == pytest.approx(1000 / 60, abs=0.01)  # the balance, like for real counters
    last = snap(T0 + 120)
    counters.add(last)
    assert last.totals.pv == pytest.approx(50)  # no PV in the second minute
    assert last.totals.grid_import == pytest.approx(500 / 60, abs=0.01)
    assert last.totals.battery_discharge == pytest.approx(200 / 60, abs=0.01)


def test_no_energy_across_a_gap_and_none_without_a_value():
    counters = PowerCounters(None, "power_counters")
    counters.add(snap(T0))
    after_gap = snap(T0 + 600)  # OpenAmpere or the device was away for ten minutes: no guess
    counters.add(after_gap)
    assert after_gap.totals.pv == 0
    counters.add(snap(T0 + 630, grid=None))  # type: ignore[arg-type]
    unknown_before = snap(T0 + 660)
    counters.add(unknown_before)  # the reading before had no grid power: that half minute is not counted
    assert unknown_before.totals.pv == pytest.approx(3000 * 30 / 3600)


def test_a_device_with_counters_keeps_them():
    counters = PowerCounters(None, "power_counters")
    real = snap(T0, totals=EnergyCounters(pv=5_000_000.0))
    counters.add(real)
    assert real.totals.pv == 5_000_000.0 and real.totals.grid_import is None


def test_the_counters_go_on_after_a_restart(tmp_path):
    storage = Storage(tmp_path / "t.db")
    counters = PowerCounters(storage, "power_counters")
    for ts in (T0, T0 + 60):
        reading = snap(ts)
        counters.add(reading)
        storage.add_snapshot(reading)  # kept with the reading, in the same write
    storage.close()
    restarted = PowerCounters(Storage(tmp_path / "t.db"), "power_counters")
    reading = snap(T0 + 3600)  # after the restart: not counted across the gap, but not from 0 again either
    restarted.add(reading)
    assert reading.totals.pv == pytest.approx(50)


def test_the_energy_history_is_filled_without_device_counters(tmp_path):
    """The bug in #636: live values, but every day value 0. The quarter hours are built from counter differences."""
    storage = Storage(tmp_path / "t.db")
    counters = PowerCounters(storage, "power_counters")
    for minute in range(0, 31):
        reading = snap(T0 + minute * 60)
        counters.add(reading.sanitize())
        storage.add_snapshot(reading)
    rows = storage.energy(T0, T0 + 3600)
    assert len(rows) == 2
    assert rows[0]["pv"] == pytest.approx(750, abs=1)  # 3 kW for a quarter hour
    assert rows[0]["grid_export"] == pytest.approx(250, abs=1)
    assert rows[0]["battery_charge"] == pytest.approx(250, abs=1)
    assert rows[0]["load"] == pytest.approx(250, abs=1)


class Driver:
    def __init__(self, integrates: bool) -> None:
        self.integrates_power = integrates


@pytest.mark.parametrize("integrates", [True, False])
def test_the_collector_integrates_only_for_a_driver_that_asks(tmp_path, integrates):
    collector = Collector(Driver(integrates), Storage(tmp_path / "t.db"), 2, 30)  # type: ignore[arg-type]
    for ts in (T0, T0 + 60):
        reading = snap(ts)
        collector._integrate_power(reading)
    assert reading.totals.pv == (pytest.approx(50) if integrates else None)


def test_integrated_counters_show_decimals(tmp_path):
    """The register map's step (whole kWh for a Solarmax) does not apply to the integrated counters."""
    collector = Collector(Driver(True), Storage(tmp_path / "t.db"), 2, 30)  # type: ignore[arg-type]
    collector.device = DeviceInfo(manufacturer="Solarmax", model="MAX.STORAGE", serial=None, firmware="3.4.4",
                                  energy_step_wh=1000)
    assert collector.energy_step_wh() == 10
    collector.driver = Driver(False)  # type: ignore[assignment]
    assert collector.energy_step_wh() == 1000


def test_all_zero_solarmax_counters_are_none():
    """All-zero counters (no "Connectivity+") are no counters, not counters that start at 0."""
    raw = {"device_state": 1, "pv_power": 900, "pv_plant_power": 900, "battery_power": 100, "house_power": 700,
           "grid_power": 100, "battery_soc": 98, "pv_total": 0, "grid_import_total": 0, "grid_export_total": 0,
           "battery_charge_total": 0, "battery_discharge_total": 0}
    assert to_snapshot(raw, T0).totals.pv is None


def test_a_solarmax_integrates_only_once_it_is_known_to_have_no_counters():
    """After a restart a device with counters may miss its first counter read: nothing is integrated then, its real
    counters must not be mixed with integrated ones."""
    from openampere.drivers.solarmax.driver import SolarmaxDriver

    driver = SolarmaxDriver("127.0.0.1")
    assert not driver.integrates_power  # not known yet (a transient failure leaves it so)
    driver.zeros_seen = True
    assert driver.integrates_power
    driver.zeros_seen, driver.has_counters = False, False  # refused, or never answered
    assert driver.integrates_power
    driver.counters_seen = True  # the counters answered with values once: they are used
    assert not driver.integrates_power
