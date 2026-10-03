"""HTTP/WebSocket API and static web app."""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import logging
import shutil
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version
from dataclasses import asdict
from pathlib import Path

from fastapi import BackgroundTasks, Body, FastAPI, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import discovery, cloud_import, health
from .auth import CSRF_HEADER, SESSION_COOKIE, SESSION_TTL_S, Auth, host_allowed
from .config import SECRETS
from .drivers import registry
from .control import (BatteryControl, ConfirmationRequired, ControlDisabled, ExportLimitControl, NotConnected,
                      WriteFailed)
from .drivers.base import Snapshot
from .periods import PERIODS, bucket_start, parse_anchor, period_bounds, to_ts
from .runtime import Runtime
from .storage import FLOWS, Storage
from .discovery import Rediscovery
from .billing import Billing
from .charging import GridCharging
from .consumers import SurplusControl
from .evcc import Evcc, EvccError
from .devices import Devices
from .notify import Notifier
from .updates import Updates
from .diagnostics import Diagnostics, report_markdown

log = logging.getLogger(__name__)

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


class PasswordRequest(BaseModel):
    password: str = Field(min_length=1, max_length=200)


class ChangePasswordRequest(BaseModel):
    current: str = Field(min_length=1, max_length=200)
    new: str = Field(min_length=1, max_length=200)


# reading these needs a login as well (secrets, grid-operator references)
PROTECTED_READS = ("/api/backup", "/api/control/log")
PUBLIC_WRITES = ("/api/auth/login", "/api/auth/setup", "/api/auth/logout")


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

    rediscovery = Rediscovery(runtime)
    charging = GridCharging(runtime)
    billing = Billing(runtime.storage, runtime.tariffs)
    evcc = Evcc(runtime)
    surplus = SurplusControl(runtime, evcc)
    devices = Devices(runtime, surplus, evcc)
    notifier = Notifier(runtime)
    diagnostics = Diagnostics(runtime)
    updates = Updates(runtime, VERSION)

    async def watchdog() -> None:
        """Background jobs: find the inverter after an IP change, exchange prices, grid charging."""
        while True:
            await asyncio.sleep(30)
            for job in (lambda: rediscovery.check(time.time()), runtime.tariffs.refresh_prices, charging.tick,
                        notifier.check, updates.tick):
                try:
                    await job()
                except asyncio.CancelledError:
                    raise
                except Exception as err:  # noqa: BLE001 - never stop the watchdog
                    log.warning("background job failed: %s", err)

    async def fast_loop() -> None:
        """Every few seconds: read evcc and share the solar surplus (the heating rod follows the sun)."""
        while True:
            await asyncio.sleep(max(5.0, min(runtime.config.inverter.poll_interval, 15.0)))
            for job in (evcc.refresh, surplus.tick, devices.record_async):
                try:
                    await job()
                except asyncio.CancelledError:
                    raise
                except Exception as err:  # noqa: BLE001 - never stop the loop
                    log.warning("surplus control failed: %s", err)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        collector.start()
        runtime.cloud_import.resume_if_running()
        watchdog_task = asyncio.create_task(watchdog())
        fast_task = asyncio.create_task(fast_loop())
        yield
        watchdog_task.cancel()
        fast_task.cancel()
        await surplus.stop()
        await charging.stop("OpenAmpere wird beendet")
        await runtime.cloud_import.stop(status="running")  # keeps running after the next start
        await collector.stop()

    app = FastAPI(title="OpenAmpere", lifespan=lifespan)
    auth = Auth(storage)

    def origin_ok(origin: str | None, host: str | None) -> bool:
        """Requests from other web sites carry their own Origin; same-origin requests match the Host."""
        if not origin or origin == "null":
            return origin is None
        return origin.split("://", 1)[-1].rstrip("/").lower() == (host or "").lower()

    @app.middleware("http")
    async def security(request: Request, call_next):
        host = request.headers.get("host")
        if not host_allowed(host, runtime.config.server.allowed_hosts):
            return JSONResponse({"detail": f"Zugriff über „{host}“ ist nicht erlaubt. Öffne OpenAmpere über die "
                                           "IP-Adresse oder trage den Namen unter server.allowed_hosts ein."}, 421)
        path = request.url.path
        writing = request.method not in ("GET", "HEAD", "OPTIONS")
        if path.startswith("/api/") and (writing or path in PROTECTED_READS):
            if writing and (not origin_ok(request.headers.get("origin"), host) or request.headers.get(CSRF_HEADER) != "1"):
                return JSONResponse({"detail": "Anfrage abgelehnt (fremde Herkunft)."}, 403)
            if path not in PUBLIC_WRITES:
                if not auth.configured:
                    return JSONResponse({"detail": "Bitte zuerst ein Passwort festlegen.", "code": "setup_required"}, 401)
                if not auth.valid(request.cookies.get(SESSION_COOKIE)):
                    return JSONResponse({"detail": "Bitte anmelden.", "code": "login_required"}, 401)
        return await call_next(request)

    def start_session(response: Response) -> None:
        response.set_cookie(SESSION_COOKIE, auth.create_session(), max_age=SESSION_TTL_S, httponly=True,
                            samesite="strict", path="/")

    # ---- access protection ---------------------------------------------------

    @app.get("/api/auth/status")
    def auth_status(request: Request):
        return {"configured": auth.configured, "authenticated": auth.valid(request.cookies.get(SESSION_COOKIE))}

    @app.post("/api/auth/setup")
    def auth_setup(body: PasswordRequest, response: Response):
        if auth.configured:
            raise HTTPException(409, "Es ist bereits ein Passwort festgelegt.")
        try:
            auth.set_password(body.password)
        except ValueError as err:
            raise HTTPException(400, str(err)) from None
        start_session(response)
        return {"configured": True, "authenticated": True}

    @app.post("/api/auth/login")
    def auth_login(body: PasswordRequest, response: Response):
        try:
            ok = auth.check_password(body.password)
        except PermissionError as err:
            raise HTTPException(429, str(err)) from None
        if not ok:
            raise HTTPException(401, "Falsches Passwort.")
        start_session(response)
        return {"configured": True, "authenticated": True}

    @app.post("/api/auth/logout")
    def auth_logout(request: Request, response: Response, everywhere: bool = False):
        token = request.cookies.get(SESSION_COOKIE)
        # logging out every device is only allowed for a logged-in device
        auth.revoke(token, everywhere=everywhere and auth.valid(token))
        response.delete_cookie(SESSION_COOKIE, path="/")
        return {"authenticated": False}

    @app.post("/api/auth/password")
    def auth_change_password(body: ChangePasswordRequest, request: Request, response: Response):
        try:
            if not auth.check_password(body.current):
                raise HTTPException(401, "Das bisherige Passwort stimmt nicht.")
            auth.set_password(body.new)
        except PermissionError as err:
            raise HTTPException(429, str(err)) from None
        except ValueError as err:
            raise HTTPException(400, str(err)) from None
        auth.revoke(None, everywhere=True)  # other devices must log in again
        start_session(response)
        return {"authenticated": True}

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
            "stale": collector.stale,
            "timezone": runtime.config.timezone,
            "clock_wrong": storage.clock_wrong,
            "web_build": WEB_BUILD,
            "relocated": storage.get_meta("relocated"),
            "devices": {"grid_charging": charging.active, "items": [d for d in devices.live() if d["enabled"]]},
            "poll_interval": collector.interval,
            "device": collector.device.__dict__ if collector.device else None,
            "firmware": {**(storage.get_meta("firmware") or {}), "history": storage.get_meta("firmware_history") or []},
            "control": {"enabled": control.enabled, "dry_run": control.dry_run},
        }

    # ---- settings & setup ------------------------------------------------

    @app.get("/api/settings")
    def get_settings():
        return runtime.settings_view()

    @app.put("/api/settings")
    async def put_settings(changes: dict = Body(...)):
        expected = changes.pop("_revision", None)
        if expected is not None and expected != runtime.settings_revision:
            raise HTTPException(409, "Die Einstellungen wurden inzwischen auf einem anderen Gerät geändert. "
                                     "Bitte die Seite neu laden und die Änderung wiederholen.")
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

    @app.get("/api/battery/health")
    def battery_health():
        return health.battery(storage, collector.latest, runtime.config.battery.capacity_kwh)

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
        except Exception:  # reading the current values failed, e.g. timeout through a proxy
            raise HTTPException(504, "Der Wechselrichter antwortet nicht. Bitte später erneut versuchen.") from None

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
        except Exception:
            raise HTTPException(504, "Der Wechselrichter antwortet nicht. Bitte später erneut versuchen.") from None

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
        storage.backup(tmp, drop_settings=SECRETS)
        background.add_task(shutil.rmtree, tmp.parent, ignore_errors=True)  # incl. SQLite side files
        name = time.strftime("openampere-backup-%Y-%m-%d.db")
        return FileResponse(tmp, filename=name, media_type="application/vnd.sqlite3")

    @app.get("/api/live")
    def live():
        if not collector.latest:
            raise HTTPException(503, "Noch keine Messwerte")
        return collector.latest.to_dict()

    @app.websocket("/api/live/ws")
    async def live_ws(ws: WebSocket):
        # live data reveals presence at home: only same-origin pages may subscribe
        host = ws.headers.get("host")
        origin = ws.headers.get("origin")
        if not host_allowed(host, runtime.config.server.allowed_hosts) or (origin is not None and not origin_ok(origin, host)):
            await ws.close(code=1008)
            return
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
            raise HTTPException(400, "Unbekannter Zeitraum")
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
        running = storage.running_quarter(latest)
        partial_since = None
        if running and start <= running["ts"] < end:
            # stored quarters + the quarter hour that is still running = exactly what the counters say
            flows = {f: (flows[f] or 0) + running[f] for f in FLOWS}
            quarters += 1
            first = storage.first_quarter(start, end) or running["ts"]
            if period == "day" and first > start + 900:
                # first day: recording started during the day; the inverter's daily counters are complete
                today = latest.today.__dict__
                if all(today.get(f) is not None for f in FLOWS) and today["pv"] >= flows["pv"]:
                    flows = {f: today[f] for f in FLOWS}
                else:
                    partial_since = first
        extra = [running] if running and start <= running["ts"] < end else []
        return {"period": period, "from": start, "to": end, "quarters": quarters,
                "partial_since": partial_since, "energy_wh": flows, **ratios(flows),
                "money": runtime.tariffs.money(start, end, runtime.tz, extra)}

    # ---- tariffs & exchange prices ------------------------------------------

    @app.get("/api/tariffs")
    def get_tariffs():
        return {"tariffs": [asdict(t) for t in runtime.tariffs.all()]}

    @app.get("/api/update")
    def get_update():
        return updates.view()

    @app.post("/api/update")
    async def post_update(body: dict = Body(...)):
        if body.get("action") == "check":
            await updates.check(force=True)
        elif body.get("action") == "install":
            if not updates.available:
                raise HTTPException(409, "Es gibt keine neuere Version.")
            try:
                updates.request()
            except RuntimeError as err:
                raise HTTPException(409, str(err)) from None
        else:
            raise HTTPException(400, "Unbekannte Aktion.")
        return updates.view()

    @app.get("/api/billing")
    def get_billing():
        return {"settings": billing.settings(), "status": billing.status(runtime.tz)}

    @app.put("/api/billing")
    def put_billing(body: dict = Body(...)):
        try:
            billing.save(body.get("settings") or {})
        except ValueError as err:
            raise HTTPException(400, str(err)) from None
        return get_billing()

    @app.put("/api/tariffs")
    async def put_tariffs(body: dict = Body(...)):
        try:
            tariffs = runtime.tariffs.save(body.get("tariffs"))
        except ValueError as err:
            raise HTTPException(400, str(err)) from None
        await runtime.tariffs.refresh_prices()
        return {"tariffs": [asdict(t) for t in tariffs]}

    @app.get("/api/charging")
    def get_charging():
        return charging.view()

    @app.put("/api/charging")
    async def put_charging(body: dict = Body(...)):
        try:
            charging.save(body)
        except ValueError as err:
            raise HTTPException(400, str(err)) from None
        await charging.tick()
        return charging.view()

    @app.get("/api/devices")
    def get_devices():
        return {"devices": devices.live(), "today_wh": devices.today_wh(),
                "evcc": {"configured": evcc.configured, "error": evcc.error},
                "wallbox_first": surplus.wallbox_first()}

    @app.get("/api/devices/energy")
    def devices_energy(period: str = "day", date: str | None = None,
                       resolution: str = Query("60m", pattern="^(15m|60m|day|month)$")):
        start, end = bounds(period, date)
        return {"period": period, "resolution": resolution, "from": start, "to": end,
                **devices.energy(start, end, resolution)}

    @app.get("/api/devices/power")
    def devices_power(date: str | None = None, step: int = Query(300, ge=60, le=3600)):
        start, end = bounds("day", date)
        return {"from": start, "to": end, **devices.power(start, end, step)}

    def order_view() -> dict:
        o = surplus.order()
        names = {f"c:{c.id}": c for c in surplus.consumers}
        lps = (evcc.fresh() or {}).get("loadpoints", [])
        items = []
        for key in o["order"]:
            if key == "battery":
                items.append({"key": key, "name": "Speicher", "kind": "battery"})
            elif key == "wallbox":
                if evcc.configured:
                    items.append({"key": key, "name": " / ".join(lp["title"] for lp in lps if not lp["heating"]) or "Wallbox",
                                  "kind": "wallbox"})
            else:
                c = names[key]
                items.append({"key": key, "name": c.name, "kind": "heating_rod" if c.adjustable else "switch"})
        return {"items": items, "order": o["order"], "battery_soc": o["battery_soc"]}

    @app.get("/api/surplus-order")
    def get_surplus_order():
        return order_view()

    @app.put("/api/surplus-order")
    async def put_surplus_order(body: dict = Body(...)):
        order = list(body.get("order") or [])
        if "wallbox" not in order:  # the app hides the wallbox when evcc is not set up
            order.insert(order.index("battery") + 1 if "battery" in order else 0, "wallbox")
        try:
            surplus.save_order(order, body.get("battery_soc", 50))
        except (TypeError, ValueError) as err:
            raise HTTPException(400, str(err)) from None
        result = order_view()
        if evcc.configured:
            try:
                await evcc.set_priority_soc(surplus.evcc_priority_soc())
                result["evcc_synced"] = True
            except EvccError as err:
                result["evcc_synced"], result["evcc_error"] = False, str(err)
        return result

    @app.post("/api/consumers/{consumer_id}/mode")
    async def consumer_mode(consumer_id: str, body: dict = Body(...)):
        try:
            surplus.set_override(consumer_id, str(body.get("mode")), body.get("hours"))
        except ValueError as err:
            raise HTTPException(400, str(err)) from None
        await surplus.tick()
        return {"devices": devices.live()}

    @app.get("/api/consumers")
    def get_consumers():
        return surplus.view()

    @app.put("/api/consumers")
    def put_consumers(body: dict = Body(...)):
        try:
            surplus.save(body.get("consumers"))
        except ValueError as err:
            raise HTTPException(400, str(err)) from None
        return surplus.view()

    @app.post("/api/consumers/{consumer_id}/switch")
    async def switch_consumer(consumer_id: str, on: bool):
        """Manual switch, e.g. to test the connection to a Shelly."""
        consumer = next((c for c in surplus.consumers if c.id == consumer_id), None)
        if consumer is None:
            raise HTTPException(404, "Verbraucher nicht gefunden")
        if not runtime.config.control.enabled:
            raise HTTPException(403, "Steuerung ist deaktiviert")
        await surplus.switch(consumer, on, "von Hand geschaltet", time.time())
        return surplus.view()

    @app.post("/api/notify/test")
    async def notify_test():
        if not runtime.config.notify.ntfy_url:
            raise HTTPException(400, "Bitte zuerst eine ntfy-Adresse eintragen.")
        if not await notifier.push("OpenAmpere", "Test: Benachrichtigungen funktionieren.", "tada"):
            raise HTTPException(502, notifier.last_error or "Senden fehlgeschlagen")
        return {"ok": True}

    @app.get("/api/diagnostics")
    def get_diagnostics():
        return {"running": diagnostics.running, "report": diagnostics.last,
                "markdown": report_markdown(diagnostics.last) if diagnostics.last else None}

    @app.post("/api/diagnostics")
    async def run_diagnostics(connection_test: bool = False, include_serial: bool = False):
        try:
            report = await diagnostics.run(connection_test=connection_test, include_serial=include_serial)
        except RuntimeError as err:
            raise HTTPException(409, str(err)) from None
        return {"running": False, "report": report, "markdown": report_markdown(report)}

    # ---- evcc (wallbox) -----------------------------------------------------

    @app.get("/api/evcc")
    async def get_evcc(refresh: bool = False):
        if refresh:
            await evcc.refresh()
        return evcc.view()

    @app.post("/api/evcc/loadpoints/{lp_id}")
    async def evcc_command(lp_id: int, body: dict = Body(...)):
        try:
            return await evcc.command(lp_id, str(body.get("action")), body.get("value"))
        except ValueError as err:
            raise HTTPException(400, str(err)) from None
        except EvccError as err:
            raise HTTPException(502, str(err)) from None

    @app.get("/api/evcc/sessions")
    async def evcc_sessions(limit: int = Query(50, ge=1, le=500)):
        try:
            return {"sessions": await evcc.sessions(limit)}
        except EvccError as err:
            raise HTTPException(502, str(err)) from None

    @app.get("/api/evcc/site")
    def evcc_site():
        """Site meters for evcc ("custom" meters with source http): one inverter connection is enough.
        Signs as in evcc: grid + = import, battery + = discharging."""
        snap = collector.latest
        if snap is None or collector.stale:
            raise HTTPException(503, "Keine aktuellen Messwerte vom Wechselrichter")
        return {"grid_power": snap.grid_power, "pv_power": snap.pv_power, "battery_power": snap.battery_power,
                "battery_soc": snap.battery_soc, "grid_import_kwh": (snap.totals.grid_import or 0) / 1000 or None,
                "grid_export_kwh": (snap.totals.grid_export or 0) / 1000 or None,
                "pv_kwh": (snap.totals.pv or 0) / 1000 or None, "timestamp": snap.timestamp}

    @app.get("/api/prices")
    def get_prices(date: str | None = None):
        """Import price per quarter hour of a day (ct/kWh gross) for the tariff valid that day."""
        start, end = bounds("day", date)
        day = datetime.datetime.fromtimestamp(start, runtime.tz).date().isoformat()
        tariff = runtime.tariffs.at(day)
        prices = storage.prices(start, end)
        entries = [{"ts": ts, "ct": round(tariff.import_price_ct(p), 2), "exchange_eur_mwh": p} for ts, p in prices.items()]
        return {"kind": tariff.kind, "feed_in_ct": tariff.feed_in_ct, "fixed_ct": tariff.price_ct if tariff.kind == "fixed" else None,
                "entries": entries if tariff.kind == "dynamic" else []}

    @app.get("/api/export/csv")
    def export_csv(start: str = Query(..., alias="from", pattern=r"^\d{4}-\d{2}-\d{2}$"),
                   end: str = Query(..., alias="to", pattern=r"^\d{4}-\d{2}-\d{2}$"),
                   resolution: str = Query("day", pattern="^(15m|60m|day|month)$")):
        """Energy per period as CSV for Excel & Co. (semicolon, decimal comma, local time)."""
        tz = runtime.tz
        try:
            first = datetime.date.fromisoformat(start)
            last = datetime.date.fromisoformat(end)
        except ValueError:
            raise HTTPException(400, "Ungültiges Datum") from None
        if last < first:
            raise HTTPException(400, "Das Enddatum liegt vor dem Startdatum")
        t0 = to_ts(first, tz)
        t1 = to_ts(last + datetime.timedelta(days=1), tz)
        buckets: dict[float, dict] = {}
        for row in storage.energy(t0, t1):
            key = bucket_start(row["ts"], resolution, tz)
            bucket = buckets.setdefault(key, {**dict.fromkeys(FLOWS, 0.0), "soc": None, "sources": set()})
            for f in FLOWS:
                bucket[f] += row[f] or 0
            bucket["soc"] = row["soc"] if row["soc"] is not None else bucket["soc"]
            bucket["sources"].add(row["source"] or "local")
        fmt = {"15m": "%d.%m.%Y %H:%M", "60m": "%d.%m.%Y %H:%M", "day": "%d.%m.%Y", "month": "%m.%Y"}[resolution]
        num = lambda v: "" if v is None else f"{v:.3f}".replace(".", ",")  # noqa: E731
        lines = ["Zeit;Erzeugung (kWh);Verbrauch (kWh);Netzbezug (kWh);Einspeisung (kWh);"
                 "Speicher geladen (kWh);Speicher entladen (kWh);Ladestand (%);Quelle"]
        for key in sorted(buckets):
            b = buckets[key]
            when = datetime.datetime.fromtimestamp(key, tz).strftime(fmt)
            source = "+".join(sorted({"local": "OpenAmpere", "cloud": "Cloud-Import"}.get(x, x) for x in b["sources"]))
            lines.append(";".join([when, *(num(b[f] / 1000) for f in FLOWS),
                                   "" if b["soc"] is None else f"{b['soc']:.0f}", source]))
        body = "\ufeff" + "\r\n".join(lines) + "\r\n"  # BOM: Excel detects UTF-8 (umlauts)
        name = f"openampere-{start}-bis-{end}-{resolution}.csv"
        return Response(body, media_type="text/csv; charset=utf-8",
                         headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @app.get("/api/energy/timeline")
    def energy_timeline(period: str = "day", date: str | None = None,
                        resolution: str = Query("15m", pattern="^(15m|60m|day|month)$")):
        start, end = bounds(period, date)
        buckets: dict[float, dict] = {}
        rows = storage.energy(start, end)
        running = storage.running_quarter(collector.latest)
        if running and start <= running["ts"] < end:
            rows.append(running)  # show the running quarter hour too
        for row in rows:
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
            for key, column in (("inverter", "t_inverter"), ("battery", "t_battery"), ("cell_max", "t_cell_max"),
                                ("cell_min", "t_cell_min")):
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
            raise HTTPException(503, "Noch keine Messwerte")
        neg = (lambda v: -v if v is not None else None)
        return {"pvPower": s.pv_power, "housePower": neg(s.house_power), "gridPower": s.grid_power,
                "batteryPower": s.battery_power, "batterySoc": s.battery_soc}

    # ---- web app ---------------------------------------------------------

    if (WEB_DIST / "index.html").is_file():
        app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

        @app.middleware("http")
        async def cache_headers(request, call_next):
            response = await call_next(request)
            if request.url.path.startswith("/assets/"):
                # file names contain a content hash: a new version always gets new names
                response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            elif not request.url.path.startswith("/api/"):
                # index.html, manifest, icons: always revalidate so updates show up immediately
                # (browsers and iOS home-screen apps otherwise keep showing an old version)
                response.headers["Cache-Control"] = "no-cache"
            return response

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            file = (WEB_DIST / path).resolve()
            if path and file.is_file() and WEB_DIST.resolve() in file.parents:
                return FileResponse(file)
            return FileResponse(WEB_DIST / "index.html")

    return app


def _web_build() -> str | None:
    """Fingerprint of the installed web app; the browser shows "new version" when it changes."""
    index = WEB_DIST / "index.html"
    if not index.is_file():
        return None
    return hashlib.sha256(index.read_bytes()).hexdigest()[:12]


WEB_BUILD = _web_build()


def storage_installation_id(storage: Storage) -> str:
    """Random, stable id for the cloud-compatible API (unrelated to any cloud id)."""
    value = storage.get_meta("installation_id")
    if not value:
        value = str(uuid.uuid4())
        storage.set_meta("installation_id", value)
    return value
