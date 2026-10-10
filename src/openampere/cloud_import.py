# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Import the history of an installation from the EKD cloud (customer API of the Ampere.IQ app) into the local
database. OpenAmpere is not affiliated with EKD; this only uses the customer API with the user's own key.

Two ways in:
  - online: the vendor's official customer API with the user's API key (while that cloud still runs).
    The API blocks clients that poll too often, so at most one request per minute is sent; the job
    runs in the background, survives restarts and resumes where it stopped.
  - offline: a ZIP file created by tools/cloud-export (same JSON format).

Local measurements always win: imported rows never overwrite quarter hours recorded by OpenAmpere.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from datetime import date, datetime, timedelta

from .drivers.base import raise_if_cancelled
from .storage import Storage

log = logging.getLogger(__name__)

CLIENT_TYPE = "de.ekd.customer.apiclient"  # protocol constant required by the customer API
MIN_INTERVAL_S = 65
MAX_UNPACKED_BYTES = 2 * 1024 ** 3  # ZIP import: limits against "zip bombs"
MAX_ENTRY_BYTES = 50 * 1024 ** 2
BACKOFF_S = 300
MAX_NETWORK_FAILURES = 12  # ~1 h of retries before giving up (e.g. cloud switched off)

# cloud "work" history -> local energy flows (Wh per quarter hour)
WORK_FIELDS = {
    "generation": "pv",
    "consumption": "load",
    "gridDraw": "grid_import",
    "gridFeed": "grid_export",
    "batteryFeed": "battery_charge",
    "batteryDraw": "battery_discharge",
}


def _ts(iso: str) -> int:
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())


def work_rows(body: dict) -> list[dict]:
    rows: dict[int, dict] = {}
    for field, flow in WORK_FIELDS.items():
        for entry in (body.get(field) or {}).get("timeline") or []:
            if entry.get("value") is None:
                continue
            row = rows.setdefault(_ts(entry["fromTimestamp"]), {"ts": _ts(entry["fromTimestamp"])})
            row[flow] = float(entry["value"])
    return [r for r in rows.values() if len(r) > 1]


def soc_values(body: dict) -> list[tuple[int, float]]:
    timeline = body.get("timeline") if isinstance(body, dict) else None
    return [(_ts(e["fromTimestamp"]), float(e["value"])) for e in timeline or [] if e.get("value") is not None]


def import_zip(storage: Storage, data: bytes | str | Path) -> dict:
    """Reads */work/*.json and */stateOfCharge/*.json from an export ZIP (its bytes or a file)."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data) if isinstance(data, bytes) else data)
    except zipfile.BadZipFile:
        raise ValueError("Das ist keine gültige ZIP-Datei.") from None
    total = sum(info.file_size for info in archive.infolist())
    if total > MAX_UNPACKED_BYTES or any(info.file_size > MAX_ENTRY_BYTES for info in archive.infolist()):
        raise ValueError("Die ZIP-Datei ist zu groß.")
    rows, socs, days = [], [], 0
    for name in archive.namelist():
        parts = name.replace("\\", "/").split("/")
        if len(parts) < 2 or not parts[-1].endswith(".json"):
            continue
        folder = parts[-2]
        if folder not in ("work", "stateOfCharge"):
            continue
        try:
            payload = json.loads(archive.read(name))
        except (ValueError, KeyError, zipfile.BadZipFile):  # also a damaged entry (CRC error), #223
            continue
        if not isinstance(payload, dict):
            continue
        body = payload.get("body") if "body" in payload else payload
        if payload.get("status", 200) != 200 or not isinstance(body, dict):
            continue
        try:  # one day with unexpected content is skipped, not the whole import (#223)
            if folder == "work":
                rows += work_rows(body)
                days += 1
            else:
                socs += soc_values(body)
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    if not days:
        raise ValueError("In der ZIP-Datei wurden keine Verlaufsdaten gefunden (Ordner „work“).")
    inserted = storage.import_energy(rows, "cloud")
    storage.import_soc(socs)
    return {"days": days, "quarters": len(rows), "inserted": inserted}


class CloudApiError(Exception):
    def __init__(self, message: str, *, retry: bool) -> None:
        super().__init__(message)
        self.retry = retry


class CloudImport:
    """Background import through the cloud customer API. State lives in the database (meta table)."""

    def __init__(self, storage: Storage, get_settings) -> None:
        self.storage = storage
        self.get_settings = get_settings  # () -> (api_key, base_url)
        self._task: asyncio.Task | None = None
        self.state = storage.get_meta("cloud_import") or {"status": "idle"}

    # ---- state -----------------------------------------------------------

    def _save(self, **changes) -> None:
        self.state = {**self.state, **changes, "updated": time.time()}
        self.storage.set_meta("cloud_import", self.state)

    def view(self) -> dict:
        s = dict(self.state)
        s.pop("installation", None)  # internal cloud id, never shown
        remaining = s.get("days_total", 0) * 2 - s.get("work_done", 0) - s.get("soc_done", 0)
        s["eta_seconds"] = max(0, remaining) * MIN_INTERVAL_S if s.get("status") == "running" else None
        return s

    # ---- control ---------------------------------------------------------

    def start(self) -> None:
        if not self.get_settings()[0]:
            raise ValueError("Bitte zuerst den API-Schlüssel hinterlegen.")
        if self._task and not self._task.done():
            return
        if self.state.get("status") == "done":
            self.state = {"status": "idle"}  # run again from scratch (fills gaps, adds new days)
        # otherwise ("paused", "error") resume from the saved position
        task = asyncio.create_task(self._run(), name="cloud-import")  # fails before any state change
        self._task = task
        self._save(status="running", error=None, notice=None)

    def reset(self) -> None:
        """Forget progress, e.g. after the API key changed (may belong to another installation)."""
        self.state = {"status": "idle"}
        self._save()

    async def stop(self, status: str = "paused") -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        if self.state.get("status") == "running":
            self._save(status=status)

    def resume_if_running(self) -> None:
        if self.state.get("status") == "running" and self.get_settings()[0]:
            self._task = asyncio.create_task(self._run(), name="cloud-import")

    # ---- cloud API -------------------------------------------------------

    def _request(self, path: str, params: dict | None = None):
        api_key, base_url = self.get_settings()
        url = base_url.rstrip("/") + path + ("?" + urllib.parse.urlencode(params) if params else "")
        req = urllib.request.Request(url, headers={
            "x-client-api-key": api_key, "x-client-type": CLIENT_TYPE, "accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return response.status, json.loads(response.read() or b"null")
        except urllib.error.HTTPError as err:
            if err.code == 401:
                raise CloudApiError("Der API-Schlüssel wurde von der EKD-Cloud abgelehnt.", retry=False) from None
            if err.code in (400, 404):
                return err.code, None
            log.warning("cloud API %s: HTTP %s", path.split("/")[-1], err.code)
            message = ("Die EKD-Cloud bremst zu viele Anfragen." if err.code in (403, 429)
                       else f"Die EKD-Cloud meldet einen Fehler ({err.code}).")
            raise CloudApiError(message, retry=True) from None
        except (urllib.error.URLError, TimeoutError, ConnectionError, ValueError) as err:
            log.warning("cloud API not reachable: %s", err)  # details only in the log, never the key
            raise CloudApiError("Die EKD-Cloud ist nicht erreichbar.", retry=True) from None

    async def _get(self, path: str, params: dict | None = None):
        """One request with rate limiting and back-off; the pacing survives restarts."""
        failures = 0
        while True:
            wait = self.state.get("last_request", 0) + MIN_INTERVAL_S - time.time()
            if wait > 0:
                await asyncio.sleep(wait)
            self._save(last_request=time.time())
            try:
                result = await asyncio.to_thread(self._request, path, params)
                if failures:
                    self._save(notice=None)
                return result
            except CloudApiError as err:
                raise_if_cancelled()
                if not err.retry:
                    raise
                failures += 1
                if failures >= MAX_NETWORK_FAILURES:
                    raise
                self._save(notice=f"{err} Neuer Versuch in {BACKOFF_S // 60} Minuten.")
                await asyncio.sleep(BACKOFF_S)

    # ---- job -------------------------------------------------------------

    async def _run(self) -> None:
        try:
            await self._run_steps()
            self._save(status="done", notice=None, finished=time.time())
            log.info("cloud import finished")
        except asyncio.CancelledError:
            raise
        except CloudApiError as err:
            self._save(status="error", error=str(err))
        except Exception as err:  # never take the app down
            log.exception("cloud import failed")
            self._save(status="error", error=f"Unerwarteter Fehler: {err}")

    async def _run_steps(self) -> None:
        if not self.state.get("installation"):
            status, body = await self._get("/api/v1/customer/installation")
            if status != 200 or not body:
                raise CloudApiError("Zu diesem API-Schlüssel wurde keine Anlage gefunden.", retry=False)
            self._save(installation=body[0]["uuid"], installations=len(body))
        base = f"/api/v1/installation/{self.state['installation']}"

        if not self.state.get("start"):
            self._save(phase="search")
            start = await self._find_start(base)
            end = date.today() - timedelta(days=1)
            self._save(start=start.isoformat(), end=end.isoformat(), days_total=(end - start).days + 1,
                       work_cursor=end.isoformat(), soc_cursor=end.isoformat(), work_done=0, soc_done=0,
                       imported=0)

        start = date.fromisoformat(self.state["start"])
        # 1) energy flows, newest day first so recent history is available quickly
        self._save(phase="work")
        while (day := date.fromisoformat(self.state["work_cursor"])) >= start:
            status, body = await self._get(base + "/history/common/work",
                                           {"period": "day", "date": day.isoformat(), "resolution": "15m"})
            inserted = 0
            if status == 200 and isinstance(body, dict):
                inserted = await asyncio.to_thread(self.storage.import_energy, work_rows(body), "cloud")
            self._save(work_cursor=(day - timedelta(days=1)).isoformat(), work_done=self.state["work_done"] + 1,
                       imported=self.state["imported"] + inserted)
        # 2) battery state of charge
        self._save(phase="soc")
        while (day := date.fromisoformat(self.state["soc_cursor"])) >= start:
            status, body = await self._get(base + "/history/stateOfCharge",
                                           {"period": "day", "date": day.isoformat(), "resolution": "15m"})
            if status == 200:
                await asyncio.to_thread(self.storage.import_soc, soc_values(body))
            self._save(soc_cursor=(day - timedelta(days=1)).isoformat(), soc_done=self.state["soc_done"] + 1)

    async def _find_start(self, base: str) -> date:
        def has_data(body) -> bool:
            if isinstance(body, (int, float)) and not isinstance(body, bool):
                return body != 0
            if isinstance(body, dict):
                return any(has_data(v) for k, v in body.items() if k not in ("date", "period"))
            if isinstance(body, list):
                return any(has_data(v) for v in body)
            return False

        year, first_year = date.today().year, None
        while year >= 2015:
            status, body = await self._get(base + "/total/common/work", {"period": "year", "date": f"{year}-01-01"})
            if status == 200 and has_data(body):
                first_year, year = year, year - 1
            else:
                break
        if first_year is None:
            raise CloudApiError("In der EKD-Cloud wurden keine Verlaufsdaten gefunden.", retry=False)
        for month in range(1, 13):
            first = date(first_year, month, 1)
            if first > date.today():
                break
            status, body = await self._get(base + "/total/common/work", {"period": "month", "date": first.isoformat()})
            if status == 200 and has_data(body):
                return first
        return date(first_year, 1, 1)
