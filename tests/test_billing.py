# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from openampere.billing import Billing, validate
from openampere.storage import Storage
from openampere.tariffs import Tariffs

TZ = ZoneInfo("Europe/Berlin")


def daily_rows(first: date, days: int, grid_import=0.0, grid_export=0.0):
    rows = []
    for i in range(days):
        day = first + timedelta(days=i)
        ts = int(datetime(day.year, day.month, day.day, 12, tzinfo=TZ).timestamp())
        rows.append({"ts": ts, "pv": 0.0, "load": grid_import, "grid_import": grid_import, "grid_export": grid_export,
                     "battery_charge": 0.0, "battery_discharge": 0.0, "soc": 50.0})
    return rows


@pytest.fixture
def billing(tmp_path):
    storage = Storage(tmp_path / "t.db")
    tariffs = Tariffs(storage, lambda: (30.0, 8.0))
    tariffs.save([{"valid_from": "2025-01-01", "price_ct": 30, "feed_in_ct": 8, "base_fee_eur_month": 12}])
    return storage, Billing(storage, tariffs)


def test_grid_import_against_prepayments(billing):
    storage, bill = billing
    storage.import_energy(daily_rows(date(2026, 1, 1), 181, grid_import=10_000), "local")  # Jan to June, 10 kWh a day
    bill.save({"import": {"start_month": 1, "payments": [{"from": "2026-01", "eur": 50}]}})
    now = datetime(2026, 7, 1, 12, tzinfo=TZ).timestamp()
    year = bill.status(TZ, now)["import"]
    assert bill.status(TZ, now)["export"] is None  # no prepayments entered
    assert (year["from"], year["to"], year["months_paid"]) == ("2026-01-01", "2027-01-01", 7)
    used = 1810 * 0.30 + 12 * 12 / 365 * 181.5  # energy plus the base fee for the days so far
    assert year["so_far_eur"] == pytest.approx(used, abs=0.05)
    assert year["balance_now_eur"] == pytest.approx(350 - used, abs=0.05)  # negative: more used than prepaid
    # stand today: July counts only for the half day that has passed (#59)
    assert year["paid_to_date_eur"] == pytest.approx(6 * 50 + 50 * 0.5 / 31, abs=0.01)
    assert year["balance_today_eur"] == pytest.approx(year["paid_to_date_eur"] - used, abs=0.05)
    assert year["missing_days"] == 0
    # no data from last year: the rest follows the typical year (Jan-Jun are 51 % of the grid import)
    assert year["method"] == "typical"
    rest = 1810 * 49 / 51 * 0.30 + 12 * 12 / 365 * 183.5
    assert year["projected_eur"] == pytest.approx(used + rest, abs=0.5)
    assert year["balance_end_eur"] == pytest.approx(600 - used - rest, abs=0.5)
    assert year["fitting_payment_eur"] == round((used + rest) / 12)


def test_feed_in_with_changed_prepayment_and_late_start(billing):
    storage, bill = billing
    # OpenAmpere recorded only from April on: January to March are estimated
    storage.import_energy(daily_rows(date(2026, 4, 1), 91, grid_export=20_000), "local")
    bill.save({"export": {"start_month": 1, "payments": [{"from": "2025-07", "eur": 30}, {"from": "2026-03", "eur": 35}]}})
    year = bill.status(TZ, datetime(2026, 7, 1, 12, tzinfo=TZ).timestamp())["export"]
    assert year["yearly_payments_eur"] == 2 * 30 + 10 * 35
    assert year["paid_eur"] == 2 * 30 + 5 * 35
    assert year["estimated_before"] == "2026-04-01"
    measured = 91 * 20  # kWh, April to June = 38 % of the typical feed-in year, January to March 14 %
    assert year["so_far_kwh"] == pytest.approx(measured * (1 + 14 / 38), abs=1)
    assert year["so_far_eur"] == pytest.approx(measured * (1 + 14 / 38) * 0.08, abs=0.05)


def test_last_year_is_used_when_recorded(billing):
    storage, bill = billing
    storage.import_energy(daily_rows(date(2025, 1, 1), 365 + 181, grid_import=4_000), "local")
    bill.save({"import": {"start_month": 1, "payments": [{"from": "2025-01", "eur": 40}]}})
    year = bill.status(TZ, datetime(2026, 7, 1, 12, tzinfo=TZ).timestamp())["import"]
    assert year["method"] == "last_year"
    assert year["projected_kwh"] == pytest.approx(365 * 4, abs=3)


def test_validation():
    with pytest.raises(ValueError):
        validate({"import": {"start_month": 13, "payments": []}})
    with pytest.raises(ValueError):
        validate({"import": {"payments": [{"from": "2026-01", "eur": 10}, {"from": "2026-01", "eur": 20}]}})
    with pytest.raises(ValueError):
        validate({"export": {"payments": [{"from": "Januar", "eur": 10}]}})
    assert validate({})["import"] == {"start_month": 1, "payments": []}


def test_base_fee_in_money(billing):
    storage, bill = billing
    start = datetime(2026, 3, 1, tzinfo=TZ).timestamp()
    money = bill.tariffs.money(start, start + 10 * 86400, TZ)
    assert money["base_fee_eur"] == pytest.approx(12 * 12 / 365 * 10, abs=0.01)
    assert money["net_cost_eur"] == money["grid_cost_eur"] + money["base_fee_eur"] - money["feed_in_eur"]


def test_missing_days_are_counted(billing):
    """Days without readings make the values so far too low; the app says so (#59)."""
    storage, bill = billing
    storage.import_energy(daily_rows(date(2026, 1, 1), 60, grid_import=5_000), "local")
    storage.import_energy(daily_rows(date(2026, 3, 12), 30, grid_import=5_000), "local")  # 10 days missing
    bill.save({"import": {"start_month": 1, "payments": [{"from": "2026-01", "eur": 50}]}})
    year = bill.status(TZ, datetime(2026, 4, 11, 12, tzinfo=TZ).timestamp())["import"]
    assert year["missing_days"] == 10
