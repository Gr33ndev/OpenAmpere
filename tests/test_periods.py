# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Calendar periods and buckets in the installation's time zone, including the daylight saving changes (#236)."""

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from openampere.periods import bucket_start, parse_anchor, period_bounds, to_ts

TZ = ZoneInfo("Europe/Berlin")


def utc(*args) -> float:
    return datetime(*args, tzinfo=timezone.utc).timestamp()


def test_anchor_defaults_to_today():
    before = datetime.now(TZ).date()
    assert parse_anchor("day", None, TZ) in (before, before + timedelta(days=1))
    assert parse_anchor("month", "", TZ) in (before, before + timedelta(days=1))


def test_anchor_formats():
    assert parse_anchor("year", "2026", TZ) == date(2026, 1, 1)
    assert parse_anchor("year", "2026-05-17", TZ) == date(2026, 5, 17)
    assert parse_anchor("week", "2026-05-17", TZ) == date(2026, 5, 17)
    with pytest.raises(ValueError):
        parse_anchor("day", "17.05.2026", TZ)


def test_period_bounds():
    anchor = date(2026, 12, 31)
    assert period_bounds("day", anchor) == (anchor, date(2027, 1, 1))
    assert period_bounds("month", anchor) == (date(2026, 12, 1), date(2027, 1, 1))
    assert period_bounds("year", anchor) == (date(2026, 1, 1), date(2027, 1, 1))
    assert period_bounds("week", date(2027, 1, 1)) == (date(2026, 12, 28), date(2027, 1, 4))
    with pytest.raises(ValueError, match="unknown period"):
        period_bounds("decade", anchor)


def test_quarter_hour_and_hour_buckets():
    assert bucket_start(utc(2026, 6, 1, 10, 14, 59), "15m", TZ) == utc(2026, 6, 1, 10, 0)
    assert bucket_start(utc(2026, 6, 1, 10, 15), "15m", TZ) == utc(2026, 6, 1, 10, 15)
    assert bucket_start(utc(2026, 6, 1, 10, 59), "60m", TZ) == utc(2026, 6, 1, 10, 0)
    # 25 October 2026: 02:00-03:00 local time happens twice, first in summer time, then in winter time
    assert bucket_start(utc(2026, 10, 25, 0, 30), "60m", TZ) == utc(2026, 10, 25, 0, 0)
    assert bucket_start(utc(2026, 10, 25, 1, 30), "60m", TZ) == utc(2026, 10, 25, 1, 0)


def test_day_and_month_buckets_start_at_local_midnight():
    noon = utc(2026, 3, 29, 10, 0)  # the day summer time starts: only 23 hours long
    start = bucket_start(noon, "day", TZ)
    assert start == utc(2026, 3, 28, 23, 0) == to_ts(date(2026, 3, 29), TZ)
    assert to_ts(date(2026, 3, 30), TZ) - start == 23 * 3600
    assert bucket_start(utc(2026, 10, 15, 12, 0), "month", TZ) == utc(2026, 9, 30, 22, 0)
    # shortly after local midnight the bucket is already the new day and month
    assert bucket_start(utc(2026, 10, 31, 23, 30), "month", TZ) == utc(2026, 10, 31, 23, 0)
    with pytest.raises(ValueError, match="unknown resolution"):
        bucket_start(noon, "week", TZ)
