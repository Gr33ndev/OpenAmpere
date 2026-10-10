"""Monthly prepayments (Abschläge) compared with the energy so far.

Grid power is usually paid with a monthly prepayment to the supplier, and the grid operator pays the feed-in
compensation the same way. Once a year both are settled. This module compares what was paid up to today with what the
energy so far costs (or earns) under the user's tariffs. The energy comes from the grid operator's meters where
OpenAmpere can fetch them (#60), otherwise from the inverter. The projection to the end of the year is still
returned, the app no longer shows it (#59).
"""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from .storage import Storage
from .tariffs import Tariffs

if TYPE_CHECKING:
    from .gridmeter import GridMeter

KINDS = ("import", "export")
# Typical share of a year's energy per month in Germany (in %): feed-in follows the sun, grid import the darkness.
# Used to project the rest of the year when there is no data from the year before.
TYPICAL_MONTH_SHARE = {
    "export": [2, 4, 8, 11, 13, 14, 14, 12, 9, 6, 4, 3],
    "import": [13, 11, 9, 7, 6, 5, 5, 6, 7, 9, 10, 12],
}


def validate(raw: dict) -> dict:
    """Cleans the prepayment settings from the web app; raises ValueError with a German message."""
    if not isinstance(raw, dict):
        raise ValueError("Ungültige Angaben.")
    clean = {}
    for kind in KINDS:
        item = raw.get(kind) or {}
        try:
            start_month = int(item.get("start_month", 1))
            payments = []
            for p in item.get("payments") or []:
                # stored as YYYY-MM: "2024-1" would sort and compare wrongly (#223)
                month = datetime.strptime(str(p["from"]), "%Y-%m").strftime("%Y-%m")
                payments.append({"from": month, "eur": round(float(p["eur"]), 2)})
        except (KeyError, TypeError, ValueError):
            raise ValueError("Bitte für jeden Abschlag einen Monat und einen Betrag angeben.") from None
        if not 1 <= start_month <= 12:
            raise ValueError("Ungültiger Monat für den Beginn des Abrechnungsjahres.")
        if any(not 0 <= p["eur"] <= 5000 for p in payments) or len(payments) > 60:
            raise ValueError("Abschläge bitte zwischen 0 und 5000 € angeben.")
        if len({p["from"] for p in payments}) != len(payments):
            raise ValueError("Zwei Abschläge beginnen im selben Monat.")
        clean[kind] = {"start_month": start_month, "payments": sorted(payments, key=lambda p: p["from"])}
    return clean


def _add_months(day: date, months: int) -> date:
    total = day.year * 12 + day.month - 1 + months
    return date(total // 12, total % 12 + 1, 1)


def _amount(payments: list[dict], month: date) -> float:
    """Prepayment valid in a month: the newest one that started before or in it."""
    key = month.strftime("%Y-%m")
    current = 0.0
    for p in payments:
        if p["from"] <= key:
            current = p["eur"]
    return current


class Billing:
    def __init__(self, storage: Storage, tariffs: Tariffs, gridmeter: GridMeter | None = None) -> None:
        self.storage = storage
        self.tariffs = tariffs
        self.gridmeter = gridmeter

    def settings(self) -> dict:
        saved = self.storage.get_meta("billing") or {}
        return validate({k: saved.get(k) or {} for k in KINDS})

    def save(self, raw: dict) -> dict:
        clean = validate(raw)
        self.storage.set_meta("billing", clean)
        return clean

    def status(self, tz: ZoneInfo, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        settings = self.settings()
        return {kind: self._year(kind, settings[kind], tz, now) for kind in KINDS}

    def _year(self, kind: str, cfg: dict, tz: ZoneInfo, now: float) -> dict | None:
        if not cfg["payments"]:
            return None
        today = datetime.fromtimestamp(now, tz).date()
        start = date(today.year, cfg["start_month"], 1)
        if start > today:
            start = date(today.year - 1, cfg["start_month"], 1)
        months = [_add_months(start, i) for i in range(12)]
        end = _add_months(start, 12)
        ts = lambda d: datetime(d.year, d.month, d.day, tzinfo=tz).timestamp()  # noqa: E731
        start_ts, end_ts = ts(start), ts(end)

        # so far: measured energy with the tariffs that applied
        money = self.tariffs.money(start_ts, now, tz)
        flows = self.storage.energy_sum(start_ts, now)
        field = "grid_import" if kind == "import" else "grid_export"
        kwh = (flows.get(field) or 0) / 1000
        so_far = money["grid_cost_eur"] + money["base_fee_eur"] if kind == "import" else money["feed_in_eur"]
        first = self.storage.first_quarter(start_ts, now)
        recorded_from = datetime.fromtimestamp(first, tz).date() if first else today
        # the grid operator's meter values (#60) replace the inverter's values for the days they cover
        meter = self.storage.meter_days(kind, start.isoformat(), (today + timedelta(days=1)).isoformat(),
                                        self.gridmeter.active_ids() if self.gridmeter else [])
        meter_from = date.fromisoformat(min(meter)) if meter else None
        meter_to = date.fromisoformat(max(meter)) + timedelta(days=1) if meter else None
        tariff = self.tariffs.at(today.isoformat())
        if kind == "import":
            fallback = tariff.import_price_ct(None)
            price = money["grid_cost_eur"] / kwh if kwh > 1 else (fallback if fallback is not None else tariff.surcharge_ct) / 100
        else:
            price = tariff.feed_in_ct / 100

        inverter_in_meter = meter_kwh = 0.0
        deviation = None
        if meter:
            inverter_in_meter = (self.storage.energy_sum(ts(meter_from), min(ts(meter_to), now)).get(field) or 0) / 1000
            meter_kwh = sum(meter.values())
            # how far the inverter is off: only over days that both recorded
            both_from = max(meter_from, recorded_from)
            both = [d for d in self.storage.energy_days(ts(both_from), ts(meter_to), tz) if d in meter]
            if len(both) >= 7 and len(both) >= 0.9 * (meter_to - both_from).days:
                inv = (self.storage.energy_sum(ts(both_from), ts(meter_to)).get(field) or 0) / 1000
                ref = sum(meter[d] for d in both)
                deviation = round((inv - ref) / ref * 100, 1) if ref > 1 else None

        # before the recording started (OpenAmpere installed during the billing year): estimated
        estimate_until = min(recorded_from, meter_from) if meter_from else recorded_from
        covered = _share(kind, recorded_from, today)
        missing_kwh = kwh * _share(kind, start, estimate_until) / covered if covered > 0 and estimate_until > start else 0.0
        # the rest of the year: last year's energy if it was recorded, otherwise the typical course of a year
        rest_kwh, method = self._last_year(field, now, end_ts)
        if rest_kwh is None:
            rest_kwh, method = (kwh * _share(kind, today, end) / covered, "typical") if covered > 0 else (0.0, "none")
        rest_eur = rest_kwh * price
        if kind == "import":
            rest_eur += tariff.base_fee_eur_month * 12 / 365 * max(0.0, (end_ts - now) / 86400)
        # the base fee is already in money() for the whole period, also before the recording started
        so_far += (missing_kwh + meter_kwh - inverter_in_meter) * price
        kwh_total_so_far = kwh + missing_kwh + meter_kwh - inverter_in_meter

        paid_months = [m for m in months if m <= today]
        paid = sum(_amount(cfg["payments"], m) for m in paid_months)
        # "Stand heute": the current month only for the days that have passed, like the consumption (#59)
        days_in_month = (_add_months(today, 1) - today.replace(day=1)).days
        paid_to_date = paid - _amount(cfg["payments"], today.replace(day=1)) * (1 - (today.day - 1 + 0.5) / days_in_month)
        # days without any reading (neither inverter nor meter) since the recording started: the values are too low then
        watched_from = max(start, min(recorded_from, meter_from) if meter_from else recorded_from)
        known = self.storage.energy_days(ts(watched_from), ts(today), tz) | set(meter)
        missing_days = sum(1 for i in range((today - watched_from).days)
                           if (watched_from + timedelta(days=i)).isoformat() not in known)
        yearly = sum(_amount(cfg["payments"], m) for m in months)
        projected = so_far + rest_eur
        # positive = money back for the user: import paid more than used, export earned more than prepaid
        sign = 1 if kind == "import" else -1
        return {
            "from": start.isoformat(), "to": end.isoformat(),
            "months_paid": len(paid_months), "paid_eur": round(paid, 2), "yearly_payments_eur": round(yearly, 2),
            "so_far_kwh": round(kwh_total_so_far, 1), "so_far_eur": round(so_far, 2),
            "estimated_before": estimate_until.isoformat() if missing_kwh else None,
            "meter": {"source": self.gridmeter.label, "from": meter_from.isoformat(),
                      "until": (meter_to - timedelta(days=1)).isoformat(), "kwh": round(meter_kwh, 1),
                      "deviation_percent": deviation} if meter and self.gridmeter else None,
            "projected_kwh": round(kwh_total_so_far + rest_kwh, 0), "projected_eur": round(projected, 2), "method": method,
            "balance_now_eur": round(sign * (paid - so_far), 2),
            "paid_to_date_eur": round(paid_to_date, 2), "balance_today_eur": round(sign * (paid_to_date - so_far), 2),
            "missing_days": missing_days, "balance_end_eur": round(sign * (yearly - projected), 2),
            "fitting_payment_eur": round(projected / 12, 0),
            "incomplete": money["incomplete"],
        }

    def _last_year(self, field: str, now: float, end_ts: float) -> tuple[float | None, str]:
        """Energy of the rest of the billing year, one year earlier, if it was recorded almost completely."""
        last_from, last_to = now - 365 * 86400, end_ts - 365 * 86400
        if last_to <= last_from:
            return 0.0, "last_year"
        if self.storage.days_with_energy(last_from, last_to) >= 0.9 * (last_to - last_from) / 86400:
            return (self.storage.energy_sum(last_from, last_to).get(field) or 0) / 1000, "last_year"
        return None, ""


def _share(kind: str, frm: date, to: date) -> float:
    """Share of a typical year (in %) between two days, the months counted pro rata."""
    total, day = 0.0, frm
    while day < to:
        nxt = min(_add_months(day, 1), to)
        days_in_month = (_add_months(day, 1) - day.replace(day=1)).days
        total += TYPICAL_MONTH_SHARE[kind][day.month - 1] * (nxt - day).days / days_in_month
        day = nxt
    return total
