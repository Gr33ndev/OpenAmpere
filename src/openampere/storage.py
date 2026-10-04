"""SQLite time-series storage.

- samples:    raw readings every poll (kept raw_retention_days)
- energy_15m: energy per quarter hour in Wh, from inverter counter deltas (kept forever)
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

from .drivers.base import Snapshot

log = logging.getLogger(__name__)

FLOWS = ("pv", "load", "grid_import", "grid_export", "battery_charge", "battery_discharge")
QUARTER = 900
MAX_PV_INPUTS = 4
# columns added after the first release; created on start-up if missing
SAMPLE_EXTRA_COLUMNS = [f"pv{i}" for i in range(1, MAX_PV_INPUTS + 1)] + ["t_inverter", "t_battery", "t_cell_max",
                                                                          "t_cell_min"]
MAX_INTEGRATION_GAP_S = 120  # do not integrate power across longer outages
MAX_POWER_W = 60_000  # upper bound for any energy flow of a home system; larger counter jumps are garbage
EARLIEST_PLAUSIBLE_TS = 1_735_689_600  # 2025-01-01: anything earlier is an unset clock
MAX_GAP_S = 48 * 3600  # outages up to this length are filled by spreading the counter difference

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS samples (
    ts REAL PRIMARY KEY, pv REAL, house REAL, grid REAL, battery REAL, soc REAL
);
CREATE TABLE IF NOT EXISTS energy_15m (
    ts INTEGER PRIMARY KEY,
    {", ".join(f"{f} REAL" for f in FLOWS)},
    soc REAL,
    source TEXT NOT NULL DEFAULT 'local'
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pv_input_15m (
    ts INTEGER NOT NULL, input INTEGER NOT NULL, wh REAL NOT NULL, PRIMARY KEY (ts, input)
);
CREATE TABLE IF NOT EXISTS prices (ts INTEGER PRIMARY KEY, eur_mwh REAL NOT NULL);
CREATE TABLE IF NOT EXISTS device_power (
    ts REAL NOT NULL, device TEXT NOT NULL, w REAL NOT NULL, PRIMARY KEY (ts, device)
);
CREATE TABLE IF NOT EXISTS device_energy_15m (
    ts INTEGER NOT NULL, device TEXT NOT NULL, wh REAL NOT NULL, PRIMARY KEY (ts, device)
);
CREATE TABLE IF NOT EXISTS grid_meter_daily (
    meter TEXT NOT NULL, kind TEXT NOT NULL, day TEXT NOT NULL, kwh REAL NOT NULL, PRIMARY KEY (meter, kind, day)
);
CREATE TABLE IF NOT EXISTS control_log (
    ts REAL NOT NULL, action TEXT NOT NULL, details TEXT NOT NULL, dry_run INTEGER NOT NULL, result TEXT NOT NULL
);
"""


class DatabaseInUse(RuntimeError):
    pass


def _lock_exclusively(path: Path) -> int | None:
    """Prevents two OpenAmpere instances from writing the same database (that corrupts it)."""
    try:
        import fcntl
    except ImportError:  # Windows: no advisory locks, rely on the user
        return None
    fd = os.open(str(path) + ".lock", os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise DatabaseInUse(f"database {path} is already used by another OpenAmpere instance") from None
    return fd


class Storage:
    def __init__(self, path: str | Path) -> None:
        path = Path(path)
        self.path = str(path)
        self._lock_fd = None
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
            self._lock_fd = _lock_exclusively(path)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        existing = {row["name"] for row in self._db.execute("PRAGMA table_info(samples)")}
        for column in SAMPLE_EXTRA_COLUMNS:
            if column not in existing:
                self._db.execute(f"ALTER TABLE samples ADD COLUMN {column} REAL")
        # One connection shared by the event loop, worker threads and the web server's thread pool.
        # SQLite connections must not be used by several threads at the same time (crashes with a
        # segmentation fault on some builds), so EVERY access - reads included - holds this lock.
        self._lock = threading.RLock()
        # per-input energy of the running quarter; persisted so a restart does not lose it
        self._pv_acc: dict | None = self._get_meta("pv_input_state")
        self._quarter: dict | None = self._get_meta("quarter_state")
        row = self._db.execute("SELECT MAX(ts) FROM samples").fetchone()
        self._last_sample_ts: float = row[0] or 0.0
        self._clock_warned = False

    def close(self) -> None:
        with self._lock:
            self._db.close()
        if self._lock_fd is not None:
            os.close(self._lock_fd)  # releases the lock
            self._lock_fd = None

    # ---- meta ------------------------------------------------------------

    def _fetchall(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, params).fetchall()]

    def _get_meta(self, key: str):
        with self._lock:
            row = self._db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else None

    def get_meta(self, key: str):
        return self._get_meta(key)

    def set_meta(self, key: str, value) -> None:
        with self._lock, self._db:
            self._set_meta(key, value)

    def _set_meta(self, key: str, value) -> None:
        self._db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", (key, json.dumps(value)))

    # ---- settings & audit log -------------------------------------------

    def get_settings(self) -> dict:
        return self._get_meta("settings") or {}

    def save_settings(self, values: dict) -> None:
        """Overwrites the old settings on disk too (also in the write-ahead log), so replaced secrets do not linger."""
        with self._lock:
            self._db.execute("PRAGMA secure_delete=ON")
            try:
                with self._db:
                    self._set_meta("settings", values)
            finally:
                self._db.execute("PRAGMA secure_delete=OFF")
            self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def log_control(self, action: str, details: dict, dry_run: bool, result: str) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT INTO control_log VALUES (?,?,?,?,?)",
                             (time.time(), action, json.dumps(details), int(dry_run), result))

    def control_log(self, limit: int = 50) -> list[dict]:
        rows = self._fetchall("SELECT * FROM control_log ORDER BY ts DESC LIMIT ?", (limit,))
        return [{**r, "details": json.loads(r["details"]), "dry_run": bool(r["dry_run"])} for r in rows]

    def backup(self, target: str | Path, *, drop_settings: set[str] | frozenset = frozenset()) -> None:
        """Consistent copy of the database while it is in use. Sessions and the given secret settings
        (e.g. API keys) are removed from the copy."""
        with self._lock:
            dest = sqlite3.connect(str(target))
            with dest:
                self._db.backup(dest)
            with dest:
                row = dest.execute("SELECT value FROM meta WHERE key='settings'").fetchone()
                if row:
                    settings = {k: v for k, v in json.loads(row[0]).items() if k not in drop_settings}
                    dest.execute("UPDATE meta SET value=? WHERE key='settings'", (json.dumps(settings),))
                dest.execute("DELETE FROM meta WHERE key='sessions'")
            dest.execute("VACUUM")  # really remove the deleted data from the file
            dest.close()

    # ---- writing ---------------------------------------------------------

    @property
    def clock_wrong(self) -> bool:
        return self._clock_warned

    def clock_plausible(self, ts: float) -> bool:
        """A Raspberry Pi without a real-time clock starts in 1970 or with the time of its last shutdown
        until NTP has synced. Readings with such a time would land on wrong days."""
        ok = ts >= EARLIEST_PLAUSIBLE_TS and ts >= self._last_sample_ts - 120
        if not ok and not self._clock_warned:
            log.warning("system clock looks wrong (%s) - not storing readings until it is set", time.ctime(ts))
        self._clock_warned = not ok
        return ok

    def add_snapshot(self, snap: Snapshot) -> None:
        if not self.clock_plausible(snap.timestamp):
            return
        self._last_sample_ts = max(self._last_sample_ts, snap.timestamp)
        inputs = [i.power for i in snap.pv_inputs[:MAX_PV_INPUTS]]
        inputs += [None] * (MAX_PV_INPUTS - len(inputs))
        temps = snap.temperatures
        with self._lock, self._db:
            self._db.execute(
                f"INSERT OR REPLACE INTO samples(ts, pv, house, grid, battery, soc, {', '.join(SAMPLE_EXTRA_COLUMNS)}) "
                f"VALUES ({', '.join('?' * (6 + len(SAMPLE_EXTRA_COLUMNS)))})",
                (snap.timestamp, snap.pv_power, snap.house_power, snap.grid_power, snap.battery_power,
                 snap.battery_soc, *inputs, temps.get("inverter"), temps.get("battery"),
                 temps.get("battery_cell_max"), temps.get("battery_cell_min")))
            self._accumulate(snap)
            self._accumulate_pv_inputs(snap.timestamp, [i or 0.0 for i in inputs[:len(snap.pv_inputs)]])

    def _accumulate_pv_inputs(self, ts: float, powers: list[float]) -> None:
        """Integrates the power of each PV input (left Riemann sum) into quarter-hour energies."""
        quarter = int(ts // QUARTER * QUARTER)
        acc = self._pv_acc
        if acc is not None:
            dt = ts - acc["last_ts"]
            if 0 < dt <= MAX_INTEGRATION_GAP_S:
                for i, power in enumerate(acc["last_powers"]):
                    acc["wh"][i] += max(0.0, power) * dt / 3600
            if quarter != acc["ts"]:
                self._db.executemany("INSERT OR REPLACE INTO pv_input_15m VALUES (?,?,?)",
                                     [(acc["ts"], i + 1, wh) for i, wh in enumerate(acc["wh"])])
                acc = None
        if acc is None or len(acc["wh"]) != len(powers):
            acc = {"ts": quarter, "wh": [0.0] * len(powers)}
        acc.update(last_ts=ts, last_powers=powers)
        self._pv_acc = acc
        self._set_meta("pv_input_state", acc)

    def _accumulate(self, snap: Snapshot) -> None:
        """Turn monotonic counters into per-quarter energy rows.

        The counters at the first reading of a quarter are the base; at the first reading of a later
        quarter the difference is stored. Guards against glitches:
        - a counter that drops to 0 (device booting, proxy cache) is ignored, the base is kept
        - a counter that goes backwards (counter reset) only re-bases
        - an implausibly large difference (garbage reading) only re-bases
        - after an outage the difference is spread evenly over the missing quarters (up to MAX_GAP_S)
        """
        totals = {f: getattr(snap.totals, f) for f in FLOWS}
        if any(v is None for v in totals.values()):
            return
        state = self._quarter
        if state is not None and any(totals[f] == 0 and state["totals"][f] > 100 for f in FLOWS):
            log.warning("ignoring reading with a counter that dropped to 0")
            self._note_glitch(snap.timestamp, "Zähler kurz auf 0")
            return
        quarter = int(snap.timestamp // QUARTER * QUARTER)
        if state is None or quarter < state["ts"]:
            self._quarter = {"ts": quarter, "totals": totals}
        elif quarter > state["ts"]:
            span = quarter - state["ts"]
            delta = {f: totals[f] - state["totals"][f] for f in FLOWS}
            limit_wh = MAX_POWER_W * span / 3600 * 1.5
            if any(v < 0 for v in delta.values()):
                log.warning("energy counter went backwards (reset?) - re-basing")
                self._note_glitch(snap.timestamp, "Zähler rückwärts")
            elif any(v > limit_wh for v in delta.values()):
                log.warning("implausible energy counter jump %s - re-basing", {f: round(v) for f, v in delta.items()})
                self._note_glitch(snap.timestamp, "unplausibler Zählersprung")
            elif span > MAX_GAP_S:
                log.warning("gap of %.1f h is too long to fill - re-basing", span / 3600)
            else:
                quarters = span // QUARTER  # spread the energy evenly over the quarters without readings
                rows = [(state["ts"] + i * QUARTER, *(delta[f] / quarters for f in FLOWS),
                         snap.battery_soc if i == quarters - 1 else None) for i in range(quarters)]
                self._db.executemany(
                    f"INSERT OR REPLACE INTO energy_15m(ts, {', '.join(FLOWS)}, soc, source) "
                    f"VALUES (?, {', '.join('?' * len(FLOWS))}, ?, 'local')", rows)
            self._quarter = {"ts": quarter, "totals": totals}
        else:
            return
        self._set_meta("quarter_state", self._quarter)

    def running_quarter(self, snap: Snapshot | None) -> dict | None:
        """Energy of the quarter hour that is still running (not yet stored), from the latest counters."""
        with self._lock:
            state = self._quarter
        if snap is None or state is None:
            return None
        totals = {f: getattr(snap.totals, f) for f in FLOWS}
        if any(v is None for v in totals.values()) or snap.timestamp - state["ts"] > 2 * QUARTER:
            return None
        delta = {f: totals[f] - state["totals"][f] for f in FLOWS}
        if any(v < 0 or v > MAX_POWER_W * QUARTER / 3600 * 1.5 for v in delta.values()):
            return None
        return {"ts": state["ts"], **delta, "soc": snap.battery_soc}

    def days_with_energy(self, start: float, end: float) -> int:
        """Number of days with at least one stored quarter hour (also for coarser imported history)."""
        rows = self._fetchall("SELECT COUNT(DISTINCT CAST(ts / 86400 AS INTEGER)) AS n FROM energy_15m WHERE ts >= ? AND ts < ?",
                              (start, end))
        return int(rows[0]["n"] or 0)

    # ---- daily values of the grid operator's meters (#60) ------------------

    def save_meter_days(self, meter: str, kind: str, days: dict[str, float]) -> None:
        with self._lock, self._db:
            self._db.executemany("INSERT OR REPLACE INTO grid_meter_daily(meter, kind, day, kwh) VALUES(?, ?, ?, ?)",
                                 [(meter, kind, day, kwh) for day, kwh in days.items()])

    def replace_meter_days(self, meter: str, kind: str, first: str, days: dict[str, float]) -> None:
        """Days from first on are exactly the given ones: a day missing there is unknown, not kept from before."""
        with self._lock, self._db:
            self._db.execute("DELETE FROM grid_meter_daily WHERE meter=? AND kind=? AND day >= ?", (meter, kind, first))
            self._db.executemany("INSERT OR REPLACE INTO grid_meter_daily(meter, kind, day, kwh) VALUES(?, ?, ?, ?)",
                                 [(meter, kind, day, kwh) for day, kwh in days.items()])

    def meter_last_day(self, meter: str, kind: str) -> str | None:
        rows = self._fetchall("SELECT MAX(day) AS day FROM grid_meter_daily WHERE meter=? AND kind=?", (meter, kind))
        return rows[0]["day"] if rows else None

    def meter_days(self, kind: str, first: str, last: str, meters: list[str]) -> dict[str, float]:
        """kWh per day (ISO dates, first <= day < last), summed over the given meters."""
        if not meters:
            return {}
        marks = ", ".join("?" * len(meters))
        rows = self._fetchall(f"SELECT day, SUM(kwh) AS kwh FROM grid_meter_daily WHERE kind=? AND day >= ? AND day < ? "
                              f"AND meter IN ({marks}) GROUP BY day ORDER BY day", (kind, first, last, *meters))
        return {r["day"]: r["kwh"] for r in rows}

    def energy_days(self, start: float, end: float, tz) -> set[str]:
        """Local days (ISO dates) with at least one stored quarter hour."""
        rows = self._fetchall("SELECT DISTINCT CAST(ts / 900 AS INTEGER) * 900 AS ts FROM energy_15m WHERE ts >= ? AND ts < ?",
                              (start, end))
        return {datetime.fromtimestamp(r["ts"], tz).date().isoformat() for r in rows}

    def first_sample_ts(self) -> float | None:
        rows = self._fetchall("SELECT MIN(ts) AS ts FROM samples")
        return rows[0]["ts"] if rows else None

    def temperature_extremes(self, start: float, end: float) -> dict:
        """Highest (and for the cells lowest) temperatures and the largest cell spread, with their time."""
        result = {}
        for key, expr, order in (("cell_max", "t_cell_max", "DESC"), ("cell_min", "t_cell_min", "ASC"),
                                 ("spread", "t_cell_max - t_cell_min", "DESC"), ("inverter", "t_inverter", "DESC"),
                                 ("battery", "t_battery", "DESC")):
            rows = self._fetchall(f"SELECT ts, {expr} AS v FROM samples WHERE ts >= ? AND ts < ? AND {expr} IS NOT NULL "
                                  f"ORDER BY v {order} LIMIT 1", (start, end))
            result[key] = {"value": rows[0]["v"], "ts": rows[0]["ts"]} if rows else None
        return result

    def note_firmware(self, serial: str | None, firmware: str | None, now: float | None = None) -> dict | None:
        """Remembers the inverter's firmware. Returns the change if the same device now reports another one."""
        if not firmware:
            return None
        now = time.time() if now is None else now
        last = self.get_meta("firmware") or {}
        if last.get("serial") == serial and last.get("firmware") == firmware:
            return None
        self.set_meta("firmware", {"serial": serial, "firmware": firmware, "since": now})
        if last.get("firmware") and last.get("serial") == serial:
            change = {"ts": now, "old": last["firmware"], "new": firmware}
            self.set_meta("firmware_history", (self.get_meta("firmware_history") or [])[-49:] + [change])
            return change
        return None

    def first_quarter(self, start: float, end: float) -> float | None:
        with self._lock:
            row = self._db.execute("SELECT MIN(ts) FROM energy_15m WHERE ts >= ? AND ts < ?", (start, end)).fetchone()
        return row[0]

    def _note_glitch(self, ts: float, kind: str) -> None:
        """Counter problems for the diagnostics (called with the lock held)."""
        glitches = (self._get_meta("counter_glitches") or [])[-19:]
        glitches.append({"ts": ts, "kind": kind})
        self._set_meta("counter_glitches", glitches)

    def import_energy(self, rows: list[dict], source: str) -> int:
        """Insert quarter-hour rows from an external source; never overwrites local measurements."""
        with self._lock, self._db:
            cur = self._db.executemany(
                f"INSERT OR IGNORE INTO energy_15m(ts, {', '.join(FLOWS)}, soc, source) "
                f"VALUES (:ts, {', '.join(':' + f for f in FLOWS)}, :soc, '{source}')",
                [{**dict.fromkeys(FLOWS), "soc": None, **r} for r in rows])
            return cur.rowcount

    def import_soc(self, values: list[tuple[int, float]]) -> None:
        """Fill in the battery state of charge for quarter hours that have none yet."""
        with self._lock, self._db:
            self._db.executemany("UPDATE energy_15m SET soc = ? WHERE ts = ? AND soc IS NULL",
                                 [(soc, ts) for ts, soc in values])

    def prune(self, retention_days: int) -> None:
        if retention_days <= 0:  # keep forever
            return
        with self._lock, self._db:
            cutoff = time.time() - retention_days * 86400
            self._db.execute("DELETE FROM samples WHERE ts < ?", (cutoff,))
            self._db.execute("DELETE FROM device_power WHERE ts < ?", (cutoff,))

    def usage(self) -> dict:
        """Size of the database and how many detail readings it holds, for the retention setting."""
        rows = self._fetchall("SELECT COUNT(*) AS n, MIN(ts) AS first FROM samples")[0]
        try:
            size = os.path.getsize(self.path) + sum(os.path.getsize(f"{self.path}{s}") for s in ("-wal", "-shm")
                                                    if os.path.exists(f"{self.path}{s}"))
            free = shutil.disk_usage(os.path.dirname(os.path.abspath(self.path))).free
        except OSError:
            size, free = None, None
        return {"db_bytes": size, "free_bytes": free, "samples": rows["n"], "first_sample": rows["first"]}

    # ---- devices (wallbox, heating rod, ...) --------------------------------

    def add_device_power(self, ts: float, powers: dict[str, float]) -> None:
        """Power of each device (W) at one moment; integrated into quarter-hour energies like the PV inputs."""
        if not powers or not self.clock_plausible(ts):
            return
        quarter = int(ts // QUARTER * QUARTER)
        with self._lock, self._db:
            self._db.executemany("INSERT OR REPLACE INTO device_power(ts, device, w) VALUES (?, ?, ?)",
                                 [(ts, key, max(0.0, w)) for key, w in powers.items()])
            acc = self._get_meta("device_energy_state")
            if acc is not None:
                dt = ts - acc["last_ts"]
                if 0 < dt <= MAX_INTEGRATION_GAP_S:
                    for key, w in acc["last"].items():
                        acc["wh"][key] = acc["wh"].get(key, 0.0) + max(0.0, w) * dt / 3600
                if quarter != acc["ts"]:
                    self._db.executemany(
                        "INSERT INTO device_energy_15m(ts, device, wh) VALUES (?, ?, ?) "
                        "ON CONFLICT(ts, device) DO UPDATE SET wh = wh + excluded.wh",
                        [(acc["ts"], key, wh) for key, wh in acc["wh"].items()])
                    acc = None
            if acc is None:
                acc = {"ts": quarter, "wh": {}}
            acc.update(last_ts=ts, last=powers)
            self._set_meta("device_energy_state", acc)

    def running_device_energy(self) -> tuple[int, dict[str, float]] | None:
        """Energy per device of the quarter hour that is still running."""
        acc = self._get_meta("device_energy_state")
        return (acc["ts"], acc["wh"]) if acc else None

    def device_energy(self, start: float, end: float) -> list[dict]:
        return self._fetchall("SELECT ts, device, wh FROM device_energy_15m WHERE ts >= ? AND ts < ? ORDER BY ts",
                              (start, end))

    def device_power(self, start: float, end: float) -> list[dict]:
        return self._fetchall("SELECT ts, device, w FROM device_power WHERE ts >= ? AND ts < ? ORDER BY ts",
                              (start, end))

    # ---- reading ---------------------------------------------------------

    def samples(self, start: float, end: float) -> list[dict]:
        return self._fetchall("SELECT * FROM samples WHERE ts >= ? AND ts < ? ORDER BY ts", (start, end))

    def energy(self, start: float, end: float) -> list[dict]:
        return self._fetchall("SELECT * FROM energy_15m WHERE ts >= ? AND ts < ? ORDER BY ts", (start, end))

    def pv_input_energy(self, start: float, end: float) -> list[dict]:
        return self._fetchall("SELECT ts, input, wh FROM pv_input_15m WHERE ts >= ? AND ts < ? ORDER BY ts, input",
                              (start, end))

    def save_prices(self, rows: list[tuple[int, float]]) -> None:
        with self._lock, self._db:
            self._db.executemany("INSERT OR REPLACE INTO prices(ts, eur_mwh) VALUES (?, ?)", rows)

    def prices(self, start: float, end: float) -> dict[int, float]:
        """Exchange price (EUR/MWh) per quarter hour."""
        return {r["ts"]: r["eur_mwh"] for r in
                self._fetchall("SELECT ts, eur_mwh FROM prices WHERE ts >= ? AND ts < ? ORDER BY ts", (start, end))}

    def energy_sum(self, start: float, end: float) -> dict:
        with self._lock:
            row = self._db.execute(
                f"SELECT {', '.join(f'SUM({f}) AS {f}' for f in FLOWS)}, COUNT(*) AS quarters "
                "FROM energy_15m WHERE ts >= ? AND ts < ?", (start, end)).fetchone()
        return dict(row)
