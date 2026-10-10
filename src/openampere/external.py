"""API for other apps, first of all the Home Assistant integration (#76): /api/external/v1/...

Only over HTTPS and only with a token (see apitokens). The data is the same as in the web app; the commands are a
fixed list: battery mode and limits, grid charging on/off with its target and the mode of the own devices. Writing
needs a "control" token and the control switch in OpenAmpere; everything goes through the same checks as in the
app and is logged with the token's name. Deliberately not available: the control switch and test mode, the feed-in
limit, remote power, settings, tariffs, devices' configuration (it contains URLs), tokens, backup and updates.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import asdict

from fastapi import Body, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect

from .apitokens import ApiTokens, Pairing
from .control import SOC_FIELDS, BatteryControl, ControlDisabled, NotConnected, WriteFailed
from .runtime import Runtime

API_VERSION = 1
PREFIX = "/api/external/v1"
# writes per hour: inverter settings end up in its non-volatile memory; an automation in a loop must not wear it out
BATTERY_WRITES_PER_HOUR = 6
OTHER_WRITES_PER_HOUR = 60


class RateLimit:
    def __init__(self) -> None:
        self._writes: dict[str, deque] = {}

    def check(self, key: str, per_hour: int, now: float | None = None) -> None:
        now = time.time() if now is None else now
        writes = self._writes.setdefault(key, deque())
        while writes and writes[0] <= now - 3600:
            writes.popleft()
        if len(writes) >= per_hour:
            wait = int(writes[0] + 3600 - now) // 60 + 1
            raise HTTPException(429, f"Zu viele Änderungen in kurzer Zeit (höchstens {per_hour} pro Stunde). "
                                     f"Bitte in {wait} min erneut versuchen.")
        writes.append(now)


def bearer(value: str | None) -> str | None:
    if value and value[:7].lower() == "bearer ":
        return value[7:].strip()
    return None


def register(app: FastAPI, runtime: Runtime, tokens: ApiTokens, pairing: Pairing, *, installation_id: str,
             version: str, battery: BatteryControl, charging, surplus, devices, live_apps: dict) -> None:
    """live_apps: token id -> {"since", "connections"} of the apps with an open live connection (#79)."""
    collector = runtime.collector
    limits = RateLimit()

    def source(request: Request) -> str:
        return f"{request.state.token['name']} (Zugang für Apps)"

    def require_control() -> None:
        if not runtime.config.control.enabled:
            raise HTTPException(403, "Die Steuerung ist in OpenAmpere ausgeschaltet (Mehr → Steuerung und Protokoll).")

    def status() -> dict:
        latest = collector.latest
        control = runtime.config.control
        return {"connected": collector.connected, "stale": collector.stale,
                "last_update": latest.timestamp if latest else None,
                "off_grid": collector.outages.current is not None,  # confirmed, not the flag of one reading (#89)
                "grid_charging": charging.active, "control": {"enabled": control.enabled, "dry_run": control.dry_run}}

    def own_devices() -> list[dict]:
        keys = ("id", "name", "kind", "enabled", "power_w", "on", "temperature_c", "target_c", "status", "error")
        return [{**{k: d[k] for k in keys}, "mode": (d["override"] or {}).get("mode", "auto")}
                for d in devices.live() if d["source"] == "openampere"]

    def live_message() -> dict:
        latest = collector.latest
        return {"type": "live", "live": latest.to_dict() if latest else None, "status": status(),
                "devices": own_devices()}

    # ---- pairing (no token yet; HTTPS only, see the middleware) ------------------------------------------------

    @app.post(f"{PREFIX}/pair/start")
    def pair_start(body: dict = Body(...)):
        try:
            started = pairing.start(str(body.get("name") or ""), str(body.get("commitment") or ""))
        except PermissionError as err:
            raise HTTPException(429, str(err)) from None
        except ValueError:
            raise HTTPException(400, "Ungültige Anfrage.") from None
        return {**started, "api_version": API_VERSION, "installation_id": installation_id, "version": version}

    @app.post(f"{PREFIX}/pair/{{request_id}}/reveal")
    def pair_reveal(request_id: str, body: dict = Body(...)):
        try:
            pairing.reveal(request_id, body.get("poll_key"), body.get("nonce"))
        except KeyError:
            raise HTTPException(404, "Anfrage nicht gefunden oder abgelaufen.") from None
        except ValueError:
            raise HTTPException(400, "Ungültige Anfrage.") from None
        return {"status": "pending"}

    @app.post(f"{PREFIX}/pair/{{request_id}}/status")
    def pair_status(request_id: str, body: dict = Body(...)):
        try:
            return pairing.status(request_id, body.get("poll_key"))
        except KeyError:
            raise HTTPException(404, "Anfrage nicht gefunden oder abgelaufen.") from None

    # ---- with a token ------------------------------------------------------------------------------------------

    @app.get(f"{PREFIX}/info")
    def info(request: Request):
        device = collector.device
        names = runtime.config.pv.input_names
        hidden = set(runtime.config.pv.hidden_inputs)
        count = len(collector.latest.pv_inputs) if collector.latest else 0
        return {
            "api_version": API_VERSION, "version": version, "installation_id": installation_id,
            "token": {"name": request.state.token["name"], "scope": request.state.token["scope"]},
            # no serial number: it would end up in Home Assistant's backups
            "device": {"manufacturer": device.manufacturer, "model": device.model, "firmware": device.firmware,
                       "rated_power_w": device.rated_power_w, "supports_control": device.supports_control}
            if device else None,
            "pv_inputs": [{"index": i, "name": names[i] if i < len(names) and names[i] else f"Modulfeld {i + 1}"}
                          for i in range(count) if str(i + 1) not in hidden],
            "pv_input_count": count,  # also the hidden ones: the integration sets up again when the number changes
            "devices": [{"id": d["id"], "name": d["name"], "kind": d["kind"]} for d in own_devices()],
            "poll_interval": runtime.config.inverter.poll_interval, "web_port": runtime.config.server.port,
        }

    @app.get(f"{PREFIX}/state")
    async def state():
        try:
            settings = await battery.read()
            battery_settings = {k: settings.get(k) for k in ("work_mode", *SOC_FIELDS)}
        except Exception:  # noqa: BLE001 - not connected or no answer: the rest is still useful
            battery_settings = None
        grid = charging.settings
        return {**live_message(), "type": "state", "battery_settings": battery_settings,
                "grid_charging": {"enabled": grid.enabled, "target_soc": grid.target_soc, "active": charging.active},
                "price_ct": runtime.tariffs.price_at(time.time(), runtime.tz)}

    @app.websocket(f"{PREFIX}/ws")
    async def live_ws(ws: WebSocket):
        # the HTTP middleware does not see WebSockets: the same checks here
        token = tokens.check(bearer(ws.headers.get("authorization"))) if ws.url.scheme == "wss" else None
        if token is None:
            await ws.close(code=1008)
            return
        await ws.accept()
        queue = collector.subscribe()
        entry = live_apps.setdefault(token["id"], {"since": time.time(), "connections": 0})
        entry["connections"] += 1

        async def push():
            await ws.send_json(live_message())
            while True:
                await queue.get()
                await ws.send_json(live_message())

        async def watch_close():
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
            entry["connections"] -= 1
            if entry["connections"] <= 0:
                live_apps.pop(token["id"], None)

    @app.put(f"{PREFIX}/battery")
    async def put_battery(request: Request, changes: dict = Body(...)):
        require_control()
        unknown = set(changes) - {"work_mode", *SOC_FIELDS}
        if unknown or not changes:
            raise HTTPException(400, "Erlaubt sind work_mode, min_soc, max_soc und min_soc_on_grid.")
        for key in changes:
            limits.check(f"battery:{key}", BATTERY_WRITES_PER_HOUR)
        try:
            result = await battery.write(changes, source=source(request))
        except ControlDisabled as err:
            raise HTTPException(403, str(err)) from None
        except NotConnected as err:
            raise HTTPException(503, str(err)) from None
        except WriteFailed as err:
            raise HTTPException(502, str(err)) from None
        except ValueError as err:
            raise HTTPException(400, str(err)) from None
        return {k: result.get(k) for k in ("dry_run", "written", "warning")}

    @app.put(f"{PREFIX}/grid-charging")
    async def put_grid_charging(request: Request, body: dict = Body(...)):
        require_control()
        if not body or set(body) - {"enabled", "target_soc"}:
            raise HTTPException(400, "Erlaubt sind enabled und target_soc.")
        limits.check("grid_charging", OTHER_WRITES_PER_HOUR)
        old = charging.settings
        if body.get("enabled") and not old.legal_confirmed:
            raise HTTPException(400, "Laden aus dem Netz bitte zuerst einmal in OpenAmpere einrichten "
                                     "(Geräte → Speicher → Laden aus dem Netz).")
        try:
            new = charging.save({**asdict(old), **body}, source=source(request))
        except ValueError as err:
            raise HTTPException(400, str(err)) from None
        runtime.storage.log_control("grid_charging", {"source": source(request), "to": body}, runtime.config.control.dry_run, "ok")
        await charging.tick()
        return {"enabled": new.enabled, "target_soc": new.target_soc, "active": charging.active}

    @app.put(f"{PREFIX}/devices/{{device_id}}/mode")
    async def put_device_mode(request: Request, device_id: str, body: dict = Body(...)):
        require_control()
        limits.check(f"device:{device_id}", OTHER_WRITES_PER_HOUR)
        try:
            # logged there with the device's name, once (#153)
            surplus.set_override(device_id, str(body.get("mode")), body.get("hours"), source=source(request))
        except ValueError as err:
            raise HTTPException(400, str(err)) from None
        await surplus.tick()
        return {"devices": own_devices()}
