"""Health of the home battery: full cycles, round-trip efficiency and cell temperatures."""

from __future__ import annotations

import time

from .drivers.base import Snapshot
from .notify import battery_problem
from .storage import Storage

WINDOW_DAYS = 30  # raw readings (with the temperatures) are kept about this long by default
MIN_EFFICIENCY_KWH = 50  # the counters need some throughput before the ratio means anything


def battery(storage: Storage, snap: Snapshot | None, capacity_kwh: float, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    totals = snap.totals if snap else None
    charged = totals.battery_charge if totals else None
    discharged = totals.battery_discharge if totals else None
    temps = snap.temperatures if snap else {}
    spread_now = None
    if temps.get("battery_cell_max") is not None and temps.get("battery_cell_min") is not None:
        spread_now = temps["battery_cell_max"] - temps["battery_cell_min"]
    return {
        "capacity_kwh": capacity_kwh or None,
        "charged_kwh": charged / 1000 if charged is not None else None,
        "discharged_kwh": discharged / 1000 if discharged is not None else None,
        # one full cycle = the usable capacity once discharged
        "cycles": discharged / 1000 / capacity_kwh if discharged and capacity_kwh else None,
        "efficiency_pct": discharged / charged * 100 if charged and discharged and charged / 1000 >= MIN_EFFICIENCY_KWH else None,
        "soh_pct": snap.battery_soh if snap else None,
        "cell_max_now_c": temps.get("battery_cell_max"), "cell_min_now_c": temps.get("battery_cell_min"),
        "spread_now_c": spread_now,
        "days": WINDOW_DAYS,
        "extremes": storage.temperature_extremes(now - WINDOW_DAYS * 86400, now),
        "warning": battery_problem(temps),
    }
