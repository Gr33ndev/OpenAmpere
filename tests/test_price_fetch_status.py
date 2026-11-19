# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""#654: the app shows whether the exchange prices could be fetched and whether the next day's prices are there."""

import asyncio
import json
from datetime import datetime

from fastapi.testclient import TestClient

from openampere import tariffs as tariffs_module
from openampere.api import create_app
from openampere.runtime import Runtime
from openampere.storage import QUARTER, Storage
from openampere.tariffs import FETCH_EVERY_S, RETRY_AFTER_ERROR_S

DYNAMIC = {"valid_from": "2026-01-01", "kind": "dynamic", "surcharge_ct": 20, "vat_percent": 19, "feed_in_ct": 8}


def offline(*_args, **_kwargs):
    raise OSError("network is unreachable")


def test_a_failed_fetch_is_kept_and_retried_soon(tmp_path, monkeypatch):
    storage = Storage(tmp_path / "t.db")
    t = tariffs_module.Tariffs(storage, lambda: (35.0, 8.0))
    t.save([DYNAMIC])
    now = 1_780_000_000 // 3600 * 3600
    payload = {"data": [{"start_timestamp": now * 1000, "end_timestamp": (now + 3600) * 1000, "marketprice": 100.0}]}
    monkeypatch.setattr(tariffs_module, "read_within", lambda *a, **k: ({}, json.dumps(payload).encode()))
    assert asyncio.run(t.refresh_prices(now=now)) == 4
    assert t.fetch_status("DE") == {"last_ok": now, "last_error": None, "failing_since": None, "error": None}

    monkeypatch.setattr(tariffs_module, "read_within", offline)
    later = now + FETCH_EVERY_S
    assert asyncio.run(t.refresh_prices(now=later)) == 0
    status = t.fetch_status("DE")
    assert status["last_ok"] == now and status["last_error"] == later and "unreachable" in status["error"]
    assert status["failing_since"] == later
    # not the full hour after an error: the owner who fixed the connection sees it working again soon
    assert asyncio.run(t.refresh_prices(now=later + RETRY_AFTER_ERROR_S - 1)) == 0
    asyncio.run(t.refresh_prices(now=later + RETRY_AFTER_ERROR_S))  # still offline: the first failure stays
    assert t.fetch_status("DE")["failing_since"] == later
    later += RETRY_AFTER_ERROR_S
    monkeypatch.setattr(tariffs_module, "read_within", lambda *a, **k: ({}, json.dumps(payload).encode()))
    asyncio.run(t.refresh_prices(now=later + RETRY_AFTER_ERROR_S))
    assert t.fetch_status("DE") == {"last_ok": later + RETRY_AFTER_ERROR_S, "last_error": None, "failing_since": None, "error": None}
    # the status survives a restart
    assert tariffs_module.Tariffs(storage, lambda: (35.0, 8.0)).fetch_status("DE")["last_ok"] == later + RETRY_AFTER_ERROR_S
    assert t.fetch_status("AT") == {"last_ok": None, "last_error": None, "failing_since": None, "error": None}


def test_prices_answer_says_whether_the_next_day_is_known(tmp_path, authed):
    storage = Storage(tmp_path / "t.db")
    runtime = Runtime({}, storage)
    client = authed(TestClient(create_app(runtime)))
    runtime.tariffs.save([DYNAMIC])
    day = datetime(2026, 6, 1, tzinfo=runtime.tz).timestamp()
    storage.save_prices([(int(day) + i * QUARTER, 80.0) for i in range(96)])

    answer = client.get("/api/prices?date=2026-06-01").json()
    assert len(answer["entries"]) == 96 and answer["next_day"] is False
    assert answer["fetch"] == {"last_ok": None, "last_error": None, "failing_since": None, "error": None}

    storage.save_prices([(int(day) + 86400 + 13 * 3600, 90.0)])
    assert client.get("/api/prices?date=2026-06-01").json()["next_day"] is True

    # a fixed tariff from tomorrow on: no exchange prices to switch to
    runtime.tariffs.save([DYNAMIC, {**DYNAMIC, "valid_from": "2026-06-02", "kind": "fixed", "price_ct": 30}])
    assert client.get("/api/prices?date=2026-06-01").json()["next_day"] is False
    runtime.tariffs.save([DYNAMIC])

    storage.set_meta("price_fetch", {"DE": {"last_ok": 1, "last_error": 2, "failing_since": 2, "error": "timed out"}})
    assert client.get("/api/prices?date=2026-06-01").json()["fetch"] == {
        "last_ok": 1, "last_error": 2, "failing_since": 2, "error": "timed out"}

    runtime.tariffs.save([{**DYNAMIC, "kind": "fixed", "price_ct": 30}])
    fixed = client.get("/api/prices?date=2026-06-01").json()
    assert fixed["next_day"] is False and fixed["fetch"] is None
