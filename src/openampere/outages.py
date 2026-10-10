# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Power cuts: how often the inverter ran the house as an island, for how long, and how far battery and sun carried it.

The collector passes every reading. While the inverter reports off-grid mode, the running outage is kept in the
meta table (so it survives a restart); when the grid is back it is closed and added to the history. Energy figures
come from the inverter's lifetime counters at the start and at the end.

A single reading is not enough (#89): the off-grid flag can be wrong for one reading, e.g. while the connection
breaks down. An outage starts with the second off-grid reading in a row and counts from the first one. A reading
that still shows power flowing to or from the grid is not off-grid, whatever the flag says.
"""

from __future__ import annotations

from .drivers.base import Snapshot
from .storage import Storage

HISTORY = 200
GAP_S = 300  # no reading for this long during an outage: the inverter went dark, or the connection was lost
GRID_FLOW_W = 100  # the meter still sees this much flowing to or from the grid: the grid is there
EMPTY_SOC = 15  # a gap only means "battery empty" if the charge was this low before it


def _counters(snap: Snapshot) -> dict:
    t = snap.totals
    return {"pv": t.pv, "load": t.load, "battery_charge": t.battery_charge, "battery_discharge": t.battery_discharge}


def _kwh(start: dict, end: dict, key: str) -> float | None:
    a, b = start.get(key), end.get(key)
    return round((b - a) / 1000, 2) if a is not None and b is not None and b >= a else None


def off_grid(snap: Snapshot) -> bool | None:
    """The off-grid flag of a reading, unless the meter shows that the grid is there."""
    if snap.off_grid and snap.grid_power is not None and abs(snap.grid_power) > GRID_FLOW_W:
        return False
    return snap.off_grid


def gap_reason(current: dict) -> str:
    """Why there were no readings for a while during an outage:
    - "inverter_off": OpenAmpere kept running and asked in vain, so it had power and only the inverter was gone
      (switched off, or the connection was lost) (#93)
    - "battery_empty": OpenAmpere was not running either and the charge was low before
    - "no_data": no readings, but the battery was charged
    """
    if current.get("polled_in_gap"):
        return "inverter_off"
    soc = current.get("soc_last")
    return "battery_empty" if soc is None or soc <= EMPTY_SOC else "no_data"


def _single_reading(outage: dict) -> bool:
    """Recorded before #89 from one off-grid reading followed by a gap, with a charged battery: not a power cut."""
    return outage.get("dark_since") == outage.get("start") and (outage.get("soc_min") or 0) > EMPTY_SOC


class Outages:
    def __init__(self, storage: Storage) -> None:
        self.storage = storage
        self._current: dict | None = storage.get_meta("outage_current")  # running outage, also after a restart
        self._candidate: dict | None = None  # first off-grid reading, until a second one confirms it
        self._saved_at = 0.0
        history = storage.get_meta("outages") or []
        cleaned = [o for o in history if not _single_reading(o)]
        if len(cleaned) != len(history):
            storage.set_meta("outages", cleaned)

    @property
    def current(self) -> dict | None:
        return self._current

    def history(self) -> list[dict]:
        return self.storage.get_meta("outages") or []

    def observe(self, snap: Snapshot) -> dict | None:
        """Feed one reading; returns the outage that just ended, if any."""
        flag = off_grid(snap)
        if flag is None:
            return None
        current = self.current
        if flag:
            soc = snap.battery_soc
            if current is None:
                candidate = self._candidate
                if candidate is None or snap.timestamp - candidate["last"] > GAP_S:
                    self._candidate = {"start": snap.timestamp, "last": snap.timestamp, "soc_start": soc,
                                       "soc_min": soc, "soc_last": soc, "counters_start": _counters(snap),
                                       "dark_since": None, "gap_reason": None}
                    return None
                current, self._candidate = candidate, None  # second reading in a row: it is a power cut
                self._saved_at = 0.0
            if snap.timestamp - current["last"] > GAP_S and current.get("dark_since") is None:
                current["dark_since"] = current["last"]  # no readings in between
                current["gap_reason"] = gap_reason(current)
            current["last"], current["soc_last"], current["polled_in_gap"] = snap.timestamp, soc, False
            if soc is not None and (current["soc_min"] is None or soc < current["soc_min"]):
                current["soc_min"] = soc
            current["counters_last"] = _counters(snap)
            self._current = current
            # saving every reading would be a lot of writes: when it is confirmed and then once a minute
            if snap.timestamp - self._saved_at > 60:
                self.storage.set_meta("outage_current", current)
                self._saved_at = snap.timestamp
            return None
        self._candidate = None  # a single off-grid reading: not a power cut
        if current is None:
            return None
        start_c, end_c = current["counters_start"], _counters(snap)
        load = _kwh(start_c, end_c, "load")
        discharged, charged = _kwh(start_c, end_c, "battery_discharge"), _kwh(start_c, end_c, "battery_charge")
        battery = round(discharged - charged, 2) if discharged is not None and charged is not None else None
        dark, reason = current.get("dark_since"), current.get("gap_reason")
        if dark is None and snap.timestamp - current["last"] > GAP_S:
            dark, reason = current["last"], gap_reason(current)
        outage = {
            "start": current["start"], "end": snap.timestamp, "duration_s": round(snap.timestamp - current["start"]),
            "soc_start": current["soc_start"], "soc_end": snap.battery_soc, "soc_min": current["soc_min"],
            "load_kwh": load, "solar_kwh": _kwh(start_c, end_c, "pv"), "battery_kwh": battery,
            # no readings for a while: "battery_empty" if the charge was low before, otherwise only data missing
            "dark_since": dark, "gap_reason": reason if dark else None,
        }
        self.storage.set_meta("outages", (self.history() + [outage])[-HISTORY:])
        self.storage.set_meta("outage_current", None)
        self._current = None
        return outage

    def unreachable(self, now: float) -> None:
        """The collector could not read the inverter, so OpenAmpere itself is running (#93)."""
        current = self.current
        if current is not None and not current.get("polled_in_gap") and now - current["last"] > GAP_S:
            current["polled_in_gap"] = True
            self.storage.set_meta("outage_current", current)

    def remove(self, start: float) -> bool:
        """Someone says it was not a power cut, e.g. the inverter was switched off on purpose (#95)."""
        history = self.history()
        kept = [o for o in history if o["start"] != start]
        if len(kept) == len(history):
            return False
        self.storage.set_meta("outages", kept)
        return True

    def view(self) -> dict:
        history = self.history()
        return {"current": self.current, "outages": list(reversed(history)),
                "count": len(history), "total_s": sum(o["duration_s"] for o in history)}
