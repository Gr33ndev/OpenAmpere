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
from dataclasses import asdict, dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from .storage import QUARTER, Storage

log = logging.getLogger(__name__)

PRICE_SOURCES = {"DE": "https://api.awattar.de/v1/marketdata", "AT": "https://api.awattar.at/v1/marketdata"}
FETCH_EVERY_S = 3600


@dataclass
class Tariff:
    valid_from: str  # YYYY-MM-DD
    kind: str = "fixed"  # fixed | dynamic
    price_ct: float = 35.0  # fixed: gross price per kWh from the grid
    surcharge_ct: float = 20.0  # dynamic: gross amount on top of the exchange price (grid fees, levies, margin)
    vat_percent: float = 19.0  # dynamic: VAT applied to the (net) exchange price
    feed_in_ct: float = 8.0  # feed-in compensation per kWh
    area: str = "DE"  # dynamic: price zone DE (DE-LU) or AT
    base_fee_eur_month: float = 0.0  # fixed monthly fee of the supplier (Grundpreis), gross

    def import_price_ct(self, exchange_eur_mwh: float | None) -> float | None:
        if self.kind == "fixed":
            return self.price_ct
        if exchange_eur_mwh is None:
            return None
        return exchange_eur_mwh / 10 * (1 + self.vat_percent / 100) + self.surcharge_ct


def validate(raw: list) -> list[Tariff]:
    """Cleans tariffs coming from the web app; raises ValueError with a German message."""
    if not isinstance(raw, list) or not 1 <= len(raw) <= 50:
        raise ValueError("Bitte mindestens einen Tarif angeben.")
    tariffs = []
    for item in raw:
        try:
            date.fromisoformat(str(item["valid_from"]))
            tariff = Tariff(valid_from=str(item["valid_from"]), kind=str(item.get("kind", "fixed")),
                            price_ct=float(item.get("price_ct", 0)), surcharge_ct=float(item.get("surcharge_ct", 0)),
                            vat_percent=float(item.get("vat_percent", 19)), feed_in_ct=float(item.get("feed_in_ct", 0)),
                            area=str(item.get("area", "DE")),
                            base_fee_eur_month=float(item.get("base_fee_eur_month") or 0))
        except (KeyError, TypeError, ValueError):
            raise ValueError("Ungültiger Tarif: bitte Datum und Preise prüfen.") from None
        if tariff.kind not in ("fixed", "dynamic") or tariff.area not in PRICE_SOURCES:
            raise ValueError("Ungültige Tarifart.")
        if not all(-100 <= v <= 200 for v in (tariff.price_ct, tariff.surcharge_ct, tariff.feed_in_ct)):
            raise ValueError("Preise bitte zwischen -100 und 200 ct/kWh angeben.")
        if not 0 <= tariff.base_fee_eur_month <= 200:
            raise ValueError("Den Grundpreis bitte zwischen 0 und 200 € pro Monat angeben.")
        tariffs.append(tariff)
    tariffs.sort(key=lambda t: t.valid_from)
    if len({t.valid_from for t in tariffs}) != len(tariffs):
        raise ValueError("Zwei Tarife beginnen am selben Tag.")
    return tariffs


class Tariffs:
    def __init__(self, storage: Storage, fallback) -> None:
        self.storage = storage
        self.fallback = fallback  # () -> (price_ct, feed_in_ct) from the settings
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
        return current

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
            price = tariff.import_price_ct(prices.get(int(row["ts"]) // QUARTER * QUARTER))
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
        prices = [p for p in self.storage.prices(start - QUARTER, end).values() if p is not None]
        exchange = sum(prices) / len(prices) if prices else None
        grid = tariff.import_price_ct(exchange)
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
