from datetime import date

import pytest

from openampere import eeg
from openampere.config import validate
from openampere.runtime import Runtime
from openampere.storage import Storage


def test_rate_is_split_by_installed_power():
    """§ 23c EEG: every kWh is split by the share of the power in each zone, as a grid operator bills it."""
    rate = eeg.rate(date(2025, 3, 15), 12.5)
    assert rate.ct == pytest.approx((10 * 7.94 + 2.5 * 6.88) / 12.5)
    assert [(z["from_kw"], z["kw"], z["ct"]) for z in rate.zones] == [(0, 10.0, 7.94), (10, 2.5, 6.88)]
    assert rate.zones[0]["share"] == pytest.approx(0.8)
    assert (rate.period_from, rate.period_to, rate.funding_until) == ("2025-02-01", "2025-07-31", "2045-12-31")
    assert eeg.rate(date(2025, 3, 15), 8).ct == 7.94  # small plants: only the first zone
    assert eeg.rate(date(2025, 3, 15), 100).ct == pytest.approx((10 * 7.94 + 30 * 6.88 + 60 * 5.62) / 100)


def test_full_feed_in_only_since_the_eeg_2023_rules():
    assert eeg.rate(date(2026, 9, 1), 12, full=True).ct == pytest.approx((10 * 12.22 + 2 * 10.24) / 12)
    before = eeg.rate(date(2022, 7, 29), 12, full=True)
    assert not before.full and before.ct == pytest.approx((10 * 6.24 + 2 * 6.06) / 12)
    assert eeg.rate(date(2022, 7, 30), 12, full=True).full


def test_periods_are_sorted_and_end_where_the_next_begins():
    starts = [row[0] for row in eeg.PERIODS]
    assert starts == sorted(starts) and len(set(starts)) == len(starts)
    assert eeg.rate(date(2018, 7, 31), 5).period_to == "2018-07-31"
    assert eeg.rate(date(2026, 12, 31), 5).period_to == eeg.LAST_DAY


@pytest.mark.parametrize("day, kwp, message", [
    (date(2017, 12, 31), 10, "vor 2018"),
    (date(2027, 6, 1), 10, "noch keine Sätze"),
    (date(2025, 1, 1), 0, "Modulleistung"),
    (date(2025, 1, 1), 120, "über 100 kWp"),
])
def test_out_of_scope(day, kwp, message):
    with pytest.raises(ValueError, match=message):
        eeg.rate(day, kwp)


def test_commissioning_date_setting():
    assert validate({"pv.commissioning_date": " 2025-03-15 "}) == {"pv.commissioning_date": "2025-03-15"}
    assert validate({"pv.commissioning_date": ""}) == {"pv.commissioning_date": ""}
    for bad in ("15.03.2025", "1999-12-31", "2025-02-30"):
        with pytest.raises(ValueError, match="Inbetriebnahme"):
            validate({"pv.commissioning_date": bad})


async def test_tariffs_use_the_eeg_rate_when_switched_on(tmp_path):
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    await runtime.update_settings({"tariff.feed_in_ct": 7.94, "pv.installed_kwp": 12.5,
                                   "pv.commissioning_date": "2025-03-15"})
    assert runtime.tariffs.at("2026-03-01").feed_in_ct == 7.94  # own value until switched on
    assert runtime.eeg_view()["rate"]["ct"] == pytest.approx(7.728)

    await runtime.update_settings({"tariff.feed_in_auto": True})
    assert runtime.tariffs.at("2026-03-01").feed_in_ct == pytest.approx(7.728)
    assert runtime.tariffs.at("2046-01-01").feed_in_ct == 7.94  # after 20 years the EEG compensation ends
    assert runtime.tariffs.all()[0].feed_in_ct == 7.94  # the saved tariffs keep their own value

    await runtime.update_settings({"pv.installed_kwp": 0})
    assert runtime.tariffs.at("2026-03-01").feed_in_ct == 7.94  # not determinable: own value
    assert runtime.eeg_view()["error"] == "Bitte die installierte Modulleistung angeben."
