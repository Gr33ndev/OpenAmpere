# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Listening to a wallbox over OCPP 1.6J, read-only (#328).

The optional third step of the wallbox check (#325): the owner starts the listener in the app and points the wallbox
at OpenAmpere in the manufacturer's app. OpenAmpere then acts as the OCPP central system only to listen and ask:

- the BootNotification is answered with "Pending": in that state the central system may only retrieve information;
- OpenAmpere sends only GetConfiguration and GetCompositeSchedule (READ_REQUESTS), never a command: no start or stop,
  no charging profile, no configuration change, no reset;
- anything else the wallbox sends is answered with a CALLERROR, not with a decision.

The login middleware does not cover WebSocket routes, so the endpoint keeps its own rules: only while the listener
runs (LISTEN_S at most), only from addresses in the home network, one connection, limited message size and count.
Security profile 0 (no password, no TLS) is acceptable because OpenAmpere only listens; control over OCPP (#318)
would need more. A minimal OCPP-J layer (JSON arrays), no new dependency.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone

from fastapi import WebSocket

from . import ha_app
from .outbound import in_home_network
from .runtime import Runtime

log = logging.getLogger(__name__)

SUBPROTOCOL = "ocpp1.6"
LISTEN_S = 15 * 60
MAX_MESSAGE = 64 * 1024
MAX_MESSAGES = 500  # per connection: a wallbox sends a few per minute
KEEP = 20  # status notifications and meter values kept
HEARTBEAT_S = 60  # the interval the wallbox is told in the Pending answer
READ_REQUESTS = ("GetConfiguration", "GetCompositeSchedule")  # all OpenAmpere ever sends
CALL, RESULT, ERROR = 2, 3, 4


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class OcppListener:
    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime
        self.until = 0.0  # the listener runs until then (time.monotonic())
        self.connected = False
        self._ws: WebSocket | None = None
        self._pending: dict[str, str] = {}  # our request id -> action
        self.record: dict | None = None  # what the wallbox said during the last connection

    # ---- the owner's switch ----------------------------------------------------------

    def start(self) -> dict:
        self.until = time.monotonic() + LISTEN_S
        return self.view()

    async def stop(self) -> dict:
        self.until = 0.0
        if self._ws is not None:
            await self._ws.close(code=1000)
        return self.view()

    @property
    def active(self) -> bool:
        return time.monotonic() < self.until

    def view(self) -> dict:
        """For the owner's own screen (the report goes through privacy.py)."""
        record = self.record or {}
        boot = record.get("boot") or {}
        return {"active": self.active, "remaining_s": max(0, round(self.until - time.monotonic())),
                "connected": self.connected, "path": "/ocpp", "charge_point": record.get("charge_point"),
                "vendor": boot.get("chargePointVendor"), "model": boot.get("chargePointModel"),
                "firmware": boot.get("firmwareVersion"), "configuration_keys": len(record.get("configuration") or []),
                "status": (record.get("status") or [{}])[-1].get("status"), "messages": record.get("messages", 0)}

    # ---- the WebSocket ------------------------------------------------------------------

    async def refuse_reason(self, ws: WebSocket) -> str | None:
        if not self.active:
            return "listener not started"
        if self._ws is not None:
            return "already connected"
        if ha_app.via_ingress(ws):  # the wallbox connects directly, never through the Home Assistant sidebar
            return "through the Home Assistant sidebar"
        client = ws.client.host if ws.client else ""
        if not client or not await asyncio.to_thread(in_home_network, client):
            return "not from the home network"
        if SUBPROTOCOL not in ws.scope.get("subprotocols", []):
            return "no ocpp1.6 subprotocol"
        return None

    async def serve(self, ws: WebSocket, charge_point: str | None) -> None:
        reason = await self.refuse_reason(ws)
        if reason:
            log.info("OCPP connection refused: %s", reason)
            await ws.close(code=1008)
            return
        await ws.accept(subprotocol=SUBPROTOCOL)
        self._ws, self.connected, self._pending = ws, True, {}
        self.record = {"charge_point": charge_point, "boot": None, "configuration": None, "unknown_keys": None,
                       "schedule": None, "status": [], "meter_values": [], "refused": [], "errors": [], "messages": 0}
        log.info("OCPP: a wallbox connected (listening only)")
        try:
            while True:
                left = self.until - time.monotonic()
                if left <= 0 or self.record["messages"] >= MAX_MESSAGES:
                    break
                try:
                    message = await asyncio.wait_for(ws.receive(), left)
                except asyncio.TimeoutError:
                    break
                if message["type"] == "websocket.disconnect":
                    break
                text = message.get("text") or (message.get("bytes") or b"").decode("utf-8", "replace")
                if len(text) > MAX_MESSAGE:
                    break
                self.record["messages"] += 1
                await self._handle(text)
        except Exception as err:  # noqa: BLE001 - a broken connection ends the session, not the server
            log.info("OCPP connection ended: %s", err)
        finally:
            self._ws, self.connected = None, False
            try:
                await ws.close(code=1000)
            except Exception:  # noqa: BLE001 - already closed
                pass

    async def _send(self, message: list) -> None:
        assert self._ws is not None
        await self._ws.send_text(json.dumps(message))

    async def _request(self, action: str, payload: dict) -> None:
        """The only way OpenAmpere sends a request: reading ones only."""
        if action not in READ_REQUESTS:
            raise ValueError(f"OpenAmpere only listens over OCPP, {action} is not sent")
        request_id = uuid.uuid4().hex[:12]
        self._pending[request_id] = action
        await self._send([CALL, request_id, action, payload])

    async def _handle(self, text: str) -> None:
        try:
            message = json.loads(text)
            kind, request_id = message[0], str(message[1])
        except (ValueError, TypeError, IndexError, KeyError):
            self.record["errors"].append("not an OCPP message")
            return
        if kind == CALL and len(message) >= 4:
            await self._answer(request_id, str(message[2]), message[3] if isinstance(message[3], dict) else {})
        elif kind == RESULT and len(message) >= 3:
            self._result(self._pending.pop(request_id, None), message[2] if isinstance(message[2], dict) else {})
        elif kind == ERROR:
            action = self._pending.pop(request_id, None)
            self.record["errors"].append(f"{action or '?'}: {message[2] if len(message) > 2 else '?'}")

    async def _answer(self, request_id: str, action: str, payload: dict) -> None:
        record = self.record
        if action == "BootNotification":
            first = record["boot"] is None
            record["boot"] = payload
            await self._send([RESULT, request_id, {"status": "Pending", "currentTime": _now_iso(),
                                                   "interval": HEARTBEAT_S}])
            if first:  # Pending: the central system may now retrieve information
                await self._request("GetConfiguration", {})
                await self._request("GetCompositeSchedule", {"connectorId": 0, "duration": 86400})
        elif action == "Heartbeat":
            await self._send([RESULT, request_id, {"currentTime": _now_iso()}])
        elif action in ("StatusNotification", "MeterValues"):
            key = "status" if action == "StatusNotification" else "meter_values"
            record[key] = [*record[key], payload][-KEEP:]
            await self._send([RESULT, request_id, {}])
        else:  # e.g. Authorize or StartTransaction: no decision from a listener (and no card number kept)
            record["refused"] = [*record["refused"], action][-KEEP:]
            await self._send([ERROR, request_id, "NotSupported", "OpenAmpere only listens (diagnostics)", {}])

    def _result(self, action: str | None, payload: dict) -> None:
        if action == "GetConfiguration":
            self.record["configuration"] = payload.get("configurationKey") or []
            self.record["unknown_keys"] = payload.get("unknownKey") or []
        elif action == "GetCompositeSchedule":
            self.record["schedule"] = payload


def ocpp_markdown(report: dict) -> list[str]:
    """The OCPP part of the diagnostics report (already masked by privacy.py), German like the rest of the report."""
    boot = report.get("boot") or {}
    status = [s.get("status") for s in report.get("status") or [] if isinstance(s, dict)]
    lines = [f"- Wallbox {report.get('charge_point') or '?'}: {boot.get('chargePointVendor') or '?'} "
             f"{boot.get('chargePointModel') or '?'}, Firmware {boot.get('firmwareVersion') or '?'}, "
             f"{report.get('messages', 0)} Nachrichten",
             f"- Zustand: {', '.join(dict.fromkeys(str(s) for s in status)) or 'nicht gemeldet'}",
             f"- Einstellungen gelesen: {len(report.get('configuration') or [])}"]
    if report.get("refused"):
        lines.append(f"- Nicht beantwortet (OpenAmpere entscheidet nichts): {', '.join(report['refused'])}")
    if report.get("errors"):
        lines.append(f"- Fehler: {'; '.join(report['errors'])}")
    details = {k: report.get(k) for k in ("boot", "configuration", "unknown_keys", "schedule", "status", "meter_values")}
    return lines + ["", "<details><summary>Meldungen der Wallbox</summary>", "", "```json",
                    json.dumps(details, ensure_ascii=False), "```", "", "</details>"]
