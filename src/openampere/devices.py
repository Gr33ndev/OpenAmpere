# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""All extra devices in one place: heating rods and switched loads (OpenAmpere) and charge points (evcc).

Gives the app one list with live power, records each device's power and energy for the analysis, and
remembers names so the history keeps its labels after a device was renamed or removed.
"""

from __future__ import annotations

import time
from collections import defaultdict
from datetime import datetime

from .consumers import State, SurplusControl
from .evcc import Evcc
from .periods import bucket_start
from .runtime import Runtime

class Devices:
    def __init__(self, runtime: Runtime, surplus: SurplusControl, evcc: Evcc) -> None:
        self.runtime, self.surplus, self.evcc = runtime, surplus, evcc

    # ---- live --------------------------------------------------------------------

    def live(self) -> list[dict]:
        devices = []
        for c in self.surplus.consumers:
            state = self.surplus.states.get(c.id, State())
            if c.adjustable:
                power = state.actual_w if state.actual_w is not None else state.power_w
            else:
                power = c.power_w if state.on else 0
            devices.append({
                "key": f"c:{c.id}", "id": c.id, "source": "openampere", "name": c.name,
                "kind": "heating_rod" if c.adjustable else "switch", "enabled": c.enabled,
                "power_w": float(power or 0), "active": bool(power), "on": state.on,
                "temperature_c": state.temperature_c, "target_c": state.target_c, "status": state.status,
                "error": state.error, "override": self.surplus.override(c.id), "connected": None,
                "soc": None, "range_km": None,
            })
        for lp in (self.evcc.fresh() or {}).get("loadpoints", []):
            devices.append({
                "key": f"evcc:{lp['id']}", "id": lp["id"], "source": "evcc", "name": lp["title"],
                "kind": "heat_pump" if lp["heating"] else "wallbox", "enabled": True,
                "power_w": lp["power_w"], "active": lp["charging"], "on": lp["charging"],
                "temperature_c": lp["soc"] if lp["heating"] else None, "target_c": lp["limit_soc"] if lp["heating"] else None,
                "status": None, "error": None, "override": None, "connected": lp.get("connected"),
                # car battery and range, only while plugged in (evcc keeps old values otherwise)
                "soc": lp["soc"] if not lp["heating"] and lp.get("connected") else None,
                "range_km": lp.get("range_km") if not lp["heating"] and lp.get("connected") else None,
            })
        return devices

    async def record_async(self) -> None:
        self.record()

    def record(self, now: float | None = None) -> None:
        """Called in the fast loop: stores the power of every device and keeps the names."""
        now = time.time() if now is None else now
        devices = self.live()
        if not devices:
            return
        names = self.runtime.storage.get_meta("device_names") or {}
        changed = False
        for d in devices:
            entry = {"name": d["name"], "kind": d["kind"]}
            if names.get(d["key"]) != entry:
                names[d["key"]], changed = entry, True
        if changed:
            self.runtime.storage.set_meta("device_names", names)
        self.runtime.storage.add_device_power(now, {d["key"]: d["power_w"] for d in devices})

    # ---- history -------------------------------------------------------------------

    def _labels(self, keys: list[str]) -> list[dict]:
        names = self.runtime.storage.get_meta("device_names") or {}
        return [{"key": k, "name": names.get(k, {}).get("name", k), "kind": names.get(k, {}).get("kind", "switch")}
                for k in keys]

    def energy(self, start: float, end: float, resolution: str) -> dict:
        rows = self.runtime.storage.device_energy(start, end)
        running = self.runtime.storage.running_device_energy()
        if running and start <= running[0] < end:
            rows += [{"ts": running[0], "device": k, "wh": wh} for k, wh in running[1].items()]
        keys = sorted({r["device"] for r in rows}, key=self._order)
        buckets: dict[float, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for r in rows:
            buckets[bucket_start(r["ts"], resolution, self.runtime.tz)][r["device"]] += r["wh"]
        totals = [sum(b.get(k, 0.0) for b in buckets.values()) for k in keys]
        return {"devices": self._labels(keys), "totals_wh": totals,
                "entries": [{"ts": ts, "values": [b.get(k, 0.0) for k in keys]} for ts, b in sorted(buckets.items())]}

    def power(self, start: float, end: float, step: int = 300) -> dict:
        rows = self.runtime.storage.device_power(start, end)
        keys = sorted({r["device"] for r in rows}, key=self._order)
        sums: dict[float, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
        for r in rows:
            sums[r["ts"] // step * step][r["device"]].append(r["w"])
        entries = [{"ts": ts, "values": [sum(v[k]) / len(v[k]) if v.get(k) else None for k in keys]}
                   for ts, v in sorted(sums.items())]
        return {"devices": self._labels(keys), "step": step, "entries": entries}

    def _order(self, key: str) -> tuple:
        """Wallboxes first, then OpenAmpere's devices in their configured order."""
        if key.startswith("evcc:"):
            return (0, key)
        ids = [c.id for c in self.surplus.consumers]
        cid = key.removeprefix("c:")
        return (1, ids.index(cid) if cid in ids else 99, key)

    def today_wh(self) -> dict[str, float]:
        tz = self.runtime.tz
        start = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        result = self.energy(start, start + 86400 + 3600, "day")
        return {d["key"]: total for d, total in zip(result["devices"], result["totals_wh"])}

