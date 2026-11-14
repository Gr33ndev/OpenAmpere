# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Energy counters of OpenAmpere's own for a device that has none (#636).

Some devices report their powers but no energy counters, e.g. a Solarmax MAX.STORAGE without "Connectivity+". The
energy history and the day values are built from counter differences, so without counters they would stay at 0. Here
the powers are integrated (left Riemann sum, not across gaps) into lifetime counters in Wh. They are kept in the
database's meta table, so they go on after a restart and never go backwards."""

from __future__ import annotations

from dataclasses import asdict

from .drivers.base import EnergyCounters, Snapshot, energy_balance
from .storage import MAX_INTEGRATION_GAP_S

FLOWS = ("pv", "grid_import", "grid_export", "battery_charge", "battery_discharge")


def flows(snap: Snapshot) -> dict[str, float] | None:
    """The power of each energy flow in W (never negative); None while a value of the balance is missing."""
    pv, grid, battery = snap.pv_power, snap.grid_power, snap.battery_power
    if pv is None or grid is None or battery is None:
        return None
    # snapshot signs: grid + = import, battery + = discharging
    return {"pv": max(0.0, pv), "grid_import": max(0.0, grid), "grid_export": max(0.0, -grid),
            "battery_charge": max(0.0, -battery), "battery_discharge": max(0.0, battery)}


def has_counters(counters: EnergyCounters) -> bool:
    return any(getattr(counters, name) is not None for name in FLOWS)


class PowerCounters:
    """Lifetime counters integrated from the powers of one device, kept under `key` in the meta table."""

    def __init__(self, storage, key: str) -> None:
        self.storage, self.key = storage, key
        state = storage.get_meta(key) if storage is not None else None
        valid = isinstance(state, dict) and isinstance(state.get("sum"), dict)
        self.state: dict = state if valid else {"sum": {}, "ts": None, "w": None}

    def add(self, snap: Snapshot) -> None:
        """Counts the energy since the previous reading into the totals of `snap` (only while it has no counters of
        its own) and works out the consumption from them like for real counters."""
        if has_counters(snap.totals):
            return
        state, now = self.state, flows(snap)
        last_ts, last_w = state.get("ts"), state.get("w")
        if last_ts is not None and isinstance(last_w, dict):
            dt = snap.timestamp - last_ts
            if 0 < dt <= MAX_INTEGRATION_GAP_S:  # not across a gap: a guess would be wrong
                for name in FLOWS:
                    state["sum"][name] = state["sum"].get(name, 0.0) + last_w.get(name, 0.0) * dt / 3600
        state["ts"], state["w"] = snap.timestamp, now
        sums = state["sum"]
        snap.totals = EnergyCounters(**{name: round(sums.get(name, 0.0), 3) for name in FLOWS})
        balance = energy_balance(asdict(snap.totals))
        snap.totals.consumption = None if balance is None else max(0.0, balance)
        if self.storage is not None:
            self.storage.keep_with_next_snapshot(self.key, state)  # with the reading: one write per poll, not two
