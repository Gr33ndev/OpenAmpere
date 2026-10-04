from openampere.drivers.base import EnergyCounters, Snapshot
from openampere.outages import Outages
from openampere.storage import Storage


def reading(ts, off_grid, soc, pv=10_000, load=50_000, charge=20_000, discharge=18_000):
    return Snapshot(timestamp=ts, off_grid=off_grid, battery_soc=soc,
                    totals=EnergyCounters(pv=pv, load=load, battery_charge=charge, battery_discharge=discharge))


def test_power_cut_is_recorded_with_energy_and_lowest_charge(tmp_path):
    """#51: how long the grid was gone and how far battery and sun carried the house."""
    storage = Storage(tmp_path / "t.db")
    outages = Outages(storage)
    assert outages.observe(reading(1000, False, 70)) is None
    outages.observe(reading(2000, True, 70))
    for ts in range(2200, 3200, 200):  # readings every few minutes at most, as while polling
        outages.observe(reading(ts, True, 61, pv=10_400, load=51_200, discharge=18_900))
    outages.observe(reading(3200, True, 58, pv=10_500, load=51_900, discharge=19_500))
    outages.observe(reading(3400, True, 59, pv=10_550, load=52_000, discharge=19_550))
    assert outages.current["soc_min"] == 58
    assert Outages(storage).current["start"] == 2000  # survives a restart
    done = outages.observe(reading(3600, False, 59, pv=10_600, load=52_100, discharge=19_600))
    assert done["duration_s"] == 1600 and (done["soc_start"], done["soc_end"], done["soc_min"]) == (70, 59, 58)
    assert (done["load_kwh"], done["solar_kwh"], done["battery_kwh"]) == (2.1, 0.6, 1.6)
    assert done["dark_since"] is None
    view = outages.view()
    assert view["count"] == 1 and view["total_s"] == 1600 and view["current"] is None


def test_battery_empty_during_a_power_cut(tmp_path):
    """No readings for a while during an outage: the inverter went dark, usually with an empty battery."""
    outages = Outages(Storage(tmp_path / "t.db"))
    outages.observe(reading(0, True, 15))
    outages.observe(reading(200, True, 10))
    outages.observe(reading(4000, True, 12))  # back after an hour, still off-grid (sun)
    outages.observe(reading(4100, True, 13))
    done = outages.observe(reading(4200, False, 14))
    assert done["dark_since"] == 200
    assert outages.observe(reading(5010, None, 14)) is None  # devices without the flag change nothing
