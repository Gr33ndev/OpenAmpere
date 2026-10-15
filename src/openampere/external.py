# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""API for other apps, first of all the Home Assistant integration (#76): /api/external/v1/...

Only over HTTPS and only with a token (see apitokens). The data is the same as in the web app; the commands are a
fixed list: battery mode and limits, grid charging on/off with its target, the mode of the own devices and the
charging mode of the wallboxes OpenAmpere controls itself with the highest current of that mode (#388, #409).
Writing needs a "control" token and the control switch in OpenAmpere; everything goes through the same checks as in
the app and is logged with the token's name.
Deliberately not available: the control switch and test mode, the feed-in limit, remote power, settings, tariffs,
devices' configuration (it contains URLs), a wallbox's address, device ID and key, tokens, backup and updates.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import asdict

from fastapi import Body, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect

from .apitokens import ApiTokens, Pairing
from .control import BACKUP_SOC, SOC_FIELDS, BatteryControl, ConfirmationRequired, ControlDisabled, NotConnected, WriteFailed
from .runtime import Runtime
from .wallbox.de_tuya import MODELS as WALLBOX_MODELS
from .wallbox.native import CHARGING as WALLBOX_CHARGING

log = logging.getLogger(__name__)

API_VERSION = 1
PREFIX = "/api/external/v1"
# writes per hour: inverter settings end up in its non-volatile memory; an automation in a loop must not wear it out
BATTERY_WRITES_PER_HOUR = 6
OTHER_WRITES_PER_HOUR = 60
# /state waits at most this long for the battery settings, then answers with the last ones: behind a slow Modbus
# proxy a reading can take longer than Home Assistant waits for the answer (#336)
SETTINGS_WAIT_S = 5.0
# a longer grace than for OpenAmpere's own control: behind a slow Modbus proxy one reading can take 20 s and more,
# and Home Assistant should not make the readings unavailable for that (#336)
STALE_AFTER_S = 60


# the writes of battery settings are kept in storage: a restart (or a crash loop) must not start a new hour (#351)
KEPT = "battery:"
KEPT_META = "external_battery_writes"


class RateLimit:
    def __init__(self, storage=None, now: float | None = None) -> None:
        """storage: keeps the writes of battery settings beyond a restart (None: only in memory)."""
        self._writes: dict[str, deque] = {}
        self._storage = storage
        stored = storage.get_meta(KEPT_META) if storage is not None else None
        now = time.time() if now is None else now
        for key, times in (stored.items() if isinstance(stored, dict) else ()):
            if isinstance(key, str) and key.startswith(KEPT) and isinstance(times, list):
                # only the last hour; a time in the future (the clock was set back) would block for longer
                self._writes[key] = deque(sorted(float(t) for t in times if isinstance(t, (int, float))
                                                 and not isinstance(t, bool) and now - 3600 < t <= now))

    def check(self, key: str, per_hour: int, now: float | None = None) -> None:
        self.take([key], per_hour, now)

    def take(self, keys: list[str], per_hour: int, now: float | None = None) -> list[tuple[str, float]]:
        """Counts one write for each key, for all of them or (with the error for the first that is used up) none."""
        now = time.time() if now is None else now
        for key in keys:
            writes = self._writes.setdefault(key, deque())
            while writes and writes[0] <= now - 3600:
                writes.popleft()
            if len(writes) >= per_hour:
                wait = int(writes[0] + 3600 - now) // 60 + 1
                raise HTTPException(429, f"Zu viele Änderungen in kurzer Zeit (höchstens {per_hour} pro Stunde). "
                                         f"Bitte in {wait} min erneut versuchen.")
        for key in keys:
            self._writes[key].append(now)
        self._keep(keys, now)
        return [(key, now) for key in keys]

    def give_back(self, taken: list[tuple[str, float]]) -> None:
        for key, at in taken:
            writes = self._writes.get(key)
            if writes and at in writes:
                writes.remove(at)
        self._keep([key for key, _ in taken], time.time())

    def _keep(self, keys: list[str], now: float) -> None:
        """The writes of battery settings beyond a restart (#351)."""
        if self._storage is None or not any(key.startswith(KEPT) for key in keys):
            return
        kept = {k: [t for t in v if t > now - 3600] for k, v in self._writes.items() if k.startswith(KEPT)}
        try:
            self._storage.set_meta(KEPT_META, {k: v for k, v in kept.items() if v})
        except Exception as err:  # noqa: BLE001 - e.g. disk full: still limited in memory
            log.debug("could not store the writes of battery settings: %s", err)

    @contextmanager
    def counted(self, keys: list[str], per_hour: int):
        """Counted before the write (requests at the same time cannot get past the limit), and given back when the
        request is refused before anything was written: a wrong value does not use up the writes of the hour. Once the
        inverter was asked (WriteFailed), the write stays counted."""
        taken = self.take(keys, per_hour)
        try:
            yield
        except WriteFailed:
            raise
        except Exception:
            self.give_back(taken)
            raise


def bearer(value: str | None) -> str | None:
    if value and value[:7].lower() == "bearer ":
        return value[7:].strip()
    return None


def register(app: FastAPI, runtime: Runtime, tokens: ApiTokens, pairing: Pairing, *, installation_id: str,
             version: str, battery: BatteryControl, charging, surplus, devices, live_apps: dict,
             wallboxes=None) -> None:
    """live_apps: token id -> {"since", "connections"} of the apps with an open live connection (#79)."""
    collector = runtime.collector
    limits = RateLimit(runtime.storage)

    def source(request: Request) -> str:
        return f"{request.state.token['name']} (Zugang für Apps)"

    def require_control() -> None:
        if not runtime.config.control.enabled:
            raise HTTPException(403, "Die Steuerung ist in OpenAmpere ausgeschaltet (Mehr → Steuerung und Protokoll).")

    def status() -> dict:
        latest = collector.latest
        control = runtime.config.control
        return {"connected": collector.connected, "stale": collector.stale_after(STALE_AFTER_S),
                "last_update": latest.timestamp if latest else None,
                "off_grid": collector.outages.current is not None,  # confirmed, not the flag of one reading (#89)
                "grid_charging": charging.active, "control": {"enabled": control.enabled, "dry_run": control.dry_run},
                # here and not only in /info: it changes with an update of the inverter, read at each new connection
                "firmware": collector.device.firmware if collector.device else None}

    def own_devices() -> list[dict]:
        keys = ("id", "name", "kind", "enabled", "power_w", "on", "temperature_c", "target_c", "status", "error")
        return [{**{k: d[k] for k in keys}, "mode": (d["override"] or {}).get("mode", "auto")}
                for d in devices.live() if d["source"] == "openampere"]

    def own_wallboxes() -> list[dict]:
        """Wallboxes OpenAmpere reads and controls itself (#318): only fresh values, None while one does not answer
        (a portable wallbox is often unplugged). Never its address, device ID or key."""
        found, now = [], time.time()
        for wb in wallboxes.wallboxes if wallboxes else []:
            live, reading = wallboxes.live.get(wb.id), wallboxes.fresh(wb, now)
            found.append({"id": wb.id, "name": wb.name, "enabled": wb.enabled, "mode": wb.mode,
                          # the owner's limit and the highest current of each charging mode (#402, #409)
                          "max_current_a": wb.max_current_a,
                          "mode_current_a": {m: wb.mode_current_a.get(m, wb.max_current_a) for m in WALLBOX_CHARGING},
                          "online": live.online if live and wb.enabled else None,
                          "state": reading.state if reading else None,
                          "connected": reading.connected if reading else None,
                          "charging": reading.charging if reading else None,
                          "power_w": reading.power_w if reading else None,
                          "current_a": reading.set_current_a if reading else None})
        return found

    def live_message() -> dict:
        latest = collector.latest
        return {"type": "live", "live": latest.to_dict() if latest else None, "status": status(),
                "devices": own_devices(), "wallboxes": own_wallboxes()}

    # ---- pairing (no token yet; HTTPS only, see the middleware) ------------------------------------------------

    @app.post(f"{PREFIX}/pair/start")
    def pair_start(request: Request, body: dict = Body(...)):
        try:
            started = pairing.start(str(body.get("name") or ""), str(body.get("commitment") or ""),
                                    client=request.client.host if request.client else None)
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
                       "rated_power_w": device.rated_power_w, "supports_control": device.supports_control,
                       "work_modes": device.work_modes,  # None: all modes (SAJ offers two, #377)
                       "backup_reserve": device.backup_reserve}  # its own reserve in backup mode (#380)
            if device else None,
            "pv_inputs": [{"index": i, "name": names[i] if i < len(names) and names[i] else f"Modulfeld {i + 1}"}
                          for i in range(count) if str(i + 1) not in hidden],
            "pv_input_count": count,  # also the hidden ones: the integration sets up again when the number changes
            "devices": [{"id": d["id"], "name": d["name"], "kind": d["kind"]} for d in own_devices()],
            "wallboxes": [{"id": wb.id, "name": wb.name, "model": WALLBOX_MODELS[wb.model].label}
                          for wb in (wallboxes.wallboxes if wallboxes else [])],
            "poll_interval": runtime.config.inverter.poll_interval, "web_port": runtime.config.server.port,
        }

    @app.get(f"{PREFIX}/state")
    async def state():
        settings = await battery.latest(SETTINGS_WAIT_S)
        battery_settings = {k: settings.get(k) for k in ("work_mode", *SOC_FIELDS, BACKUP_SOC)} if settings else None
        grid = charging.settings
        return {**live_message(), "type": "state", "battery_settings": battery_settings,
                "grid_charging": {"enabled": grid.enabled, "target_soc": grid.target_soc, "active": charging.active},
                "price_ct": runtime.tariffs.price_at(time.time(), runtime.tz)}

    @app.websocket(f"{PREFIX}/ws")
    async def live_ws(ws: WebSocket):
        # the HTTP middleware does not see WebSockets: the same checks here
        secret = bearer(ws.headers.get("authorization"))
        token = tokens.check(secret) if ws.url.scheme == "wss" else None
        if token is None:
            await ws.close(code=1008)
            return
        await ws.accept()
        queue = collector.subscribe()
        entry = live_apps.setdefault(token["id"], {"since": time.time(), "connections": 0})
        entry["connections"] += 1

        async def push():
            while True:
                # a token revoked meanwhile gets nothing more: the connection ends, a new one is refused
                if tokens.check(secret) is None:
                    await ws.close(code=1008)
                    return
                await ws.send_json(live_message())
                await queue.get()

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
        unknown = set(changes) - {"work_mode", *SOC_FIELDS, BACKUP_SOC}
        if unknown or not changes:
            raise HTTPException(400, "Erlaubt sind work_mode, min_soc, max_soc und min_soc_on_grid.")
        try:
            with limits.counted([f"battery:{key}" for key in changes], BATTERY_WRITES_PER_HOUR):
                result = await battery.write(changes, source=source(request))
        except ControlDisabled as err:
            raise HTTPException(403, str(err)) from None
        except ConfirmationRequired:  # the warning can only be confirmed in OpenAmpere itself (#313)
            raise HTTPException(428, "Eine Notstrom-Reserve unter 10 % lässt sich nur in OpenAmpere selbst einstellen, "
                                     "nach einer Warnung.") from None
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
        old = charging.settings
        if body.get("enabled") and not old.legal_confirmed:
            raise HTTPException(400, "Laden aus dem Netz bitte zuerst einmal in OpenAmpere einrichten "
                                     "(Geräte → Speicher → Laden aus dem Netz).")
        try:
            with limits.counted(["grid_charging"], OTHER_WRITES_PER_HOUR):
                new = charging.save({**asdict(old), **body}, source=source(request))
        except ValueError as err:
            raise HTTPException(400, str(err)) from None
        await charging.tick()
        return {"enabled": new.enabled, "target_soc": new.target_soc, "active": charging.active}

    @app.put(f"{PREFIX}/devices/{{device_id}}/mode")
    async def put_device_mode(request: Request, device_id: str, body: dict = Body(...)):
        require_control()
        if device_id not in {c.id for c in surplus.consumers}:  # no limit entries for made-up ids
            raise HTTPException(400, "Gerät nicht gefunden.")  # as before, from set_override
        try:
            with limits.counted([f"device:{device_id}"], OTHER_WRITES_PER_HOUR):
                # logged there with the device's name, once (#153)
                surplus.set_override(device_id, str(body.get("mode")), body.get("hours"), source=source(request))
        except ValueError as err:
            raise HTTPException(400, str(err)) from None
        await surplus.tick()
        return {"devices": own_devices()}

    @app.put(f"{PREFIX}/wallboxes/{{wallbox_id}}/mode")
    async def put_wallbox_mode(request: Request, wallbox_id: str, body: dict = Body(...)):
        require_control()
        if wallboxes is None or wallbox_id not in {wb.id for wb in wallboxes.wallboxes}:  # no limit entries either
            raise HTTPException(400, "Diese Wallbox gibt es nicht.")  # as from set_mode
        try:
            with limits.counted([f"wallbox:{wallbox_id}"], OTHER_WRITES_PER_HOUR):
                # logged there with the wallbox's name; the master switch and test mode apply to every write; the
                # highest current of the mode is optional and checked there against the owner's limit (#409)
                wallboxes.set_mode(wallbox_id, str(body.get("mode")), source=source(request),
                                   current_a=body.get("current_a"))
        except ValueError as err:
            raise HTTPException(400, str(err)) from None
        await surplus.tick()
        return {"wallboxes": own_wallboxes()}
