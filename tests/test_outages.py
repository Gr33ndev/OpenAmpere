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


def test_one_off_grid_reading_is_not_a_power_cut(tmp_path):
    """#89: the flag of a single reading can be wrong, e.g. while the connection breaks down."""
    outages = Outages(Storage(tmp_path / "t.db"))
    assert outages.observe(reading(1000, True, 100)) is None
    assert outages.current is None  # not yet
    assert outages.observe(reading(1010, False, 100)) is None
    assert outages.view()["count"] == 0

    # the case from #89: one off-grid reading, then 8 minutes without readings, then the grid as usual
    outages.observe(reading(2000, True, 100))
    assert outages.observe(reading(2480, False, 100)) is None
    outages.observe(reading(3000, True, 100))
    outages.observe(reading(3500, True, 100))  # too far apart to confirm each other
    assert outages.current is None and outages.view()["count"] == 0


def test_flag_is_ignored_while_power_flows_to_or_from_the_grid(tmp_path):
    outages = Outages(Storage(tmp_path / "t.db"))
    for ts in (0, 10, 20):
        snap = reading(ts, True, 100)
        snap.grid_power = -3600  # exporting: the grid is there
        outages.observe(snap)
    assert outages.current is None
    quiet = reading(30, True, 100)
    quiet.grid_power = 0  # no flow at the grid meter, as in a real power cut
    outages.observe(quiet)
    outages.observe(reading(40, True, 100))
    assert outages.current["start"] == 30


def test_gap_with_a_charged_battery_means_missing_readings(tmp_path):
    """#89: no readings for a while is not "battery empty" when the battery was full before."""
    outages = Outages(Storage(tmp_path / "t.db"))
    outages.observe(reading(0, True, 90))
    outages.observe(reading(10, True, 90))
    done = outages.observe(reading(900, False, 88))  # 15 minutes without readings
    assert done["dark_since"] == 10 and done["gap_reason"] == "no_data"


def test_entries_from_a_single_reading_are_removed(tmp_path):
    """Recorded before #89: one off-grid reading followed by a gap, with a charged battery."""
    storage = Storage(tmp_path / "t.db")
    bogus = {"start": 1000, "end": 1480, "duration_s": 480, "soc_start": 100, "soc_end": 100, "soc_min": 100,
             "load_kwh": None, "solar_kwh": None, "battery_kwh": None, "dark_since": 1000}
    real = {"start": 5000, "end": 9000, "duration_s": 4000, "soc_start": 20, "soc_end": 30, "soc_min": 8,
            "load_kwh": 1.0, "solar_kwh": 0.2, "battery_kwh": 0.8, "dark_since": 5000}  # empty right away: kept
    storage.set_meta("outages", [bogus, real])
    assert Outages(storage).history() == [real]


def test_inverter_switched_off_while_openampere_kept_running(tmp_path):
    """#93: the inverter was switched off on purpose. OpenAmpere kept asking it, so it had power: not "battery empty"."""
    storage = Storage(tmp_path / "t.db")
    outages = Outages(storage)
    outages.observe(reading(0, True, 15))
    outages.observe(reading(10, True, 15))
    outages.unreachable(100)  # a short hiccup does not count yet
    assert not outages.current.get("polled_in_gap")
    outages.unreachable(600)
    assert Outages(storage).current["polled_in_gap"]  # also after a restart
    done = outages.observe(reading(1800, False, 15))
    assert done["dark_since"] == 10 and done["gap_reason"] == "inverter_off"
    outages.unreachable(5000)  # no outage running: nothing to note
