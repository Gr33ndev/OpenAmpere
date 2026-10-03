"""Monthly prepayments (Abschläge) and a forecast of the annual bill.

Grid power is usually paid with a monthly prepayment to the supplier, and the grid operator pays the feed-in
compensation the same way. Once a year both are settled. This module compares what was paid so far with what the
measured energy costs (or earns) under the user's tariffs and projects the year to its end, so the annual bill is
no surprise.
"""

from __future__ import annotations

import time
from datetime import date, datetime
from zoneinfo import ZoneInfo

from .storage import Storage
from .tariffs import Tariffs

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
                month = str(p["from"])
                datetime.strptime(month, "%Y-%m")
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
    def __init__(self, storage: Storage, tariffs: Tariffs) -> None:
        self.storage = storage
        self.tariffs = tariffs

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
        tariff = self.tariffs.at(today.isoformat())
        if kind == "import":
            fallback = tariff.import_price_ct(None)
            price = money["grid_cost_eur"] / kwh if kwh > 1 else (fallback if fallback is not None else tariff.surcharge_ct) / 100
        else:
            price = tariff.feed_in_ct / 100

        # before the recording started (OpenAmpere installed during the billing year): estimated
        covered = _share(kind, recorded_from, today)
        missing_kwh = kwh * _share(kind, start, recorded_from) / covered if covered > 0 and recorded_from > start else 0.0
        # the rest of the year: last year's energy if it was recorded, otherwise the typical course of a year
        rest_kwh, method = self._last_year(field, now, end_ts)
        if rest_kwh is None:
            rest_kwh, method = (kwh * _share(kind, today, end) / covered, "typical") if covered > 0 else (0.0, "none")
        rest_eur = rest_kwh * price
        if kind == "import":
            rest_eur += tariff.base_fee_eur_month * 12 / 365 * max(0.0, (end_ts - now) / 86400)
            missing_eur = missing_kwh * price + tariff.base_fee_eur_month * 12 / 365 * (recorded_from - start).days
        else:
            missing_eur = missing_kwh * price
        so_far += missing_eur
        kwh_total_so_far = kwh + missing_kwh

        paid_months = [m for m in months if m <= today]
        paid = sum(_amount(cfg["payments"], m) for m in paid_months)
        yearly = sum(_amount(cfg["payments"], m) for m in months)
        projected = so_far + rest_eur
        # positive = money back for the user: import paid more than used, export earned more than prepaid
        sign = 1 if kind == "import" else -1
        return {
            "from": start.isoformat(), "to": end.isoformat(),
            "months_paid": len(paid_months), "paid_eur": round(paid, 2), "yearly_payments_eur": round(yearly, 2),
            "so_far_kwh": round(kwh_total_so_far, 1), "so_far_eur": round(so_far, 2),
            "estimated_before": recorded_from.isoformat() if missing_kwh else None,
            "projected_kwh": round(kwh_total_so_far + rest_kwh, 0), "projected_eur": round(projected, 2), "method": method,
            "balance_now_eur": round(sign * (paid - so_far), 2), "balance_end_eur": round(sign * (yearly - projected), 2),
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
