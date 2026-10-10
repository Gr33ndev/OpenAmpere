"""HTTP/WebSocket API and static web app."""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import json
import logging
import secrets
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

from . import discovery, cloud_import, external, health, i18n
from .apitokens import ApiTokens, Pairing, connection_code
from .auth import CSRF_HEADER, SESSION_COOKIE, SESSION_TTL_S, Auth, host_allowed
from .config import PRIVATE, SECRETS
from .drivers import registry
from .control import (BatteryControl, ConfirmationRequired, ControlDisabled, ExportLimitControl, NotConnected,
                      WriteFailed)
from .drivers.base import Snapshot
from .periods import PERIODS, bucket_start, parse_anchor, period_bounds, to_ts
from .runtime import Runtime
from .storage import FLOWS, InvalidBackup, Storage
from .discovery import Rediscovery
from .billing import Billing
from .gridmeter import GridMeter
from .charging import GridCharging
from .consumers import SurplusControl
from .evcc import Evcc, EvccError
from .devices import Devices
from .notify import Notifier
from .remote import Remote
from .updates import Updates
from .diagnostics import Diagnostics, report_markdown

log = logging.getLogger(__name__)

try:
    VERSION = version("openampere")
except PackageNotFoundError:  # running from source
    VERSION = "dev"

WEB_DIST = Path(__file__).parent / "web"
MAX_UPLOAD_BYTES = 500 * 1024 * 1024
# a backup holds every detail reading (about 0.4 GB per year at the default interval); written to disk, not memory
MAX_BACKUP_BYTES = 4 * 1024 * 1024 * 1024


def ratios(flows: dict) -> dict:
    """Autarky = share of consumption not drawn from the grid; self-consumption = share of PV used locally."""
    load, pv = flows.get("load") or 0, flows.get("pv") or 0
    grid_import, grid_export = flows.get("grid_import") or 0, flows.get("grid_export") or 0
    autarky = max(0.0, min(1.0, 1 - grid_import / load)) if load > 0 else None
    self_consumption = max(0.0, min(1.0, 1 - grid_export / pv)) if pv > 0 else None
    # what goes in (solar on the DC side, grid, battery) minus what goes out (export, battery, house): mainly the
    # conversion losses of the inverter, a few per cent of the solar energy (#15)
    sources = pv + grid_import + (flows.get("battery_discharge") or 0)
    sinks = load + grid_export + (flows.get("battery_charge") or 0)
    return {"autarky": autarky, "self_consumption": self_consumption,
            "conversion_loss_wh": sources - sinks if sources > sinks else 0.0}


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


# reading these needs a login as well (secrets, grid-operator references, meter numbers, the Tailscale login link,
# the diagnostics report that can hold the full serial number); /api/settings hides its private values instead (#164)
PROTECTED_READS = ("/api/backup", "/api/control/log", "/api/diagnostics", "/api/gridmeter", "/api/remote",
                   "/api/tokens")
PUBLIC_WRITES = ("/api/auth/login", "/api/auth/setup", "/api/auth/logout")


class TokenRequest(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    scope: str = Field(pattern="^(read|control)$")


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
    gridmeter = GridMeter(runtime)
    billing = Billing(runtime.storage, runtime.tariffs, gridmeter)
    evcc = Evcc(runtime)
    surplus = SurplusControl(runtime, evcc)
    devices = Devices(runtime, surplus, evcc)
    notifier = Notifier(runtime)
    diagnostics = Diagnostics(runtime)
    updates = Updates(runtime, VERSION)
    remote = Remote(runtime)

    async def watchdog() -> None:
        """Background jobs: find the inverter after an IP change, exchange prices, grid charging."""
        while True:
            await asyncio.sleep(30)
            for job in (lambda: rediscovery.check(time.time()), runtime.tariffs.refresh_prices, charging.tick,
                        notifier.check, updates.tick, gridmeter.tick,
                        diagnostics.watch_remote):
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

    # no /docs, /redoc and /openapi.json: the app does not need them and they are open on the home network (#164)
    app = FastAPI(title="OpenAmpere", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    auth = Auth(storage)
    tokens = ApiTokens(storage)
    pairing = Pairing(tokens, lambda: runtime.tls.fingerprint)
    live_apps: dict[str, dict] = {}  # apps with an open live connection right now (#79)
    # short-lived links for downloads that open in their own window (iPhone home-screen app, see /api/backup/link)
    download_links: dict[str, float] = {}

    def download_link_ok(token: str | None) -> bool:
        now = time.time()
        for key in [k for k, until in download_links.items() if until < now]:
            del download_links[key]
        return bool(token) and token in download_links

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
        if path.startswith(external.PREFIX + "/") or path == external.PREFIX:
            # other apps (#76): only with a token in the Authorization header and only over HTTPS; no cookies,
            # so cross-site requests cannot use it and the CSRF checks of the web app do not apply
            if request.url.scheme != "https":
                return JSONResponse({"detail": "Nur über HTTPS erreichbar.", "code": "https_required"}, 403)
            if path.startswith(external.PREFIX + "/pair/"):
                return await call_next(request)  # pairing: the app gets its token only after approval in the web app
            token = tokens.check(external.bearer(request.headers.get("authorization")))
            if token is None:
                return JSONResponse({"detail": "Zugang ungültig oder widerrufen.", "code": "token_invalid"}, 401)
            if writing and token["scope"] != "control":
                return JSONResponse({"detail": "Dieser Zugang darf nur lesen.", "code": "read_only"}, 403)
            request.state.token = token
            return await call_next(request)
        if path == "/api/backup" and not writing and download_link_ok(request.query_params.get("token")):
            return await call_next(request)
        if path.startswith("/api/") and (writing or path in PROTECTED_READS):
            if writing and (not origin_ok(request.headers.get("origin"), host) or request.headers.get(CSRF_HEADER) != "1"):
                return JSONResponse({"detail": "Anfrage abgelehnt (fremde Herkunft)."}, 403)
            if path not in PUBLIC_WRITES:
                if not auth.configured:
                    return JSONResponse({"detail": "Bitte zuerst ein Passwort festlegen.", "code": "setup_required"}, 401)
                if not auth.valid(request.cookies.get(SESSION_COOKIE)):
                    return JSONResponse({"detail": "Bitte anmelden.", "code": "login_required"}, 401)
        return await call_next(request)

    # added after the security check, so it also translates the errors that check returns
    @app.middleware("http")
    async def translate_errors(request: Request, call_next):
        """Error messages in the language of the web app, if it asks for one (#104)."""
        response = await call_next(request)
        lang = request.headers.get(i18n.HEADER)
        if (not lang or lang == "de" or response.status_code < 400
                or response.headers.get("content-type") != "application/json"):
            return response
        body = b"".join([chunk async for chunk in response.body_iterator])
        headers = {k: v for k, v in response.headers.items() if k.lower() not in ("content-length", "content-type")}
        try:
            data = json.loads(body)
        except ValueError:
            data = None
        if isinstance(data, dict) and isinstance(data.get("detail"), str):
            data["detail"] = i18n.translate(data["detail"], lang)
            return JSONResponse(data, status_code=response.status_code, headers=headers)
        return Response(body, status_code=response.status_code, headers=headers, media_type="application/json")

    def logged_in(request: Request) -> bool:
        return auth.valid(request.cookies.get(SESSION_COOKIE))

    def start_session(response: Response) -> None:
        response.set_cookie(SESSION_COOKIE, auth.create_session(), max_age=SESSION_TTL_S, httponly=True,
                            samesite="strict", path="/")

    # ---- access protection ---------------------------------------------------

    @app.get("/api/auth/status")
    def auth_status(request: Request):
        return {"configured": auth.configured, "authenticated": logged_in(request)}

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

    # ---- access tokens for other apps (#76) --------------------------------------

    def tls_view() -> dict:
        port = runtime.config.server.tls_port
        return {"port": port or None, "fingerprint": runtime.tls.fingerprint if port else None, "error": runtime.tls_error}

    def tokens_view() -> dict:
        return {"tokens": [{**t, "live_since": (live_apps.get(t["id"]) or {}).get("since")} for t in tokens.list()],
                "pairing": pairing.pending(), "tls": tls_view()}

    @app.get("/api/tokens")
    def get_tokens():
        return tokens_view()

    @app.post("/api/tokens/pairing/{request_id}")
    def decide_pairing(request_id: str, body: dict = Body(...)):
        approve, scope = bool(body.get("approve")), str(body.get("scope") or "read")
        if scope not in ("read", "control"):
            raise HTTPException(400, "Unbekannte Berechtigung.")
        try:
            entry = pairing.decide(request_id, approve, scope)
        except KeyError:
            raise HTTPException(404, "Die Anfrage ist abgelaufen. Bitte die Kopplung in der App neu starten.") from None
        except ValueError as err:
            raise HTTPException(400, str(err)) from None
        if approve:
            storage.log_control("token_created", {"name": entry["name"], "scope": entry["scope"], "via": "Kopplung"},
                                False, "ok")
        return tokens_view()

    @app.post("/api/tokens")
    def create_token(body: TokenRequest, request: Request):
        port = runtime.config.server.tls_port
        if not port:
            raise HTTPException(409, "Der HTTPS-Port ist ausgeschaltet (server.tls_port). Ohne ihn können sich "
                                     "andere Apps nicht sicher verbinden.")
        try:
            token, entry = tokens.create(body.name, body.scope)
        except ValueError as err:
            raise HTTPException(400, str(err)) from None
        storage.log_control("token_created", {"name": entry["name"], "scope": entry["scope"]}, False, "ok")
        # the address the browser uses is usually the one Home Assistant can reach, too
        code = connection_code(request.url.hostname or "", port, runtime.tls.fingerprint, token)
        return {"token": entry, "code": code, **tokens_view()}

    @app.delete("/api/tokens/{token_id}")
    def delete_token(token_id: str):
        try:
            name = next(t["name"] for t in tokens.list() if t["id"] == token_id)
            tokens.revoke(token_id)
        except (StopIteration, KeyError):
            raise HTTPException(404, "Zugang nicht gefunden.") from None
        storage.log_control("token_revoked", {"name": name}, False, "ok")
        return tokens_view()

    external.register(app, runtime, tokens, pairing, installation_id=installation_id, version=VERSION, battery=battery,
                      charging=charging, surplus=surplus, devices=devices, live_apps=live_apps)

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
            "database": {"damaged": storage.get_meta("database_damaged"),
                         "restored": storage.get_meta("database_restored"),
                         "rollback": storage.get_meta("database_rollback")},
            "devices": {"grid_charging": charging.active, "items": [d for d in devices.live() if d["enabled"]]},
            "poll_interval": collector.interval,
            "device": collector.device.__dict__ if collector.device else None,
            "energy_step_wh": collector.energy_step_wh(),
            "firmware": {**(storage.get_meta("firmware") or {}), "history": storage.get_meta("firmware_history") or []},
            "control": {"enabled": control.enabled, "dry_run": control.dry_run},
            "storage": collector.storage_state(),
        }

    # ---- settings & setup ------------------------------------------------

    @app.get("/api/settings")
    def get_settings(request: Request):
        return runtime.settings_view(authenticated=logged_in(request))

    @app.put("/api/settings")
    async def put_settings(changes: dict = Body(...)):
        expected = changes.pop("_revision", None)
        # null is the placeholder of a value hidden without login (#164): never store it over the real value
        changes = {k: v for k, v in changes.items() if not (k in PRIVATE and v is None)}
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
    def get_control_log(request: Request, limit: int = Query(50, ge=0)):
        """limit=0: the whole log, for the export. "note": the reason or error in the language of the app (#153)."""
        lang = request.headers.get(i18n.HEADER)
        return {"entries": [{**e, "note": i18n.log_note(e["result"], lang)} for e in storage.control_log(limit)]}

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

    @app.get("/api/outages")
    def outages():
        """Power cuts: the running one and the history (off-grid mode of the inverter)."""
        return collector.outages.view()

    @app.delete("/api/outages/{start}")
    def remove_outage(start: float):
        """"Das war kein Stromausfall": remove an entry from the history (#95)."""
        if not collector.outages.remove(start):
            raise HTTPException(404, "Diesen Stromausfall gibt es nicht.")
        when = datetime.datetime.fromtimestamp(start, runtime.tz).strftime("%d.%m.%Y %H:%M")
        storage.log_control("outage_removed", {"from": {"outage": when}, "to": {"outage": "entfernt"}}, False,
                            "kein Stromausfall")
        return collector.outages.view()

    @app.get("/api/storage")
    def storage_usage():
        """Database size, free space and an estimate per year of detail readings (retention setting)."""
        usage = storage.usage()
        # one detail row per poll; about 130 bytes each including index and device readings
        per_day = 86400 / max(1.0, runtime.config.inverter.poll_interval) * 130
        return {**usage, "bytes_per_year": round(per_day * 365), "retention_days": runtime.config.storage.raw_retention_days}

    @app.post("/api/backup/link")
    def backup_link():
        """A download link valid for 10 minutes without the login cookie: on the iPhone, downloads from the
        home-screen app must open in their own window (with a "Done" button), which does not share the cookie."""
        token = secrets.token_urlsafe(24)
        download_links[token] = time.time() + 600
        return {"url": f"/api/backup?token={token}", "valid_s": 600}

    @app.get("/api/backup")
    def backup(background: BackgroundTasks):
        tmp = Path(tempfile.mkdtemp()) / "openampere.db"
        storage.backup(tmp, drop_settings=SECRETS)
        background.add_task(shutil.rmtree, tmp.parent, ignore_errors=True)  # incl. SQLite side files
        name = time.strftime("openampere-backup-%Y-%m-%d.db")
        return FileResponse(tmp, filename=name, media_type="application/vnd.sqlite3")

    @app.post("/api/backup/restore")
    async def restore_backup(request: Request):
        """Replaces the database with an uploaded backup and starts OpenAmpere again (#165). The current database
        is kept as a copy; password, sessions and secrets stay those of the running installation."""
        folder = Path(storage.path).resolve().parent
        length = int(request.headers.get("content-length") or 0)
        if length > MAX_BACKUP_BYTES:
            raise HTTPException(413, "Datei zu groß (max. 4 GB).")
        if length > shutil.disk_usage(folder).free:
            raise HTTPException(507, "Nicht genug freier Speicherplatz für die Sicherung.")
        fd, name = tempfile.mkstemp(prefix=".restore-upload-", dir=folder)
        upload, size = Path(name), 0
        try:
            with open(fd, "wb") as file:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_BACKUP_BYTES:
                        raise HTTPException(413, "Datei zu groß (max. 4 GB).")
                    file.write(chunk)
            await asyncio.to_thread(storage.stage_restore, upload, keep_settings=SECRETS)
        except InvalidBackup as err:
            raise HTTPException(400, str(err)) from None
        except OSError as err:
            log.warning("could not store the uploaded backup: %s", err)
            raise HTTPException(507, "Die Sicherung ließ sich nicht speichern. Bitte prüfen, ob genug freier "
                                     "Speicherplatz da ist.") from None
        finally:
            for suffix in ("", "-wal", "-shm", "-journal"):  # the file was already moved when it was valid
                Path(f"{upload}{suffix}").unlink(missing_ok=True)
        log.warning("backup uploaded, OpenAmpere starts again to restore it")
        return {"restarting": runtime.request_restart()}

    @app.delete("/api/database/damaged")
    def dismiss_damaged_database():
        """The owner has read the notice about the damaged database (and restored a backup or not)."""
        storage.delete_meta("database_damaged")
        return {"ok": True}

    @app.delete("/api/database/rollback")
    def dismiss_database_rollback():
        """The owner has read that an older version continues with the copy from before an upgrade (#166)."""
        storage.delete_meta("database_rollback")
        return {"ok": True}

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
        partial_since = recorded_since = None
        money_rows = None
        if running and start <= running["ts"] < end:
            # stored quarters + the quarter hour that is still running = exactly what the counters say
            flows = {f: (flows[f] or 0) + running[f] for f in FLOWS}
            quarters += 1
            first = storage.first_quarter(start, end) or running["ts"]
            if period == "day" and first > start + 900:
                # first day: recording started during the day; the inverter's daily counters are complete
                recorded_since = first
                today = latest.today.__dict__
                if all(today.get(f) is not None for f in FLOWS) and today["pv"] >= flows["pv"]:
                    flows = {f: today[f] for f in FLOWS}
                    money_rows = [{"ts": start, **flows}]  # the money must follow the same values (#15)
                else:
                    partial_since = first
        extra = [running] if running and start <= running["ts"] < end and money_rows is None else []
        return {"period": period, "from": start, "to": end, "quarters": quarters,
                "partial_since": partial_since, "recorded_since": recorded_since, "energy_wh": flows,
                **ratios(flows), "money": runtime.tariffs.money(start, end, runtime.tz, extra, money_rows)}

    # ---- tariffs & exchange prices ------------------------------------------

    @app.get("/api/tariffs")
    def get_tariffs():
        return {"tariffs": [asdict(t) for t in runtime.tariffs.all()], "eeg": runtime.eeg_view()}

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

    # ---- access from anywhere with Tailscale (#83) ----------------------------

    @app.get("/api/remote")
    def get_remote():
        return remote.view()

    @app.post("/api/remote")
    def post_remote(body: dict = Body(...)):
        try:
            remote.request(str(body.get("action")))
        except ValueError as err:
            raise HTTPException(400, str(err)) from None
        except RuntimeError as err:
            raise HTTPException(409, str(err)) from None
        return remote.view()

    # ---- meter values of the grid operator (#60) -----------------------------

    @app.get("/api/gridmeter")
    def get_gridmeter():
        return gridmeter.view()

    @app.post("/api/gridmeter/sync")
    async def sync_gridmeter():
        """Fetch now, e.g. right after entering the login. The answer says whether it worked."""
        await gridmeter.sync(force=True)
        return gridmeter.view()

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
        return {"tariffs": [asdict(t) for t in tariffs], "eeg": runtime.eeg_view()}

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
                result["evcc_synced"] = await evcc.set_priority_soc(surplus.evcc_priority_soc())
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
        exchange = storage.prices(start, end)
        prices = runtime.tariffs.quarter_prices(start, end, runtime.tz) if tariff.kind != "fixed" else {}
        entries = [{"ts": ts, "ct": round(ct, 2), "exchange_eur_mwh": exchange.get(ts)} for ts, ct in prices.items()]
        return {"kind": tariff.kind, "feed_in_ct": tariff.feed_in_ct, "fixed_ct": tariff.price_ct if tariff.kind == "fixed" else None,
                "entries": entries}

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
        # inputs hidden in the settings (e.g. an unused MPPT) are left out; their readings are kept (#38)
        hidden = {str(h) for h in runtime.config.pv.hidden_inputs}
        # an input without any yield in the whole period is not connected (#58): leave it out as well
        produced = [max((e["values"][i] or 0 for e in entries), default=0) > (1 if mode == "energy" else 5)
                    for i in range(count)]
        keep = [i for i in range(count) if str(i + 1) not in hidden and (produced[i] or not any(produced))]
        return {"mode": mode, "inputs": [i + 1 for i in keep], "labels": [labels[i] for i in keep],
                "entries": [{"ts": e["ts"], "values": [e["values"][i] for i in keep]} for e in entries],
                "totals_wh": [sums[i] for i in keep] if sums is not None else None}

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
        def neg(v):
            return -v if v is not None else None

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
