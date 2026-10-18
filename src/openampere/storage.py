# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""SQLite time-series storage.

- samples:    raw readings every poll (kept raw_retention_days)
- energy_15m: energy per quarter hour in Wh, from inverter counter deltas (kept forever)
"""

from __future__ import annotations

import errno
import glob
import json
import logging
import os
import shutil
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from .drivers.base import Snapshot, energy_balance

log = logging.getLogger(__name__)

FLOWS = ("pv", "load", "grid_import", "grid_export", "battery_charge", "battery_discharge")
# the counters the quarter hours come from; "load" is their balance, not the inverter's load counter (#346)
COUNTERS = tuple(f for f in FLOWS if f != "load")
# a second PV inverter the inverter measures (SAJ Meter B, #419): energy_15m.pv and samples.pv include it, this column
# says how much of it came from there (the report shows it on its own). Not in FLOWS: SCHEMA (version 1) uses FLOWS.
EXTERNAL = "pv_external"
MAX_CARRY_WH = 1000  # deficit of the balance carried into the next quarters, see _consumption
QUARTER = 900
MAX_PV_INPUTS = 4
# columns added after the first release, before schema versions; created on start-up if missing
SAMPLE_EXTRA_COLUMNS = [f"pv{i}" for i in range(1, MAX_PV_INPUTS + 1)] + ["t_inverter", "t_battery", "t_cell_max",
                                                                          "t_cell_min"]
MAX_INTEGRATION_GAP_S = 120  # do not integrate power across longer outages
MAX_POWER_W = 60_000  # upper bound for any energy flow of a home system; larger counter jumps are garbage
EARLIEST_PLAUSIBLE_TS = 1_735_689_600  # 2025-01-01: anything earlier is an unset clock
MAX_GAP_S = 48 * 3600  # outages up to this length are filled by spreading the counter difference
STEP_QUARTERS = 7 * 96  # latest quarters looked at to find the step of the energy counters (#194)
STEP_MIN_DELTAS = 20  # non-zero counter differences needed before the recorded data decides the step
STEP_CACHE_S = 3600

# Version 1 of the database: the tables when schema versions were introduced (#166). Do not change it; a change to
# the database is a new step in MIGRATIONS and SCHEMA_VERSION + 1.
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


def _consumption(flows: dict, carry: float) -> tuple[float, float]:
    """Consumption of a quarter hour from the balance of the other flows (#346) and the deficit to carry on.
    Counters with 0.1 kWh steps do not tick at the same moment, so a single quarter can come out negative: the
    deficit is taken from the next quarters, no quarter is negative and the sum stays the balance."""
    balance = energy_balance(flows) + carry
    return max(0.0, balance), max(-MAX_CARRY_WH, min(0.0, balance))


def _consumption_from_balance(db: sqlite3.Connection) -> None:
    """Version 1 -> 2: the consumption of the quarter hours OpenAmpere recorded came from the inverter's load counter,
    which can count much more than the house used (#346). Imported history (cloud) already is the balance."""
    carry, updates = 0.0, []
    for ts, *values in db.execute(f"SELECT ts, {', '.join(COUNTERS)} FROM energy_15m WHERE source = 'local' "
                                  "ORDER BY ts").fetchall():
        flows = dict(zip(COUNTERS, values))
        if energy_balance(flows) is None:
            continue
        load, carry = _consumption(flows, carry)
        updates.append((load, ts))
    db.executemany("UPDATE energy_15m SET load = ? WHERE ts = ?", updates)


def _external_pv_columns(db: sqlite3.Connection) -> None:
    """Version 2 -> 3: the energy and power of a second PV inverter as their own columns (#419)."""
    for table in ("energy_15m", "samples"):
        if EXTERNAL not in {row[1] for row in db.execute(f"PRAGMA table_info({table})")}:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {EXTERNAL} REAL")


def _allowed_hosts_from_the_app_only(db: sqlite3.Connection) -> None:
    """Version 3 -> 4: the own host names can be set in the app now (#437). Before, the app could not set them, so a
    value in the saved settings can only come from a crafted backup restored by an older version (before it took
    only settings of the app); every start ignored it. It is removed, so it does not take effect now."""
    row = db.execute("SELECT value FROM meta WHERE key='settings'").fetchone()
    try:
        settings = json.loads(row[0]) if row else None
    except ValueError:
        return  # the start reads it the same way and ignores it
    if isinstance(settings, dict) and "server.allowed_hosts" in settings:
        del settings["server.allowed_hosts"]
        db.execute("UPDATE meta SET value=? WHERE key='settings'", (json.dumps(settings),))


def _car_tables(db: sqlite3.Connection) -> None:
    """Version 4 -> 5: the data of a car from its manufacturer (#401) and its charges at a wallbox OpenAmpere
    controls. The manufacturer keeps the data only for a while, so OpenAmpere keeps its own history."""
    db.execute("""CREATE TABLE IF NOT EXISTS car_points (
        car TEXT NOT NULL, ts REAL NOT NULL, soc REAL, range_km REAL, odometer_km REAL, plugged INTEGER,
        charging INTEGER, target_soc REAL, PRIMARY KEY (car, ts))""")
    db.execute("""CREATE TABLE IF NOT EXISTS car_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, car TEXT NOT NULL, wallbox TEXT NOT NULL, start REAL NOT NULL,
        end REAL, kwh REAL NOT NULL DEFAULT 0, counter REAL)""")
    db.execute("CREATE INDEX IF NOT EXISTS car_sessions_car ON car_sessions (car, start)")


# MIGRATIONS[n] brings a database from version n to n + 1 (PRAGMA user_version), each step in one transaction.
# Before the first step the database is copied to <db>.schema-<n>: after a rollback (the updater goes back to the
# previous image when a new version does not start) the older version continues with that copy instead of a
# database it cannot read. Only the copy of the newest upgrade is kept, each is as large as the database. The access
# and device state (KEEP_ON_RESTORE) in the copy is kept that of the running installation: the older version takes
# the copy as it is, and an old password, sessions or revoked app tokens must not become valid again with it.
MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {1: _consumption_from_balance, 2: _external_pv_columns,
                                                               3: _allowed_hosts_from_the_app_only, 4: _car_tables}
SCHEMA_VERSION = 5
COPY_SUFFIX = ".schema-"  # <db>.schema-<version>: the database as it was before the upgrade from that version
NEWER_SUFFIX = ".newer-"  # <db>.newer-<version>-<time>: a newer version's database, set aside after a rollback
COPY_SPARE_BYTES = 50_000_000  # free space left over after the copy, so the app can still write


SIDE_FILES = ("-wal", "-shm")  # SQLite's write-ahead log and its index belong to the database file
DAMAGED_SUFFIX = ".damaged-"  # a damaged database is kept under this name plus the time (#165)
RESTORE_SUFFIX = ".restore"  # a checked backup that replaces the database on the next start
KEPT_SUFFIX = ".before-restore-"  # the database a restored backup replaced, plus the time
REQUIRED_TABLES = {"samples", "energy_15m", "meta"}  # every OpenAmpere database has them since the first release
# Set when a damaged database had a password that could not be taken over: the first-password setup stays closed
# until `openampere reset-password` on the server, otherwise anybody in the home network could choose one first.
SETUP_LOCKED = "setup_locked"
# Access and device state stay those of the running installation when a backup is restored: backups contain no
# sessions, and an empty or old password would let anyone in the home network take over (or lock out the owner).
# api_tokens: tokens revoked since the backup must stay revoked. remote_command: whether OpenAmpere still has to
# switch off the inverter's remote control right now (#141). grid_charging_reserve(_stops_at_reserve): a backup reserve
# 0.23.1 raised and that is still to be set back (charging.RAISED_RESERVE, #320); a backup must neither bring back an
# old one nor drop one that is pending.
KEEP_ON_RESTORE = ("auth", "sessions", "api_tokens", "remote_command", "consumer_switched_on",  # last: #243
                   "known_devices", SETUP_LOCKED, "grid_charging_reserve", "grid_charging_stops_at_reserve")
SQLITE_CORRUPT, SQLITE_NOTADB = 11, 26


class DatabaseInUse(RuntimeError):
    pass


class DatabaseDamaged(sqlite3.DatabaseError):
    """The integrity check found errors in the database file."""


class InvalidBackup(ValueError):
    """An uploaded file that cannot be restored, with the reason for the owner."""


class SchemaError(RuntimeError):
    """This version of OpenAmpere cannot use the database; the message tells the owner what to do."""


class _NewerSchema(Exception):
    def __init__(self, version: int) -> None:
        super().__init__(version)
        self.version = version


def _is_damage(err: sqlite3.DatabaseError) -> bool:
    """Only a damaged file is moved aside, never one that is locked, read-only or on a missing disk."""
    if isinstance(err, DatabaseDamaged):
        return True
    code = getattr(err, "sqlite_errorcode", None)
    if code is not None:
        return code & 0xFF in (SQLITE_CORRUPT, SQLITE_NOTADB)
    text = str(err).lower()
    return "malformed" in text or "not a database" in text


def _salvage_meta(path: Path) -> tuple[dict, bool]:
    """Reads the access and device state (KEEP_ON_RESTORE) row by row from a damaged database, read-only: a power cut
    usually damages the pages of the readings, not the few rows of meta. Returns the values that could be read and
    whether the file certainly had no password (then there is nothing to protect)."""
    values, no_password = {}, False
    try:
        db = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    except (sqlite3.Error, OSError):
        return values, False
    try:
        for key in KEEP_ON_RESTORE:
            try:
                row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
                value = None if row is None else json.loads(row[0])
            except (sqlite3.Error, ValueError, TypeError):
                continue
            if value is None:
                no_password = no_password or key == "auth"
            elif key != "auth" or isinstance(value, dict) and {"salt", "hash"} <= value.keys():
                values[key] = value  # a password OpenAmpere cannot check counts as lost, not as absent
    finally:
        db.close()
    return values, no_password


def _move_with_side_files(source: Path, target: Path) -> None:
    """Renames a database together with its write-ahead log, so the copy can still be opened as it was."""
    for suffix in ("", *SIDE_FILES):
        if os.path.exists(f"{source}{suffix}"):
            os.replace(f"{source}{suffix}", f"{target}{suffix}")


def _only_for_owner(path: Path) -> None:
    """The database, its side files and copies readable only by the app user (also files of older versions, which
    were created readable for everyone): they hold the password hash, history and meter numbers."""
    if not hasattr(os, "getuid"):  # Windows: no such permissions
        return
    for name in glob.glob(f"{glob.escape(str(path))}*"):
        try:
            info = os.stat(name)
            if os.path.isfile(name) and info.st_uid == os.getuid() and info.st_mode & 0o077:
                os.chmod(name, 0o600)
        except OSError:  # e.g. removed in the meantime, or a file system without permissions
            pass


def _stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def _current_access_rows(path: Path) -> dict[str, str | None]:
    """The access and device state (KEEP_ON_RESTORE) of the database as it is now, as stored (None: no such row).
    Rows that cannot be read (e.g. a damaged file) are left out."""
    rows: dict[str, str | None] = {}
    if not path.is_file():
        return rows
    try:
        db = sqlite3.connect(str(path))  # not read-only: only then can SQLite read a write-ahead log left over
    except (sqlite3.Error, OSError):
        return rows
    try:
        for key in KEEP_ON_RESTORE:
            try:
                row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            except sqlite3.Error:
                continue
            rows[key] = None if row is None else row[0]
    finally:
        db.close()
    return rows


def _apply_pending_restore(path: Path) -> Path | None:
    """Puts a backup that was restored in the app in place of the database, before anything opens it.
    The previous database is kept next to it. Returns where. The access and device state (KEEP_ON_RESTORE) is taken
    from the database it replaces, not from the time the backup was uploaded: a password changed, a logout everywhere
    or an app access revoked in between stays. Without that, the backup is not restored."""
    pending = Path(f"{path}{RESTORE_SUFFIX}")
    if not pending.is_file():
        return None
    try:
        db = sqlite3.connect(str(pending))
        try:
            with db:
                for key, value in _current_access_rows(path).items():
                    if value is None:
                        db.execute("DELETE FROM meta WHERE key=?", (key,))
                    else:
                        db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", (key, value))
        finally:
            db.close()
    except (sqlite3.Error, OSError) as err:  # e.g. a full disk: the owner can upload the backup again
        log.error("could not take the password and app accesses over into the restored backup (%s); "
                  "the backup is not restored", err)
        for suffix in ("", "-journal"):
            Path(f"{pending}{suffix}").unlink(missing_ok=True)
        return None
    kept = path.with_name(f"{path.name}{KEPT_SUFFIX}{_stamp()}")
    _move_with_side_files(path, kept)  # also a write-ahead log without its database: it must not meet the backup
    os.replace(pending, path)
    log.warning("restored a backup; the previous database is kept as %s", kept.name)
    return kept


def _external_delta(total: float, state: dict, snap: Snapshot) -> float | None:
    """Energy of the second PV inverter since the base of the quarter (#419); None while it is not counted and
    nothing was integrated, so quarters without it stay empty in the report."""
    part = max(0.0, total - state.get("external", total))
    return part if part or snap.pv_external_power is not None else None


def _create_version_1(db: sqlite3.Connection) -> None:
    """The tables of schema version 1, also in a database from before schema versions."""
    db.executescript(SCHEMA)
    existing = {row[1] for row in db.execute("PRAGMA table_info(samples)")}
    for column in SAMPLE_EXTRA_COLUMNS:
        if column not in existing:
            db.execute(f"ALTER TABLE samples ADD COLUMN {column} REAL")
    db.execute("PRAGMA user_version=1")


def _copy_backup(upload: Path, target: Path) -> None:
    """Copies the rows of a checked backup into a new database with the tables of this version. Nothing else of
    the uploaded file is taken over: no triggers, views or other table definitions, which could otherwise act in the
    running installation (e.g. bring back a revoked session on every write)."""
    db = sqlite3.connect(str(target))
    try:
        db.execute("PRAGMA journal_mode=DELETE")  # everything in the one file that is moved
        _create_version_1(db)
        Storage._migrate(db, 1, max(1, _user_version(upload)))  # the backup's version: the next start upgrades it
        db.execute("PRAGMA trusted_schema=OFF")  # no functions from the backup's schema while reading it
        db.execute("ATTACH DATABASE ? AS backup", (str(upload),))
        tables = {row[0] for row in db.execute("SELECT name FROM backup.sqlite_master WHERE type='table' "
                                               "AND sql LIKE 'CREATE TABLE %'")}  # no virtual tables
        with db:
            for (table,) in db.execute("SELECT name FROM main.sqlite_master WHERE type='table'").fetchall():
                if table not in tables:
                    continue
                ours = [row[1] for row in db.execute(f"PRAGMA main.table_info({table})")]
                theirs = {row[1] for row in db.execute(f"PRAGMA backup.table_info({table})")}
                columns = ", ".join(f'"{c}"' for c in ours if c in theirs)  # names of this version only
                if columns:
                    db.execute(f"INSERT OR REPLACE INTO main.{table}({columns}) SELECT {columns} FROM backup.{table}")
        db.execute("DETACH DATABASE backup")
    finally:
        db.close()


def schema_copies(path: Path) -> list[Path]:
    """The copies an older version continues with after a rollback (<db>.schema-<version>, see MIGRATIONS)."""
    copies = []
    for copy in path.parent.glob(f"{glob.escape(path.name)}{COPY_SUFFIX}*"):
        number = copy.name[len(path.name) + len(COPY_SUFFIX):]
        if number.isdigit() and int(number) < SCHEMA_VERSION:
            copies.append(copy)
    return copies


def _user_version(path: Path) -> int:
    db = sqlite3.connect(str(path))
    try:
        return db.execute("PRAGMA user_version").fetchone()[0]
    finally:
        db.close()


def _audited_changes(current: dict, restored: dict) -> dict:
    """The changes of AUDITED settings by a restore, as the control log shows changes in the app."""
    from .config import AUDITED, Config, get_value  # the configuration does not need the storage
    defaults = Config()
    values = {key: (current.get(key, get_value(defaults, key)), restored.get(key, get_value(defaults, key)))
              for key in AUDITED}
    changed = {key: value for key, value in values.items() if value[0] != value[1]}
    return {"from": {k: v[0] for k, v in changed.items()}, "to": {k: v[1] for k, v in changed.items()}} if changed else {}


def check_backup(path: str | Path) -> None:
    """Raises InvalidBackup unless the file is an intact OpenAmpere database."""
    with open(path, "rb") as file:
        header = file.read(16)
    if header != b"SQLite format 3\x00":
        raise InvalidBackup("Das ist keine Sicherung von OpenAmpere. Bitte die Datei wählen, die unter "
                            "„Datensicherung herunterladen“ gespeichert wurde.")
    db = sqlite3.connect(str(path))
    try:
        problems = [row[0] for row in db.execute("PRAGMA integrity_check(5)")]
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        version = db.execute("PRAGMA user_version").fetchone()[0]
    except sqlite3.DatabaseError:
        problems, tables, version = ["error"], set(), 0
    finally:
        db.close()
    if problems != ["ok"]:
        raise InvalidBackup("Die Sicherung ist beschädigt und kann nicht wiederhergestellt werden. Bitte eine "
                            "andere Sicherung wählen.")
    if not REQUIRED_TABLES <= tables:
        raise InvalidBackup("Das ist keine Sicherung von OpenAmpere. Bitte die Datei wählen, die unter "
                            "„Datensicherung herunterladen“ gespeichert wurde.")
    if version > SCHEMA_VERSION:
        raise InvalidBackup("Die Sicherung stammt von einer neueren Version von OpenAmpere. Bitte zuerst OpenAmpere "
                            "aktualisieren und die Sicherung dann wiederherstellen.")


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
        # One connection shared by the event loop, worker threads and the web server's thread pool.
        # SQLite connections must not be used by several threads at the same time (crashes with a
        # segmentation fault on some builds), so EVERY access - reads included - holds this lock.
        self._lock = threading.RLock()
        memory = str(path) == ":memory:"
        kept = None
        if not memory:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._lock_fd = _lock_exclusively(path)
            kept = _apply_pending_restore(path)
        rolled_back = None
        try:
            try:
                self._db = self._open(check=not memory)
            except _NewerSchema as newer:
                rolled_back = self._use_own_copy(path, newer.version)
                self._db = self._open(check=not memory)
        except sqlite3.DatabaseError as err:
            if memory or not _is_damage(err):
                raise
            # typical after a power cut on an SD card: without this the app would not start at all, and the
            # owner could not even download a backup (#165)
            damaged = path.with_name(f"{path.name}{DAMAGED_SUFFIX}{_stamp()}")
            log.error("the database is damaged (%s); it is kept as %s and OpenAmpere starts with an empty one",
                      err, damaged.name)
            _move_with_side_files(path, damaged)
            self._db = self._open(check=False)
            # the password, sessions and device state are taken over if they can be read; a password that is lost
            # must not open the first-password setup to anybody in the home network
            values, no_password = _salvage_meta(damaged)
            if "auth" not in values and not no_password:
                values[SETUP_LOCKED] = {"ts": time.time()}
            with self._db:
                for key, value in values.items():
                    self._set_meta(key, value)
                self._set_meta("database_damaged", {"ts": time.time(), "file": damaged.name})
        if not memory:
            _only_for_owner(path)
        self._copies = [] if memory else schema_copies(path)
        with self._lock:
            self._keep_copies_current(KEEP_ON_RESTORE)  # also what was changed while OpenAmpere was not running
        if kept is not None:
            self.set_meta("database_restored", {"ts": time.time(), "kept": kept.name})
        if rolled_back is not None:
            self.set_meta("database_rollback", rolled_back)
        # per-input energy of the running quarter; persisted so a restart does not lose it
        self._pv_acc: dict | None = self._get_meta("pv_input_state")
        self._quarter: dict | None = self._get_meta("quarter_state")
        # the second PV inverter has no energy counter: its power is integrated into one of OpenAmpere's own (#419)
        self._external: dict = self._get_meta("pv_external_state") or {"total": 0.0, "ts": None, "w": None}
        row = self._db.execute("SELECT MAX(ts) FROM samples").fetchone()
        self._last_sample_ts: float = row[0] or 0.0
        self._clock_warned = False
        self._step: tuple[float, int | None] | None = None  # (computed at, step of the energy counters in Wh)

    def _open(self, check: bool) -> sqlite3.Connection:
        """Connects and brings the tables up to date. check: run SQLite's quick integrity check, which reads the
        whole file once (a few seconds for a large database on an SD card), so damage is found at the start."""
        db = sqlite3.connect(self.path, check_same_thread=False)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA journal_mode=WAL")
            if check:
                problems = [row[0] for row in db.execute("PRAGMA quick_check(5)")]
                if problems != ["ok"]:
                    raise DatabaseDamaged("; ".join(problems))
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise _NewerSchema(version)
            new = db.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0] == 0
            if version == 0:
                # a new database, or one from before schema versions: brought to version 1 the way all versions
                # before did it (and still do, they ignore the version), so no copy is needed
                _create_version_1(db)
                version = 1
            if version < SCHEMA_VERSION:
                if not new:
                    self._copy_before_upgrade(db, version)
                self._migrate(db, version)
        except BaseException:
            db.close()
            raise
        return db

    @staticmethod
    def _migrate(db: sqlite3.Connection, version: int, target: int | None = None) -> None:
        for step in range(version, SCHEMA_VERSION if target is None else target):
            log.info("updating the database from schema version %s to %s", step, step + 1)
            db.execute("BEGIN")
            try:
                MIGRATIONS[step](db)
                db.execute(f"PRAGMA user_version={step + 1}")
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def _copy_before_upgrade(self, db: sqlite3.Connection, version: int) -> None:
        """Copies the database to <db>.schema-<version> (SQLite's backup, consistent also with a write-ahead log).
        Copies of older versions are removed: each is as large as the database and only the newest can still be
        needed. Without enough free space the upgrade does not start (the updater then keeps the old version)."""
        path = Path(self.path)
        target = Path(f"{path}{COPY_SUFFIX}{version}")
        size = sum(os.path.getsize(f"{path}{s}") for s in ("", *SIDE_FILES) if os.path.exists(f"{path}{s}"))
        free = shutil.disk_usage(path.resolve().parent).free + (target.stat().st_size if target.exists() else 0)
        if free < size + COPY_SPARE_BYTES:
            raise SchemaError(f"Für das Update der Datenbank fehlt Speicherplatz: OpenAmpere legt vorher eine Kopie an "
                              f"(etwa {size // 1_000_000 + 1} MB). Bitte Speicherplatz freigeben und OpenAmpere neu "
                              "starten.")
        partial = Path(f"{target}.partial")
        dest = sqlite3.connect(str(partial))
        try:
            db.backup(dest)
        except BaseException:
            dest.close()
            partial.unlink(missing_ok=True)
            raise
        dest.close()
        os.replace(partial, target)
        for old in path.parent.glob(f"{glob.escape(path.name)}{COPY_SUFFIX}*"):
            number = old.name[len(path.name) + len(COPY_SUFFIX):]
            if number.isdigit() and int(number) < version:
                old.unlink()
        log.warning("copied the database to %s before updating its schema", target.name)

    @staticmethod
    def _use_own_copy(path: Path, version: int) -> dict:
        """A newer version has upgraded the database and this older one runs again (e.g. rolled back by the
        updater): continue with the copy made before that upgrade, and keep the newer database aside (#166)."""
        copy = Path(f"{path}{COPY_SUFFIX}{SCHEMA_VERSION}")
        if not copy.is_file():
            raise SchemaError(f"Die Datenbank stammt von einer neueren Version von OpenAmpere (Datenbank-Version "
                              f"{version}, diese Version kennt nur bis {SCHEMA_VERSION}) und es gibt keine Kopie von "
                              "vorher. Bitte wieder die neuere Version von OpenAmpere installieren.")
        newer = path.with_name(f"{path.name}{NEWER_SUFFIX}{version}-{_stamp()}")
        log.warning("the database is from a newer version of OpenAmpere (schema %s, this one knows up to %s): using "
                    "the copy %s from before that upgrade, the newer database is kept as %s",
                    version, SCHEMA_VERSION, copy.name, newer.name)
        _move_with_side_files(path, newer)
        _move_with_side_files(copy, path)
        return {"ts": time.time(), "version": version, "kept": newer.name}

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
        with self._lock:
            with self._db:
                self._set_meta(key, value)
            self._keep_copies_current((key,))

    def replace_meta(self, key: str, value, field: str, expected) -> bool:
        """Replaces an entry only while its field still has the expected value. One statement, so nothing written in
        between (also by another process, e.g. `openampere reset-password`) is overwritten."""
        with self._lock:
            with self._db:
                replaced = self._db.execute("UPDATE meta SET value=? WHERE key=? AND json_extract(value, ?)=?",
                                            (json.dumps(value), key, f"$.{field}", expected)).rowcount == 1
            if replaced:
                self._keep_copies_current((key,))
            return replaced

    def delete_meta(self, key: str) -> None:
        with self._lock:
            with self._db:
                self._db.execute("DELETE FROM meta WHERE key=?", (key,))
            self._keep_copies_current((key,))

    def _keep_copies_current(self, keys: tuple[str, ...]) -> None:
        """Writes the access and device state (KEEP_ON_RESTORE) into the copy from before the upgrade, see MIGRATIONS.
        The released versions take the copy as it is after a rollback. Call with the lock held."""
        keys = tuple(key for key in keys if key in KEEP_ON_RESTORE)
        if not keys or not self._copies:
            return
        rows = {key: self._db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone() for key in keys}
        for copy in self._copies:
            try:
                db = sqlite3.connect(str(copy))
                try:
                    with db:
                        for key, row in rows.items():
                            if row is None:
                                db.execute("DELETE FROM meta WHERE key=?", (key,))
                            else:
                                db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", (key, row[0]))
                finally:
                    db.close()
            except (sqlite3.Error, OSError) as err:  # e.g. a full disk: the next start tries again
                log.warning("could not update %s in %s: %s", ", ".join(keys), copy.name, err)

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

    def last_control(self, action: str, result_prefix: str) -> float | None:
        """When an action with this result last really happened (test mode not counted), e.g. a grid charging start."""
        rows = self._fetchall("SELECT ts FROM control_log WHERE action=? AND dry_run=0 AND result LIKE ? "
                              "ORDER BY ts DESC LIMIT 1", (action, result_prefix.replace("%", "") + "%"))
        return rows[0]["ts"] if rows else None

    def last_overwrite(self, since: float) -> dict | None:
        """The newest change another device made to a value OpenAmpere had written (an entry "…_check"), with the
        names of the settings. None once OpenAmpere wrote that setting again: a new check then decides."""
        rows = self._fetchall("SELECT * FROM control_log WHERE action LIKE '%\\_check' ESCAPE '\\' AND ts > ? "
                              "ORDER BY ts DESC LIMIT 1", (since,))
        if not rows:
            return None
        entry = rows[0]
        if self._fetchall("SELECT 1 FROM control_log WHERE action=? AND dry_run=0 AND ts > ? LIMIT 1",
                          (entry["action"].removesuffix("_check"), entry["ts"])):
            return None
        return {"ts": entry["ts"], "settings": sorted(json.loads(entry["details"]).get("to") or {})}

    def control_log(self, limit: int = 50) -> list[dict]:
        """The newest entries first; limit 0 returns all of them."""
        rows = self._fetchall("SELECT * FROM control_log ORDER BY ts DESC LIMIT ?", (limit or -1,))
        return [{**r, "details": json.loads(r["details"]), "dry_run": bool(r["dry_run"])} for r in rows]

    def backup(self, target: str | Path, *, drop_settings: set[str] | frozenset = frozenset()) -> None:
        """Consistent copy of the database while it is in use. Sessions, the password hash, the app tokens and the
        given secret settings (e.g. API keys) are removed from the copy; a restore keeps those of the installation."""
        with self._lock:
            dest = sqlite3.connect(str(target))
            with dest:
                self._db.backup(dest)
            with dest:
                row = dest.execute("SELECT value FROM meta WHERE key='settings'").fetchone()
                if row:
                    settings = {k: v for k, v in json.loads(row[0]).items() if k not in drop_settings}
                    dest.execute("UPDATE meta SET value=? WHERE key='settings'", (json.dumps(settings),))
                dest.execute("DELETE FROM meta WHERE key IN ('sessions', 'auth', 'api_tokens', 'known_devices', "
                             "'grid_charging_reserve', 'grid_charging_stops_at_reserve')")
            dest.execute("VACUUM")  # really remove the deleted data from the file
            dest.close()

    def stage_restore(self, upload: str | Path, *, settings: Callable[[dict, dict], dict] | None = None,
                      source: str | None = None) -> None:
        """Checks an uploaded backup and copies its data next to the database, where it replaces it on the next start
        (nothing has the database open then). The password, sessions and app tokens are taken over from the running
        installation; settings(backup, current) gives the settings to restore (config.restore_settings). Every
        restore is in the control log, with who restored it (source, see api.actor). Raises InvalidBackup, or OSError
        without enough free space."""
        upload = Path(upload)
        check_backup(upload)
        with self._lock:
            keep = {key: self._get_meta(key) for key in KEEP_ON_RESTORE}
            current = self.get_settings()
            revision = int(self._get_meta("settings_revision") or 0)
        staged = upload.with_name(f"{upload.name}.copy")  # an upload's name: removed at the start if left over
        if upload.stat().st_size > shutil.disk_usage(upload.parent).free:
            raise OSError(errno.ENOSPC, "no space for the copy of the backup")
        db = None
        try:
            _copy_backup(upload, staged)
            db = sqlite3.connect(str(staged))
            # upgraded now, not at the next start: the settings written below are those of this version, and an
            # upgrade step must not change them afterwards (e.g. remove the own host names kept from now, #437)
            self._migrate(db, db.execute("PRAGMA user_version").fetchone()[0])
            with db:
                row = db.execute("SELECT value FROM meta WHERE key='settings'").fetchone()
                restored = json.loads(row[0]) if row else {}
                if not isinstance(restored, dict):
                    raise InvalidBackup("Die Sicherung ist beschädigt und kann nicht wiederhergestellt werden. "
                                        "Bitte eine andere Sicherung wählen.")
                if settings is not None:
                    restored = settings(restored, current)
                # update_latest: what the app shows as a new version, with a link, is looked up again
                values = {**keep, "settings": restored, "settings_revision": revision + 1,
                          "database_damaged": None, "database_restored": None, "update_latest": None}
                # also control switched on or off by the restore
                details = {"from": {}, "to": {}, **_audited_changes(current, restored),
                           **({"source": source} if source else {})}
                result = "Datensicherung wiederhergestellt"
                db.execute("INSERT INTO control_log VALUES (?,?,?,?,?)",
                           (time.time(), "control_switches", json.dumps(details), 0, result))
                for key, value in values.items():
                    if value is None:
                        db.execute("DELETE FROM meta WHERE key=?", (key,))
                    else:
                        db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", (key, json.dumps(value)))
            db.close()
            db = None
            os.replace(staged, f"{self.path}{RESTORE_SUFFIX}")
        except InvalidBackup:
            raise
        except sqlite3.OperationalError as err:
            if "full" in str(err):  # the disk, not the backup
                raise OSError(errno.ENOSPC, str(err)) from err
            raise InvalidBackup("Die Sicherung ist beschädigt und kann nicht wiederhergestellt werden. Bitte eine "
                                "andere Sicherung wählen.") from err
        except (sqlite3.DatabaseError, TypeError, ValueError) as err:  # e.g. NULL in a required column, broken JSON
            raise InvalidBackup("Die Sicherung ist beschädigt und kann nicht wiederhergestellt werden. Bitte eine "
                                "andere Sicherung wählen.") from err
        finally:
            if db is not None:
                db.close()
            for suffix in ("", "-journal"):
                Path(f"{staged}{suffix}").unlink(missing_ok=True)

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
                f"INSERT OR REPLACE INTO samples(ts, pv, house, grid, battery, soc, {', '.join(SAMPLE_EXTRA_COLUMNS)}, "
                f"{EXTERNAL}) VALUES ({', '.join('?' * (7 + len(SAMPLE_EXTRA_COLUMNS)))})",
                (snap.timestamp, snap.pv_power, snap.house_power, snap.grid_power, snap.battery_power,
                 snap.battery_soc, *inputs, temps.get("inverter"), temps.get("battery"),
                 temps.get("battery_cell_max"), temps.get("battery_cell_min"), snap.pv_external_power))
            self._integrate_external(snap)
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

    def _integrate_external(self, snap: Snapshot) -> None:
        """Integrates the power of the second PV inverter (left Riemann sum, not across outages) into a lifetime counter
        of OpenAmpere's own, which the quarter hours then treat like the inverter's counters (#419). It only grows
        while the second inverter is counted."""
        state = self._external
        if state["ts"] is not None and state["w"] is not None:
            dt = snap.timestamp - state["ts"]
            if 0 < dt <= MAX_INTEGRATION_GAP_S:
                state["total"] += max(0.0, state["w"]) * dt / 3600
        state["ts"], state["w"] = snap.timestamp, snap.pv_external_power
        self._set_meta("pv_external_state", state)

    def external_total(self) -> float:
        """Energy of the second PV inverter OpenAmpere has integrated so far, in Wh (#419)."""
        with self._lock:
            return round(self._external["total"], 1)

    def _accumulate(self, snap: Snapshot) -> None:
        """Turn monotonic counters into per-quarter energy rows.

        The counters at the first reading of a quarter are the base; at the first reading of a later
        quarter the difference is stored. Guards against glitches:
        - a counter that drops to 0 (device booting, proxy cache) is ignored, the base is kept
        - a counter that goes backwards (counter reset) only re-bases
        - an implausibly large difference (garbage reading) only re-bases
        - after an outage the difference is spread evenly over the missing quarters (up to MAX_GAP_S)
        The consumption is the balance of the other counters, not the inverter's load counter (#346).
        """
        totals = {f: getattr(snap.totals, f) for f in COUNTERS}
        if any(v is None for v in totals.values()):
            return
        state = self._quarter
        if state is not None and any(totals[f] == 0 and state["totals"][f] > 100 for f in COUNTERS):
            log.warning("ignoring reading with a counter that dropped to 0")
            self._note_glitch(snap.timestamp, "Zähler kurz auf 0")
            return
        quarter = int(snap.timestamp // QUARTER * QUARTER)
        carry = 0.0  # re-basing drops the deficit of the balance with the energy it belonged to
        external = self._external["total"]
        if state is None or quarter < state["ts"]:
            self._quarter = {"ts": quarter, "totals": totals, "carry": carry, "external": external}
        elif quarter > state["ts"]:
            span = quarter - state["ts"]
            delta = {f: totals[f] - state["totals"][f] for f in COUNTERS}
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
                # the second PV inverter is part of the solar energy, and so of the consumption (#419)
                part = _external_delta(external, state, snap)
                delta["pv"] += part or 0.0
                delta["load"], carry = _consumption(delta, state.get("carry", 0.0))
                quarters = span // QUARTER  # spread the energy evenly over the quarters without readings
                rows = [(state["ts"] + i * QUARTER, *(delta[f] / quarters for f in FLOWS),
                         snap.battery_soc if i == quarters - 1 else None, None if part is None else part / quarters)
                        for i in range(quarters)]
                self._db.executemany(
                    f"INSERT OR REPLACE INTO energy_15m(ts, {', '.join(FLOWS)}, soc, {EXTERNAL}, source) "
                    f"VALUES (?, {', '.join('?' * len(FLOWS))}, ?, ?, 'local')", rows)
            self._quarter = {"ts": quarter, "totals": totals, "carry": carry, "external": external}
        else:
            return
        self._set_meta("quarter_state", self._quarter)

    def running_quarter(self, snap: Snapshot | None) -> dict | None:
        """Energy of the quarter hour that is still running (not yet stored), from the latest counters."""
        with self._lock:
            state = self._quarter
        if snap is None or state is None:
            return None
        totals = {f: getattr(snap.totals, f) for f in COUNTERS}
        if any(v is None for v in totals.values()) or snap.timestamp - state["ts"] > 2 * QUARTER:
            return None
        delta = {f: totals[f] - state["totals"][f] for f in COUNTERS}
        if any(v < 0 or v > MAX_POWER_W * QUARTER / 3600 * 1.5 for v in delta.values()):
            return None
        with self._lock:
            part = _external_delta(self._external["total"], state, snap)
        delta["pv"] += part or 0.0
        delta["load"], _ = _consumption(delta, state.get("carry", 0.0))
        return {"ts": state["ts"], **delta, EXTERNAL: part, "soc": snap.battery_soc}

    def days_with_energy(self, start: float, end: float) -> int:
        """Number of days with at least one stored quarter hour (also for coarser imported history)."""
        rows = self._fetchall("SELECT COUNT(DISTINCT CAST(ts / 86400 AS INTEGER)) AS n FROM energy_15m WHERE ts >= ? AND ts < ?",
                              (start, end))
        return int(rows[0]["n"] or 0)

    def energy_step_wh(self, now: float | None = None) -> int | None:
        """Step of the inverter's energy counters in Wh as seen in the recorded quarter hours (#194): 100 if
        enough non-zero differences are all multiples of 0.1 kWh (classic FoxESS map, or firmware that reports
        0.01 kWh units but counts in 0.1 kWh), otherwise 10. None if there is too little data to tell.

        Looks at the latest local quarters only (bounded) and caches the result for an hour. Quarters filled by
        spreading an outage (neighbouring rows with the same values) are skipped: they hold a share of a
        difference, not a counter step."""
        now = time.time() if now is None else now
        cached = self._step
        if cached is not None and 0 <= now - cached[0] < STEP_CACHE_S:
            return cached[1]
        with self._lock:
            rows = self._db.execute(
                f"SELECT ts, {', '.join(FLOWS)} FROM energy_15m WHERE source = 'local' ORDER BY ts DESC LIMIT ?",
                (STEP_QUARTERS,)).fetchall()
        deltas = []
        for i, row in enumerate(rows):
            neighbours = (rows[j] for j in (i - 1, i + 1) if 0 <= j < len(rows))
            if any(abs(other[0] - row[0]) == QUARTER and other[1:] == row[1:] for other in neighbours):
                continue
            deltas.extend(v for v in row[1:] if v is not None and abs(v) >= 0.5)
        step = None
        if len(deltas) >= STEP_MIN_DELTAS:
            step = 100 if all(abs(v - round(v / 100) * 100) < 0.5 for v in deltas) else 10
        self._step = (now, step)
        return step

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

    def free_bytes(self) -> int | None:
        """Free space on the disk that holds the database; None if it cannot be determined."""
        try:
            return shutil.disk_usage(os.path.dirname(os.path.abspath(self.path))).free
        except OSError:
            return None

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

    # ---- cars (#401) -----------------------------------------------------

    CAR_FIELDS = ("soc", "range_km", "odometer_km", "plugged", "charging", "target_soc")

    def add_car_points(self, car: str, rows: list[dict]) -> None:
        """Data of a car as its manufacturer delivered it; the same delivery again replaces the first."""
        with self._lock, self._db:
            self._db.executemany(
                f"INSERT OR REPLACE INTO car_points(car, ts, {', '.join(self.CAR_FIELDS)}) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(car, row["ts"], *(row.get(f) for f in self.CAR_FIELDS)) for row in rows])

    def car_last_ts(self, car: str) -> float | None:
        return self._fetchall("SELECT MAX(ts) AS ts FROM car_points WHERE car = ?", (car,))[0]["ts"]

    def car_value(self, car: str, field: str, *, before: float | None = None, after: float | None = None,
                  newest: bool = True) -> dict | None:
        """The newest (or oldest) delivery of one value in a time range: {"ts": …, "value": …}."""
        if field not in self.CAR_FIELDS:
            raise ValueError(field)
        sql = f"SELECT ts, {field} AS value FROM car_points WHERE car = ? AND {field} IS NOT NULL"
        params: list = [car]
        if after is not None:
            sql += " AND ts >= ?"
            params.append(after)
        if before is not None:
            sql += " AND ts <= ?"
            params.append(before)
        rows = self._fetchall(sql + f" ORDER BY ts {'DESC' if newest else 'ASC'} LIMIT 1", tuple(params))
        return rows[0] if rows else None

    def car_points(self, car: str, start: float, end: float) -> list[dict]:
        return self._fetchall(f"SELECT ts, {', '.join(self.CAR_FIELDS)} FROM car_points WHERE car = ? AND ts >= ? "
                              "AND ts < ? ORDER BY ts", (car, start, end))

    def open_car_session(self, car: str, wallbox: str, start: float) -> int:
        with self._lock, self._db:
            return self._db.execute("INSERT INTO car_sessions(car, wallbox, start) VALUES (?, ?, ?)",
                                    (car, wallbox, start)).lastrowid

    def update_car_session(self, session_id: int, kwh: float, end: float | None = None, *,
                           counter: float | None = None) -> None:
        """counter: the wallbox's energy counter that goes with kwh, to go on counting after a restart."""
        with self._lock, self._db:
            self._db.execute("UPDATE car_sessions SET kwh = ?, end = COALESCE(?, end), counter = COALESCE(?, counter) "
                             "WHERE id = ?", (kwh, end, counter, session_id))

    def car_sessions(self, car: str | None = None, *, open_only: bool = False, limit: int = 500) -> list[dict]:
        sql, params = "SELECT id, car, wallbox, start, end, kwh, counter FROM car_sessions WHERE 1 = 1", []
        if car is not None:
            sql += " AND car = ?"
            params.append(car)
        if open_only:
            sql += " AND end IS NULL"
        return self._fetchall(sql + " ORDER BY start DESC LIMIT ?", (*params, limit))

    def delete_car(self, car: str) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM car_points WHERE car = ?", (car,))
            self._db.execute("DELETE FROM car_sessions WHERE car = ?", (car,))

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
                f"SELECT {', '.join(f'SUM({f}) AS {f}' for f in (*FLOWS, EXTERNAL))}, COUNT(*) AS quarters "
                "FROM energy_15m WHERE ts >= ? AND ts < ?", (start, end)).fetchone()
        return dict(row)
