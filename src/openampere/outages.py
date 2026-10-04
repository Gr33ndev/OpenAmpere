"""Power cuts: how often the inverter ran the house as an island, for how long, and how far battery and sun carried it.

The collector passes every reading. While the inverter reports off-grid mode, the running outage is kept in the
meta table (so it survives a restart); when the grid is back it is closed and added to the history. Energy figures
come from the inverter's lifetime counters at the start and at the end.
"""

from __future__ import annotations

from .drivers.base import Snapshot
from .storage import Storage

HISTORY = 200
GAP_S = 300  # no reading for this long during an outage: the inverter went dark (battery empty)


def _counters(snap: Snapshot) -> dict:
    t = snap.totals
    return {"pv": t.pv, "load": t.load, "battery_charge": t.battery_charge, "battery_discharge": t.battery_discharge}


def _kwh(start: dict, end: dict, key: str) -> float | None:
    a, b = start.get(key), end.get(key)
    return round((b - a) / 1000, 2) if a is not None and b is not None and b >= a else None


class Outages:
    def __init__(self, storage: Storage) -> None:
        self.storage = storage
        self._current: dict | None = storage.get_meta("outage_current")  # running outage, also after a restart
        self._saved_at = 0.0

    @property
    def current(self) -> dict | None:
        return self._current

    def history(self) -> list[dict]:
        return self.storage.get_meta("outages") or []

    def observe(self, snap: Snapshot) -> dict | None:
        """Feed one reading; returns the outage that just ended, if any."""
        if snap.off_grid is None:
            return None
        current = self.current
        if snap.off_grid:
            soc = snap.battery_soc
            if current is None:
                current = {"start": snap.timestamp, "last": snap.timestamp, "soc_start": soc, "soc_min": soc,
                           "soc_last": soc, "counters_start": _counters(snap), "dark_since": None}
            else:
                if snap.timestamp - current["last"] > GAP_S and current.get("dark_since") is None:
                    current["dark_since"] = current["last"]  # the inverter was off in between
                current["last"], current["soc_last"] = snap.timestamp, soc
                if soc is not None and (current["soc_min"] is None or soc < current["soc_min"]):
                    current["soc_min"] = soc
            current["counters_last"] = _counters(snap)
            self._current = current
            # saving every reading would be a lot of writes: at the start and then once a minute
            if snap.timestamp - self._saved_at > 60 or current["last"] == current["start"]:
                self.storage.set_meta("outage_current", current)
                self._saved_at = snap.timestamp
            return None
        if current is None:
            return None
        start_c, end_c = current["counters_start"], _counters(snap)
        load = _kwh(start_c, end_c, "load")
        discharged, charged = _kwh(start_c, end_c, "battery_discharge"), _kwh(start_c, end_c, "battery_charge")
        battery = round(discharged - charged, 2) if discharged is not None and charged is not None else None
        dark = current.get("dark_since") or (current["last"] if snap.timestamp - current["last"] > GAP_S else None)
        outage = {
            "start": current["start"], "end": snap.timestamp, "duration_s": round(snap.timestamp - current["start"]),
            "soc_start": current["soc_start"], "soc_end": snap.battery_soc, "soc_min": current["soc_min"],
            "load_kwh": load, "solar_kwh": _kwh(start_c, end_c, "pv"), "battery_kwh": battery,
            # supply interrupted: no readings for a while, usually because the battery was empty
            "dark_since": dark,
        }
        self.storage.set_meta("outages", (self.history() + [outage])[-HISTORY:])
        self.storage.set_meta("outage_current", None)
        self._current = None
        return outage

    def view(self) -> dict:
        history = self.history()
        return {"current": self.current, "outages": list(reversed(history)),
                "count": len(history), "total_s": sum(o["duration_s"] for o in history)}
