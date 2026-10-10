"""Electricity tariffs and exchange prices.

A plant can have several tariffs over time (each valid from a date): a fixed price per kWh, or a dynamic
tariff that follows the day-ahead exchange price plus a surcharge (grid fees, levies, margin). Exchange
prices come from the free aWATTar API (Germany/Luxembourg and Austria) and are stored per quarter hour.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.request
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime
from zoneinfo import ZoneInfo

from .storage import QUARTER, Storage

log = logging.getLogger(__name__)

PRICE_SOURCES = {"DE": "https://api.awattar.de/v1/marketdata", "AT": "https://api.awattar.at/v1/marketdata"}
FETCH_EVERY_S = 3600


@dataclass
class Tariff:
    valid_from: str  # YYYY-MM-DD
    kind: str = "fixed"  # fixed | time (own time windows) | dynamic (exchange price)
    price_ct: float = 35.0  # fixed: gross price per kWh from the grid; time: price outside the windows
    surcharge_ct: float = 20.0  # dynamic: gross amount on top of the exchange price (grid fees, levies, margin)
    vat_percent: float = 19.0  # dynamic: VAT applied to the (net) exchange price
    feed_in_ct: float = 8.0  # feed-in compensation per kWh
    area: str = "DE"  # dynamic: price zone DE (DE-LU) or AT
    base_fee_eur_month: float = 0.0  # fixed monthly fee of the supplier (Grundpreis), gross
    # time: own prices in time windows, e.g. a night tariff or time-variable grid fees (§ 14a EnWG, module 3):
    # [{"from": "00:30", "to": "05:30", "price_ct": 9.0}], a window may run past midnight
    windows: list = field(default_factory=list)

    def import_price_ct(self, exchange_eur_mwh: float | None, minute_of_day: int | None = None) -> float | None:
        if self.kind == "fixed":
            return self.price_ct
        if self.kind == "time":
            if minute_of_day is not None:
                for w in self.windows:
                    start, end = _minutes(w["from"]), _minutes(w["to"])
                    inside = start <= minute_of_day < end if start < end else minute_of_day >= start or minute_of_day < end
                    if inside:
                        return float(w["price_ct"])
            return self.price_ct
        if exchange_eur_mwh is None:
            return None
        return exchange_eur_mwh / 10 * (1 + self.vat_percent / 100) + self.surcharge_ct


def _minutes(hhmm: str) -> int:
    h, m = str(hhmm).split(":")
    return int(h) * 60 + int(m)


def _clean_windows(raw) -> list[dict]:
    windows = []
    for w in raw or []:
        start, end = _minutes(w["from"]), _minutes(w["to"])  # raises on bad input
        if not (0 <= start < 1440 and 0 <= end <= 1440) or start == end:
            raise ValueError
        windows.append({"from": f"{start // 60:02d}:{start % 60:02d}", "to": f"{end // 60 % 24:02d}:{end % 60:02d}",
                        "price_ct": round(float(w["price_ct"]), 2)})
    return windows


def validate(raw: list) -> list[Tariff]:
    """Cleans tariffs coming from the web app; raises ValueError with a German message."""
    if not isinstance(raw, list) or not 1 <= len(raw) <= 50:
        raise ValueError("Bitte mindestens einen Tarif angeben.")
    tariffs = []
    for item in raw:
        try:
            # stored as YYYY-MM-DD: tariffs are compared as text, "20240101" would never apply (#223)
            tariff = Tariff(valid_from=date.fromisoformat(str(item["valid_from"])).isoformat(), kind=str(item.get("kind", "fixed")),
                            price_ct=float(item.get("price_ct", 0)), surcharge_ct=float(item.get("surcharge_ct", 0)),
                            vat_percent=float(item.get("vat_percent", 19)), feed_in_ct=float(item.get("feed_in_ct", 0)),
                            area=str(item.get("area", "DE")),
                            base_fee_eur_month=float(item.get("base_fee_eur_month") or 0),
                            windows=_clean_windows(item.get("windows")))
        except (KeyError, TypeError, ValueError):
            raise ValueError("Ungültiger Tarif: bitte Datum und Preise prüfen.") from None
        if tariff.kind not in ("fixed", "time", "dynamic") or tariff.area not in PRICE_SOURCES:
            raise ValueError("Ungültige Tarifart.")
        if not all(-100 <= v <= 200 for v in (tariff.price_ct, tariff.surcharge_ct, tariff.feed_in_ct)):
            raise ValueError("Preise bitte zwischen -100 und 200 ct/kWh angeben.")
        if tariff.kind == "time" and not tariff.windows:
            raise ValueError("Bitte mindestens ein Zeitfenster mit eigenem Preis angeben.")
        if len(tariff.windows) > 6 or not all(-100 <= w["price_ct"] <= 200 for w in tariff.windows):
            raise ValueError("Höchstens 6 Zeitfenster, Preise zwischen -100 und 200 ct/kWh.")
        if not 0 <= tariff.vat_percent <= 100:  # also refuses "nan", which broke the tariffs page (#223)
            raise ValueError("Die Mehrwertsteuer bitte zwischen 0 und 100 % angeben.")
        if not 0 <= tariff.base_fee_eur_month <= 200:
            raise ValueError("Den Grundpreis bitte zwischen 0 und 200 € pro Monat angeben.")
        tariffs.append(tariff)
    tariffs.sort(key=lambda t: t.valid_from)
    if len({t.valid_from for t in tariffs}) != len(tariffs):
        raise ValueError("Zwei Tarife beginnen am selben Tag.")
    return tariffs


class Tariffs:
    def __init__(self, storage: Storage, fallback, feed_in=lambda: None) -> None:
        self.storage = storage
        self.fallback = fallback  # () -> (price_ct, feed_in_ct) from the settings
        self.feed_in = feed_in  # () -> FeedInRate from the EEG rates (#71), or None to use each tariff's own value
        self._last_fetch = 0.0

    def all(self) -> list[Tariff]:
        saved = self.storage.get_meta("tariffs")
        if saved:
            return [Tariff(**t) for t in saved]
        price, feed_in = self.fallback()
        return [Tariff(valid_from="2000-01-01", price_ct=price, feed_in_ct=feed_in)]

    def save(self, raw: list) -> list[Tariff]:
        tariffs = validate(raw)
        self.storage.set_meta("tariffs", [asdict(t) for t in tariffs])
        self._last_fetch = 0.0  # a new dynamic tariff wants prices right away
        return tariffs

    def at(self, day: str) -> Tariff:
        tariffs = self.all()
        current = tariffs[0]
        for tariff in tariffs:
            if tariff.valid_from <= day:
                current = tariff
        eeg = self.feed_in()
        if eeg is not None and day <= eeg.funding_until:
            current = replace(current, feed_in_ct=eeg.ct)
        return current

    def quarter_prices(self, start: float, end: float, tz: ZoneInfo) -> dict[int, float]:
        """Import price (ct/kWh gross) per quarter hour where it is known: every quarter for fixed and time tariffs,
        quarters with an exchange price for dynamic ones."""
        exchange = self.storage.prices(start, end)
        result: dict[int, float] = {}
        cache: dict[str, Tariff] = {}
        ts = int(start // QUARTER * QUARTER)
        while ts < end:
            local = datetime.fromtimestamp(ts, tz)
            day = local.date().isoformat()
            tariff = cache.get(day) or cache.setdefault(day, self.at(day))
            price = tariff.import_price_ct(exchange.get(ts), local.hour * 60 + local.minute)
            if price is not None:
                result[ts] = price
            ts += QUARTER
        return result

    def price_at(self, ts: float, tz: ZoneInfo) -> float | None:
        quarter = int(ts // QUARTER * QUARTER)
        return self.quarter_prices(quarter, quarter + QUARTER, tz).get(quarter)

    def dynamic_areas(self) -> set[str]:
        return {t.area for t in self.all() if t.kind == "dynamic"}

    # ---- money -------------------------------------------------------------

    def money(self, start: float, end: float, tz: ZoneInfo, extra_rows: list[dict] | None = None,
              rows: list[dict] | None = None) -> dict:
        """Savings for a period: self-used solar energy at the price of that moment plus feed-in pay.

        rows replaces the stored quarter hours, e.g. by the inverter's daily counters on the first day."""
        rows = (self.storage.energy(start, end) if rows is None else rows) + (extra_rows or [])
        prices = self.storage.prices(start, end)
        savings = feed_in = grid_cost = 0.0
        missing = 0
        cache: dict[str, Tariff] = {}
        for row in rows:
            day = datetime.fromtimestamp(row["ts"], tz).date().isoformat()
            tariff = cache.get(day) or cache.setdefault(day, self.at(day))
            local = datetime.fromtimestamp(row["ts"], tz)
            price = tariff.import_price_ct(prices.get(int(row["ts"]) // QUARTER * QUARTER), local.hour * 60 + local.minute)
            if price is None:  # dynamic tariff without a known exchange price: use the surcharge only
                missing += 1
                price = tariff.surcharge_ct
            load, grid_import, export = (row.get(k) or 0 for k in ("load", "grid_import", "grid_export"))
            savings += max(0.0, load - grid_import) * price / 100_000 + export * tariff.feed_in_ct / 100_000
            feed_in += export * tariff.feed_in_ct / 100_000
            grid_cost += grid_import * price / 100_000
        base_fee = self.base_fee(start, min(end, time.time()), tz)
        return {"savings_eur": round(savings, 2), "feed_in_eur": round(feed_in, 2), "grid_cost_eur": round(grid_cost, 2),
                "base_fee_eur": round(base_fee, 2),
                # what electricity cost in the end: grid power and base fee minus the feed-in pay
                "net_cost_eur": round(grid_cost + base_fee - feed_in, 2), "incomplete": missing > 0}

    def base_fee(self, start: float, end: float, tz: ZoneInfo) -> float:
        """Monthly base fee for the days between start and end (12 monthly fees spread over 365 days)."""
        total, ts = 0.0, start
        while ts < end:
            step = min(86400.0, end - ts)
            tariff = self.at(datetime.fromtimestamp(ts, tz).date().isoformat())
            total += tariff.base_fee_eur_month * 12 / 365 * step / 86400
            ts += step
        return total

    def charge_cost(self, start: float, end: float, kwh: float, solar_share: float, tz: ZoneInfo) -> tuple[float, float]:
        """Cost of charging kwh (e.g. a car) between start and end, and what it would cost from the grid only.

        Solar energy is valued at the feed-in pay that was given up, grid energy at the average price of the period.
        """
        tariff = self.at(datetime.fromtimestamp(start, tz).date().isoformat())
        known = list(self.quarter_prices(start - QUARTER, max(end, start + 1), tz).values())
        grid = sum(known) / len(known) if known else None
        if grid is None:  # dynamic tariff without a known exchange price
            grid = tariff.surcharge_ct
        share = min(1.0, max(0.0, solar_share))
        cost = kwh * (share * tariff.feed_in_ct + (1 - share) * grid) / 100
        return round(cost, 2), round(kwh * grid / 100, 2)

    # ---- exchange prices ------------------------------------------------------

    async def refresh_prices(self, now: float | None = None) -> int:
        """Fetches exchange prices for today and tomorrow when a dynamic tariff is used (at most hourly)."""
        now = time.time() if now is None else now
        areas = self.dynamic_areas()
        if not areas or now - self._last_fetch < FETCH_EVERY_S:
            return 0
        self._last_fetch = now
        start = int(now // 86400 * 86400) - 86400
        count = 0
        for area in sorted(areas):
            try:
                rows = await asyncio.to_thread(fetch_prices, area, start, start + 3 * 86400)
            except Exception as err:  # noqa: BLE001 - no internet: keep the prices we have
                log.warning("could not fetch exchange prices (%s): %s", area, err)
                continue
            self.storage.save_prices(rows)
            count += len(rows)
        return count


def fetch_prices(area: str, start: float, end: float) -> list[tuple[int, float]]:
    """aWATTar market data -> [(quarter-hour timestamp, EUR/MWh)], hourly values spread over their quarters."""
    url = f"{PRICE_SOURCES[area]}?start={int(start * 1000)}&end={int(end * 1000)}"
    request = urllib.request.Request(url, headers={"User-Agent": "OpenAmpere"})
    with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310 - fixed https URL
        data = json.load(response)["data"]
    rows = []
    for item in data:
        t0, t1 = item["start_timestamp"] // 1000, item["end_timestamp"] // 1000
        for ts in range(t0 // QUARTER * QUARTER, t1, QUARTER):
            rows.append((ts, float(item["marketprice"])))
    return rows
