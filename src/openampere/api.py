"""HTTP/WebSocket API and static web app."""

from __future__ import annotations

import asyncio
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from fastapi import BackgroundTasks, Body, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import discovery, cloud_import
from .drivers import registry
from .control import (BatteryControl, ConfirmationRequired, ControlDisabled, ExportLimitControl, NotConnected,
                      WriteFailed)
from .drivers.base import Snapshot
from .periods import PERIODS, bucket_start, parse_anchor, period_bounds, to_ts
from .runtime import Runtime
from .storage import FLOWS, Storage

try:
    VERSION = version("openampere")
except PackageNotFoundError:  # running from source
    VERSION = "dev"

WEB_DIST = Path(__file__).parent / "web"
MAX_UPLOAD_BYTES = 500 * 1024 * 1024


def ratios(flows: dict) -> dict:
    """Autarky = share of consumption not drawn from the grid; self-consumption = share of PV used locally."""
    load, pv = flows.get("load") or 0, flows.get("pv") or 0
    grid_import, grid_export = flows.get("grid_import") or 0, flows.get("grid_export") or 0
    autarky = max(0.0, min(1.0, 1 - grid_import / load)) if load > 0 else None
    self_consumption = max(0.0, min(1.0, 1 - grid_export / pv)) if pv > 0 else None
    return {"autarky": autarky, "self_consumption": self_consumption}


class Target(BaseModel):
    host: str = Field(min_length=1)
    port: int = Field(502, ge=1, le=65535)
    unit: int = Field(0, ge=0, le=255)  # 0 = try the default ids of each device type
    driver: str = Field("auto", pattern="^(auto|foxess|saj)$")


class ExportLimitRequest(BaseModel):
    limit_w: int = Field(ge=0, le=99_999)
    grid_operator_confirmed: bool = False
    confirmation_reference: str = Field("", max_length=200)


class ScanRequest(BaseModel):
    prefix: str
    port: int = Field(502, ge=1, le=65535)
    unit: int = Field(0, ge=0, le=255)


def create_app(runtime: Runtime) -> FastAPI:
    storage, collector = runtime.storage, runtime.collector
    installation_id = storage_installation_id(storage)
    battery = BatteryControl(runtime)
    export_limit = ExportLimitControl(runtime)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        collector.start()
        runtime.cloud_import.resume_if_running()
        yield
        await runtime.cloud_import.stop(status="running")  # keeps running after the next start
        await collector.stop()

    app = FastAPI(title="OpenAmpere", lifespan=lifespan)

    # ---- live ------------------------------------------------------------

    @app.get("/api/status")
    def status():
        latest = collector.latest
        control = runtime.config.control
        return {
            "version": VERSION,
            "configured": collector.configured,
            "connected": collector.connected,
            "last_error": collector.last_error,
            "last_update": latest.timestamp if latest else None,
            "device": collector.device.__dict__ if collector.device else None,
            "control": {"enabled": control.enabled, "dry_run": control.dry_run},
        }

    # ---- settings & setup ------------------------------------------------

    @app.get("/api/settings")
    def get_settings():
        return runtime.settings_view()

    @app.put("/api/settings")
    async def put_settings(changes: dict = Body(...)):
        try:
            return await runtime.update_settings(changes)
        except PermissionError as err:
            raise HTTPException(409, str(err)) from None
        except (ValueError, KeyError) as err:
            raise HTTPException(400, str(err)) from None

    @app.get("/api/setup/networks")
    def setup_networks():
        return {"prefixes": discovery.local_prefixes()}

    @app.get("/api/setup/drivers")
    def setup_drivers():
        return {"drivers": [{"key": k, "label": v} for k, v in registry.LABELS.items()]}

    @app.post("/api/setup/scan")
    async def setup_scan(request: ScanRequest):
        try:
            return {"devices": await discovery.scan(runtime, request.prefix, request.port, request.unit)}
        except ValueError as err:
            raise HTTPException(400, str(err)) from None

    @app.post("/api/setup/test")
    async def setup_test(target: Target):
        return await discovery.test_connection(runtime, target.host.strip(), target.port, target.unit, target.driver)

    # ---- battery control -------------------------------------------------

    @app.get("/api/battery/settings")
    async def get_battery_settings():
        try:
            return await battery.read()
        except NotConnected as err:
            raise HTTPException(503, str(err)) from None
        except Exception as err:  # e.g. timeout through a proxy
            raise HTTPException(504, f"Wechselrichter antwortet nicht: {err}") from None

    @app.put("/api/battery/settings")
    async def put_battery_settings(changes: dict = Body(...)):
        try:
            return await battery.write(changes)
        except ControlDisabled as err:
            raise HTTPException(403, str(err)) from None
        except NotConnected as err:
            raise HTTPException(503, str(err)) from None
        except WriteFailed as err:
            raise HTTPException(502, str(err)) from None
        except ValueError as err:
            raise HTTPException(400, str(err)) from None

    @app.get("/api/grid/export-limit")
    async def get_export_limit():
        try:
            return await export_limit.read()
        except NotConnected as err:
            raise HTTPException(503, str(err)) from None
        except Exception as err:
            raise HTTPException(504, f"Wechselrichter antwortet nicht: {err}") from None

    @app.put("/api/grid/export-limit")
    async def put_export_limit(request: ExportLimitRequest):
        try:
            return await export_limit.write(request.limit_w, confirmed=request.grid_operator_confirmed,
                                            reference=request.confirmation_reference)
        except ControlDisabled as err:
            raise HTTPException(403, str(err)) from None
        except ConfirmationRequired as err:
            raise HTTPException(428, str(err)) from None
        except NotConnected as err:
            raise HTTPException(503, str(err)) from None
        except WriteFailed as err:
            raise HTTPException(502, str(err)) from None
        except ValueError as err:
            raise HTTPException(400, str(err)) from None

    @app.get("/api/control/log")
    def get_control_log(limit: int = Query(50, ge=1, le=500)):
        return {"entries": storage.control_log(limit)}

    # ---- history import from the former vendor cloud ---------------------

    @app.get("/api/import/cloud")
    def cloud_import_status():
        return {**runtime.cloud_import.view(), "key_set": bool(runtime.config.cloud.api_key)}

    @app.post("/api/import/cloud/start")
    async def cloud_import_start():  # async: the job is an asyncio task on the server's event loop
        try:
            runtime.cloud_import.start()
        except ValueError as err:
            raise HTTPException(400, str(err)) from None
        return cloud_import_status()

    @app.post("/api/import/cloud/stop")
    async def cloud_import_stop():
        await runtime.cloud_import.stop()
        return cloud_import_status()

    @app.post("/api/import/cloud/file")
    async def cloud_import_file(request: Request):
        data = await request.body()
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "Datei zu groß (max. 500 MB).")
        try:
            return await asyncio.to_thread(cloud_import.import_zip, storage, data)
        except ValueError as err:
            raise HTTPException(400, str(err)) from None

    # ---- data ------------------------------------------------------------

    @app.get("/api/backup")
    def backup(background: BackgroundTasks):
        tmp = Path(tempfile.mkdtemp()) / "openampere.db"
        storage.backup(tmp)
        background.add_task(lambda: (tmp.unlink(missing_ok=True), tmp.parent.rmdir()))
        name = time.strftime("openampere-backup-%Y-%m-%d.db")
        return FileResponse(tmp, filename=name, media_type="application/vnd.sqlite3")

    @app.get("/api/live")
    def live():
        if not collector.latest:
            raise HTTPException(503, "no data yet")
        return collector.latest.to_dict()

    @app.websocket("/api/live/ws")
    async def live_ws(ws: WebSocket):
        await ws.accept()
        queue = collector.subscribe()

        async def push():
            if collector.latest:
                await ws.send_json(collector.latest.to_dict())
            while True:
                snap: Snapshot = await queue.get()
                await ws.send_json(snap.to_dict())

        async def watch_close():
            # Returns as soon as the client disconnects or the server shuts down, even when no new
            # readings arrive (otherwise a stalled inverter would block a clean shutdown).
            while True:
                if (await ws.receive())["type"] == "websocket.disconnect":
                    return

        tasks = [asyncio.create_task(push()), asyncio.create_task(watch_close())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        except (WebSocketDisconnect, asyncio.CancelledError):
            pass
        finally:
            for task in tasks:
                task.cancel()
            collector.unsubscribe(queue)

    # ---- energy ----------------------------------------------------------

    def bounds(period: str, date: str | None) -> tuple[float, float]:
        tz = runtime.tz
        if period not in PERIODS:
            raise HTTPException(400, f"period must be one of {PERIODS}")
        try:
            start, end = period_bounds(period, parse_anchor(period, date, tz))
        except ValueError as err:
            raise HTTPException(400, str(err)) from None
        return to_ts(start, tz), to_ts(end, tz)

    @app.get("/api/energy/summary")
    def energy_summary(period: str = "day", date: str | None = None):
        start, end = bounds(period, date)
        flows = storage.energy_sum(start, end)
        quarters = flows.pop("quarters")
        latest = collector.latest
        # For the running day prefer the inverter's own daily counters (complete up to now)
        if period == "day" and latest and start <= latest.timestamp < end:
            today = latest.today.__dict__
            if all(today.get(f) is not None for f in FLOWS):
                flows = {f: today[f] for f in FLOWS}
        return {"period": period, "from": start, "to": end, "quarters": quarters,
                "energy_wh": flows, **ratios(flows)}

    @app.get("/api/energy/timeline")
    def energy_timeline(period: str = "day", date: str | None = None,
                        resolution: str = Query("15m", pattern="^(15m|60m|day|month)$")):
        start, end = bounds(period, date)
        buckets: dict[float, dict] = {}
        for row in storage.energy(start, end):
            key = bucket_start(row["ts"], resolution, runtime.tz)
            bucket = buckets.setdefault(key, {"ts": key, **dict.fromkeys(FLOWS, 0.0), "soc": None})
            for f in FLOWS:
                bucket[f] += row[f] or 0
            bucket["soc"] = row["soc"] if row["soc"] is not None else bucket["soc"]
        return {"period": period, "resolution": resolution, "from": start, "to": end,
                "entries": list(buckets.values())}

    @app.get("/api/power/timeline")
    def power_timeline(date: str | None = None, step: int = Query(60, ge=10, le=3600)):
        start, end = bounds("day", date)
        buckets: dict[float, list[dict]] = {}
        for row in storage.samples(start, end):
            buckets.setdefault(row["ts"] // step * step, []).append(row)
        entries = []
        for ts, rows in buckets.items():
            entry = {"ts": ts}
            for key in ("pv", "house", "grid", "battery", "soc"):
                values = [r[key] for r in rows if r[key] is not None]
                entry[key] = sum(values) / len(values) if values else None
            entries.append(entry)
        return {"from": start, "to": end, "step": step, "entries": entries}

    @app.get("/api/pv/inputs")
    def pv_inputs_timeline(period: str = "day", date: str | None = None, mode: str = Query("energy", pattern="^(energy|power)$"),
                           resolution: str = Query("60m", pattern="^(15m|60m|day|month)$")):
        """Per PV input (module array): energy per bucket, or for a day also the power curve."""
        start, end = bounds(period, date)
        names = runtime.config.pv.input_names
        if mode == "power":
            step = 300
            buckets: dict[float, list[dict]] = {}
            for row in storage.samples(start, end):
                buckets.setdefault(row["ts"] // step * step, []).append(row)
            entries = []
            for ts, rows in sorted(buckets.items()):
                values = []
                for i in range(1, 5):
                    column = [r.get(f"pv{i}") for r in rows if r.get(f"pv{i}") is not None]
                    values.append(sum(column) / len(column) if column else None)
                entries.append({"ts": ts, "values": values})
            count = max((i + 1 for e in entries for i, v in enumerate(e["values"]) if v is not None), default=0)
            entries = [{"ts": e["ts"], "values": e["values"][:count]} for e in entries]
        else:
            totals: dict[float, dict[int, float]] = {}
            for row in storage.pv_input_energy(start, end):
                key = bucket_start(row["ts"], resolution, runtime.tz)
                totals.setdefault(key, {})
                totals[key][row["input"]] = totals[key].get(row["input"], 0.0) + row["wh"]
            count = max((i for t in totals.values() for i in t), default=0)
            entries = [{"ts": ts, "values": [t.get(i, 0.0) for i in range(1, count + 1)]} for ts, t in sorted(totals.items())]
        labels = [names[i] if i < len(names) and names[i] else f"Modulfeld {i + 1}" for i in range(count)]
        sums = [sum(e["values"][i] or 0 for e in entries) for i in range(count)] if mode == "energy" else None
        return {"mode": mode, "labels": labels, "entries": entries, "totals_wh": sums}

    @app.get("/api/temperatures/timeline")
    def temperatures_timeline(date: str | None = None, step: int = Query(300, ge=60, le=3600)):
        start, end = bounds("day", date)
        buckets: dict[float, list[dict]] = {}
        for row in storage.samples(start, end):
            buckets.setdefault(row["ts"] // step * step, []).append(row)
        entries = []
        for ts, rows in sorted(buckets.items()):
            entry = {"ts": ts}
            for key, column in (("inverter", "t_inverter"), ("battery", "t_battery")):
                values = [r[column] for r in rows if r.get(column) is not None]
                entry[key] = sum(values) / len(values) if values else None
            entries.append(entry)
        return {"entries": entries}

    # ---- compatibility with the former cloud customer API (read-only subset)

    @app.get("/api/v1/customer/installation")
    def cloud_installations():
        return [{"uuid": installation_id}]

    @app.get("/api/v1/installation/{installation}/now/all/power")
    def cloud_now(installation: str):
        if installation != installation_id:
            raise HTTPException(404, "installation not found")
        s = collector.latest
        if not s:
            raise HTTPException(503, "no data yet")
        neg = (lambda v: -v if v is not None else None)
        return {"pvPower": s.pv_power, "housePower": neg(s.house_power), "gridPower": s.grid_power,
                "batteryPower": s.battery_power, "batterySoc": s.battery_soc}

    # ---- web app ---------------------------------------------------------

    if (WEB_DIST / "index.html").is_file():
        app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            file = (WEB_DIST / path).resolve()
            if path and file.is_file() and WEB_DIST.resolve() in file.parents:
                return FileResponse(file)
            return FileResponse(WEB_DIST / "index.html")

    return app


def storage_installation_id(storage: Storage) -> str:
    """Random, stable id for the cloud-compatible API (unrelated to any cloud id)."""
    value = storage.get_meta("installation_id")
    if not value:
        value = str(uuid.uuid4())
        storage.set_meta("installation_id", value)
    return value
