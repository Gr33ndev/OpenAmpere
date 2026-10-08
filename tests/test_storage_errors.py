"""A failing database write (e.g. disk full) shows up in /api/status instead of only in the log (#170)."""

import logging
import sqlite3

from fastapi.testclient import TestClient

from openampere import collector as collector_module
from openampere.api import create_app
from openampere.collector import Collector, storage_problem
from openampere.drivers.base import Snapshot
from openampere.runtime import Runtime
from openampere.storage import Storage

from conftest import login


def test_storage_problem_kinds():
    assert storage_problem(sqlite3.OperationalError("database or disk is full")) == "full"
    assert storage_problem(OSError(28, "No space left on device")) == "full"
    assert storage_problem(sqlite3.OperationalError("attempt to write a readonly database")) == "read_only"
    assert storage_problem(sqlite3.OperationalError("disk I/O error")) == "other"


async def test_failed_writes_are_tracked_and_logged_once(tmp_path, monkeypatch, caplog):
    storage = Storage(tmp_path / "t.db")
    collector = Collector(None, storage, 10, 30)
    clock = [1000.0]
    monkeypatch.setattr(collector_module.time, "time", lambda: clock[0])
    working = storage.add_snapshot

    def full(_snap):
        raise sqlite3.OperationalError("database or disk is full")

    monkeypatch.setattr(storage, "add_snapshot", full)
    caplog.set_level(logging.INFO, logger="openampere.collector")
    for _ in range(30):  # five minutes of polls
        await collector._store(Snapshot(timestamp=clock[0]))
        clock[0] += 10
    assert collector.storage_failing_since == 1000.0 and collector.storage_error == "full"
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1  # not one line per poll

    clock[0] = 1000.0 + 3700
    await collector._store(Snapshot(timestamp=clock[0]))
    assert len([r for r in caplog.records if r.levelno == logging.ERROR]) == 2  # reminded after an hour

    monkeypatch.setattr(storage, "add_snapshot", working)
    await collector._store(Snapshot(timestamp=clock[0] + 10))
    assert collector.storage_failing_since is None and collector.storage_error is None
    assert "stored again" in caplog.records[-1].getMessage()


def test_status_reports_storage_errors_and_low_space(tmp_path, monkeypatch):
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    client = TestClient(create_app(runtime))
    login(client)
    storage = client.get("/api/status").json()["storage"]
    assert storage["failing_since"] is None and storage["error"] is None and storage["free_bytes"] > 0

    runtime.collector.storage_failing_since, runtime.collector.storage_error = 1000.0, "full"
    monkeypatch.setattr(runtime.storage, "free_bytes", lambda: 50 * 1024 * 1024)
    storage = client.get("/api/status").json()["storage"]
    assert storage == {"failing_since": 1000.0, "error": "full", "free_bytes": 50 * 1024 * 1024, "low_space": True}
