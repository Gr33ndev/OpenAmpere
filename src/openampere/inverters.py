# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Further inverters of the same installation (#619): each read on its own, combined into one reading.

The first inverter (config.inverter) stays what it was: its reading is stored, grid charging and the export limit
control it. Each further inverter is polled by an ExtraCollector with the same connection handling (identity by serial
number, temporary errors, backoff). Before the first inverter's reading is stored, `combine` adds the further ones:

- PV and battery power are added up. Their energy counters are not: each further inverter's counters go into
  lifetime counters of OpenAmpere's own (ExtraCounters) by their differences, so a further inverter set up later
  does not bring its whole lifetime into one quarter hour, and a counter that drops to 0 for a moment (a restart) or
  goes backwards is no energy;
- the grid is the first inverter's reading: all of them usually measure the same house connection. A further inverter
  marked `own_grid` measures a grid connection of its own, its grid power and counters are added;
- the house is the balance grid + PV + battery, not what the inverters report: each of them sees the other batteries
  as part of the house;
- the state of charge is weighted by the capacity of each battery when all are known, otherwise the average;
- a further inverter marked `in_first` is part of a parallel system whose first inverter already reports the values
  of all: it is shown on its own, but nothing of it is added (it would count twice). Its PV inputs keep their places.

A further inverter without a recent reading makes the combined values it takes part in missing (None): a sum without
it would be too low, and a wrong value is worse than a missing one. That holds for a short while (GONE_AFTER_S, the
storage spreads such a gap). An inverter that stays away longer (switched off, a new address, no Wi-Fi) is left out
of the sums from then on, so the others are still shown and recorded; the app and a notification tell the owner. Its
counters then count again from its next reading: what it produced while it was away is not known.
"""

from __future__ import annotations

import copy
import logging
import time

from .collector import KNOWN_DEVICE, Collector
from .config import MAIN_INVERTER, ExtraInverter
from .drivers.base import EnergyCounters, InverterDriver, PvInput, Snapshot, energy_balance

log = logging.getLogger(__name__)

FRESH_S = 30  # a further inverter's reading counts for the combined values up to this age (at least 3 poll intervals)
SUMMED = ("pv", "load", "battery_charge", "battery_discharge")
GRID = ("grid_import", "grid_export")
COUNTERS_META = "inverter_counters"
MAX_POWER_W = 60_000  # like storage: a larger counter step of a home system is garbage
GONE_AFTER_S = 300  # a further inverter without a reading for this long is left out of the combined values


class ExtraCounters:
    """Lifetime counters (Wh) of OpenAmpere's own for the further inverters (#619), built from the differences of
    their counters: what each one counted since it was set up. They only grow, also after an inverter was removed
    (its part stays), so the combined counters never go backwards. Kept in the database's meta table."""

    def __init__(self, storage=None) -> None:
        self.storage = storage
        state = storage.get_meta(COUNTERS_META) if storage is not None else None
        self.state: dict = state if isinstance(state, dict) else {}

    def add(self, inverter_id: str, counters: EnergyCounters, ts: float, own_grid: bool, host: str = "") -> None:
        """One reading's counters of a further inverter. host: its address; another one at the same id (removed and
        another device added) counts from its first reading, not from the difference to the one before."""
        entry = self.state.setdefault(inverter_id, {"last": {}, "sum": {}, "ts": ts, "host": host})
        if entry.get("host", host) != host:
            entry["last"], entry["host"] = {}, host
        span = max(1.0, ts - entry.get("ts", ts))
        changed = False
        for name in (*SUMMED, *GRID):
            value = getattr(counters, name)
            if value is None or (name in GRID and not own_grid):
                continue
            last = entry["last"].get(name)
            if last is not None and value == 0 and last > 100:
                continue  # the device restarting: the counter is back in a moment
            if last is not None and 0 <= value - last <= MAX_POWER_W * span / 3600 * 1.5:
                entry["sum"][name] = entry["sum"].get(name, 0.0) + value - last
            elif last is not None:
                log.warning("counter %s of inverter %s jumped from %.0f to %.0f Wh - counted from here", name,
                            inverter_id, last, value)
            # first reading (set up now), backwards (a reset) or an implausible jump: counted from this value on
            entry["last"][name] = value
            changed = True
        entry["ts"] = ts
        if changed and self.storage is not None:
            try:
                self.storage.set_meta(COUNTERS_META, self.state)
            except Exception as err:  # noqa: BLE001 - must never disturb polling
                log.warning("could not keep the counters of the further inverters: %s", err)

    def forget(self, counting: set[str]) -> None:
        """Only the inverters in `counting` go on from their last counter values. One that was removed or stayed away
        counts again from its next reading: the difference to a value from long ago would land in one quarter hour,
        for a different device even a whole lifetime. What it counted stays in the sum."""
        stale = [i for i, entry in self.state.items() if i not in counting and entry.get("last")]
        for inverter_id in stale:
            self.state[inverter_id]["last"] = {}
        if stale and self.storage is not None:
            try:
                self.storage.set_meta(COUNTERS_META, self.state)
            except Exception as err:  # noqa: BLE001 - must never disturb polling
                log.warning("could not keep the counters of the further inverters: %s", err)

    def total(self, name: str) -> float:
        """What all further inverters counted, the removed ones included."""
        return sum(entry["sum"].get(name, 0.0) for entry in self.state.values())


class ExtraCollector(Collector):
    """Polls one further inverter. Nothing of its own is stored: the first inverter's collector combines its latest
    reading into the stored one."""

    prunes = False

    def __init__(self, entry: ExtraInverter, driver: InverterDriver | None, storage, interval: float, *,
                 external_pv: bool = False) -> None:
        self.entry = entry
        self.known_key = f"{KNOWN_DEVICE}:{entry.id}"
        self.label = f"inverter {entry.id}"
        super().__init__(driver, storage, interval, 0, release_connection=entry.connection_mode == "per_poll")
        known = storage.get_meta(self.known_key)
        if not isinstance(known, dict) or known.get("host") != entry.host:
            # set up now or given another address by the owner: the device found there is the one meant
            self.known_serial, self._new_host = None, entry.host
        self.external_pv = external_pv
        self.rated_power = (entry.rated_power_reported_w, entry.rated_power_w)
        self.created = time.time()
        # PV inputs it reported at most: its places in the combined list stay its own, also after a restart or a
        # change of the settings (kept with its address: another device there starts again)
        self.inputs_key = f"inverter_inputs:{entry.id}"
        kept = storage.get_meta(self.inputs_key)
        self.input_count = (int(kept.get("count") or 0) if isinstance(kept, dict) and kept.get("host") == entry.host
                            else 0)

    @property
    def name(self) -> str:
        return self.entry.name

    def fresh(self, now: float | None = None) -> bool:
        snap = self.latest
        if snap is None or not self.connected:
            return False
        return (time.time() if now is None else now) - snap.timestamp <= max(3 * self.poll_interval(), FRESH_S)

    def gone(self, now: float | None = None) -> bool:
        """No reading for GONE_AFTER_S: left out of the combined values until it answers again."""
        last = self.latest.timestamp if self.latest is not None else self.created
        return (time.time() if now is None else now) - last > GONE_AFTER_S

    async def _reading(self, snap: Snapshot) -> None:
        self.own = self.latest = snap
        if len(snap.pv_inputs) > self.input_count:
            self.input_count = len(snap.pv_inputs)
            try:
                self.storage.set_meta(self.inputs_key, {"host": self.entry.host, "count": self.input_count})
            except Exception:  # noqa: BLE001 - must never disturb polling
                pass

    def _unreachable(self) -> None:
        pass  # a power cut is told by the first inverter

    def _check_firmware(self) -> None:
        pass  # the firmware history is the first inverter's (an update can change its registers)

    def _event(self, kind: str, detail: str = "") -> None:
        super()._event(kind, f"{self.entry.id}: {detail}" if detail else self.entry.id)


def _add(values) -> float | None:
    """The sum of the values known; None if none is."""
    known = [v for v in values if v is not None]
    return sum(known) if known else None


def _all(values) -> float | None:
    """The sum, or None if one is missing."""
    values = list(values)
    return None if any(v is None for v in values) else sum(values)


def combined_soc(parts: list[tuple[Snapshot, float]]) -> float | None:
    """The state of charge of all batteries: weighted by capacity (kWh) when every battery has one, else the
    average. parts: (reading, capacity) of each inverter with a battery."""
    socs = [(snap.battery_soc, capacity) for snap, capacity in parts if snap.battery_soc is not None]
    if not socs:
        return None
    if all(capacity > 0 for _, capacity in socs):
        return sum(soc * capacity for soc, capacity in socs) / sum(capacity for _, capacity in socs)
    return sum(soc for soc, _ in socs) / len(socs)


def _part(inverter_id: str, name: str, snap: Snapshot | None, left_out: bool = False, in_first: bool = False) -> dict:
    """What the app shows of one inverter. left_out: away for long, not part of the combined values. in_first: its
    values are already in the first inverter's (a parallel system), it is only shown."""
    flags = {**({"left_out": True} if left_out else {}), **({"in_first": True} if in_first else {})}
    if snap is None:
        return {"id": inverter_id, "name": name, "connected": False, **flags}
    return {"id": inverter_id, "name": name, "connected": True, **flags, "pv_power": snap.pv_power,
            "pv_external_power": snap.pv_external_power, "battery_power": snap.battery_power,
            "battery_soc": snap.battery_soc, "grid_power": snap.grid_power, "alarms": list(snap.alarms)}


def _balance(out: EnergyCounters) -> None:
    balance = energy_balance({f: getattr(out, f) for f in ("pv", "grid_import", "battery_discharge", "grid_export",
                                                            "battery_charge")})
    out.consumption = None if balance is None else max(0.0, balance)


def _totals(out: EnergyCounters, readings: list, counters: ExtraCounters) -> None:
    """The lifetime counters: the first inverter's plus what the further ones counted (ExtraCounters). Missing while
    a further inverter has no recent reading, so the storage spreads the gap instead of counting it at the end."""
    for entry, snap in readings:
        if snap is not None:
            counters.add(entry.id, snap.totals, snap.timestamp, entry.own_grid, entry.host)
    missing = any(snap is None for _, snap in readings)
    grid_missing = any(entry.own_grid and snap is None for entry, snap in readings)
    for name in (*SUMMED, *GRID):
        own = getattr(out, name)
        lost = grid_missing if name in GRID else missing
        setattr(out, name, None if own is None or lost else own + counters.total(name))
    _balance(out)


def _today(out: EnergyCounters, readings: list) -> None:
    """Today's counters, only shown: added up (each inverter starts the day at 0)."""
    missing = any(snap is None for _, snap in readings)
    for name in SUMMED:
        values = [getattr(snap.today, name) for _, snap in readings if snap is not None]
        setattr(out, name, None if missing else _add([getattr(out, name), *values]))
    for name in GRID:
        if any(entry.own_grid and snap is None for entry, snap in readings):
            setattr(out, name, None)
        else:
            setattr(out, name, _all([getattr(out, name),
                                     *(getattr(snap.today, name) for entry, snap in readings if entry.own_grid)]))
    _balance(out)


def _battery_power(snaps: list[Snapshot]) -> float | None:
    """The battery power of all: None if one with a battery (it reports a state of charge) has no power in this
    reading, the sum would be too low."""
    if any(s.battery_power is None and s.battery_soc is not None for s in snaps):
        return None
    return _add([s.battery_power for s in snaps])


def _pv_inputs(own: Snapshot, everyone: list) -> list[PvInput]:
    """The PV inputs of all inverters, each inverter at the same places every time (the owner names inputs by place,
    and the history keeps them so): one without a reading gets empty places, as many as it reported before."""
    inputs = list(own.pv_inputs)
    for extra, snap in everyone:
        its = list(snap.pv_inputs) if snap is not None else []
        count = max(getattr(extra, "input_count", 0), len(its))
        inputs += its + [PvInput() for _ in range(count - len(its))]
    return inputs


def combine(own: Snapshot, extras: list[ExtraCollector], main_name: str = "", main_capacity: float = 0.0,
            now: float | None = None, counters: ExtraCounters | None = None) -> Snapshot:
    """The first inverter's reading with the further ones added (see the module docstring). counters: the further
    inverters' lifetime counters, kept by the first inverter's collector."""
    counters = counters if counters is not None else ExtraCounters()
    everyone = [(e, e.latest if e.fresh(now) else None) for e in extras]
    gone = {e.entry.id for e, r in everyone if r is None and e.gone(now)}
    # one whose values the first inverter already reports (a parallel system, `in_first`) is only shown
    readings = [(e, r) for e, r in everyone if e.entry.id not in gone and not e.entry.in_first]
    counters.forget({e.entry.id for e, _ in readings})
    snap = copy.deepcopy(own)
    snap.inverters = [_part(MAIN_INVERTER, main_name, own)] + [
        _part(e.entry.id, e.name, r, e.entry.id in gone and not e.entry.in_first, e.entry.in_first)
        for e, r in everyone]
    missing = any(r is None for _, r in readings)
    present = [(e, r) for e, r in readings if r is not None]
    if missing:
        snap.pv_power = snap.pv_external_power = snap.battery_power = snap.battery_soc = None
    else:
        snap.pv_power = _add([own.pv_power, *(r.pv_power for _, r in present)])
        snap.pv_external_power = _add([own.pv_external_power, *(r.pv_external_power for _, r in present)])
        snap.battery_power = _battery_power([own, *(r for _, r in present)])
        snap.battery_soc = combined_soc([(own, main_capacity), *((r, e.entry.capacity_kwh) for e, r in present)])
    snap.pv_inputs = _pv_inputs(own, everyone)
    if any(e.entry.own_grid and r is None for e, r in readings):
        snap.grid_power = None
    else:
        snap.grid_power = _all([own.grid_power, *(r.grid_power for e, r in present if e.entry.own_grid)])
    # the house from the balance: each inverter's own house value counts the other batteries as consumption
    parts = (snap.grid_power, snap.pv_power, snap.battery_power)
    snap.house_power = None if any(v is None for v in parts) else max(0.0, sum(parts))
    snap.house_with_external = False
    entries = [(e.entry, r) for e, r in readings]
    _totals(snap.totals, entries, counters)  # snap is a copy: its counters can be changed
    _today(snap.today, entries)
    return snap
