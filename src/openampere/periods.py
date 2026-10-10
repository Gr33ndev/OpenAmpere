# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Calendar periods (day/week/month/year) in the installation's local time zone."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

PERIODS = ("day", "week", "month", "year")


def parse_anchor(period: str, value: str | None, tz: ZoneInfo) -> date:
    """Accepts YYYY-MM-DD for every period, plus YYYY-Www, YYYY-MM and YYYY like the cloud customer API."""
    if not value:
        return datetime.now(tz).date()
    if period == "week" and "W" in value:
        year, week = value.split("-W")
        return date.fromisocalendar(int(year), int(week), 1)
    if period == "month" and len(value) == 7:
        return date.fromisoformat(value + "-01")
    if period == "year" and len(value) == 4:
        return date(int(value), 1, 1)
    return date.fromisoformat(value)


def period_bounds(period: str, anchor: date) -> tuple[date, date]:
    """Returns [first day, first day of next period)."""
    if period == "day":
        start = anchor
        end = anchor + timedelta(days=1)
    elif period == "week":
        start = anchor - timedelta(days=anchor.weekday())
        end = start + timedelta(days=7)
    elif period == "month":
        start = anchor.replace(day=1)
        end = (start + timedelta(days=32)).replace(day=1)
    elif period == "year":
        start = date(anchor.year, 1, 1)
        end = date(anchor.year + 1, 1, 1)
    else:
        raise ValueError(f"unknown period {period!r}")
    return start, end


def to_ts(day: date, tz: ZoneInfo) -> float:
    return datetime(day.year, day.month, day.day, tzinfo=tz).timestamp()


def bucket_start(ts: float, resolution: str, tz: ZoneInfo) -> float:
    """Start of the bucket containing ts for resolution 15m | 60m | day | month."""
    if resolution == "15m":
        return ts // 900 * 900
    if resolution == "60m":
        local = datetime.fromtimestamp(ts, tz).replace(minute=0, second=0, microsecond=0)
        return local.timestamp()
    local = datetime.fromtimestamp(ts, tz)
    if resolution == "day":
        return to_ts(local.date(), tz)
    if resolution == "month":
        return to_ts(local.date().replace(day=1), tz)
    raise ValueError(f"unknown resolution {resolution!r}")
