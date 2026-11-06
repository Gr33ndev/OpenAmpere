# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""go-e Charger (HOME, Gemini) over its local HTTP API, read only (#597).

Keys and units as facts (nothing copied): go-e's public API descriptions (goecharger/go-eCharger-API-v1 and
go-eCharger-API-v2 on GitHub, no license: facts only), the units of API v2 as evcc's type definitions read them
(evcc-io/evcc, charger/go-e/types2.go, MIT; evcc's go-e charger itself is not MIT and was not used) and the API v1
library cathiele/goecharger (MIT). See docs/devices/goe/charger.md for what is confirmed and what is open.

- API v2 (hardware V3 and newer): GET /api/status, the local HTTP API v2 has to be switched on in the go-e app. With
  `filter` (firmware 051.4 and newer) only the keys OpenAmpere needs. `car` 1 no car, 2 charging, 3 waiting for the car, 4 charge finished, 5
  error; `nrg` voltages L1–L3 and N (V), currents L1–L3 (A), powers L1–L3 and N, then the total (W); `wh` energy since
  the car was connected (Wh); `amp` the current it offers (A); `ama` its highest current (A); `fwv` firmware; `cdi` duration of the charge
  ({"type": 1, "value": ms}).
- API v1 (all hardware, the only one of hardware V2; also switched on in the app): GET /status, values as strings. `car` as above (1–4), `nrg`
  voltages (V), currents (0.1 A), powers of the phases (0.1 kW), the total (0.01 kW); `dws` energy since the car was
  connected (10 Ws); `amp` (A); `fwv`.

Only GET to these two paths, nothing else: the API writes with GET /api/set and /mqtt, which OpenAmpere never asks.
One request per read, no connection held. No redirect is followed and no proxy used: the device is in the home network.
"""

from __future__ import annotations

import asyncio
import http.client
import json
import math
import urllib.error
import urllib.request

from ..outbound import fetch_within
from .base import Reading, WallboxUnreachable

PORT = 80
V2_KEYS = ("car", "nrg", "wh", "amp", "ama", "fwv", "cdi")
V2_PATH = "/api/status?filter=" + ",".join(V2_KEYS)
V1_PATH = "/status"
TIMEOUT_S = 3.0
DEADLINE_S = 6.0
MAX_ANSWER_BYTES = 64 * 1024  # the full v1 answer is a few kB; the filtered v2 one less than one

CAR_STATE = {1: "idle", 2: "charging", 3: "plugged_in", 4: "finished", 5: "fault"}
PHASE_ON_V = 100.0
PHASE_ACTIVE_A = 1.0
CHARGING_W = 100.0
# above these a value is a wrong unit or a broken answer, not a measurement: not shown
MAX_PLAUSIBLE_V = 400.0
MAX_PLAUSIBLE_A = 80.0
MAX_PLAUSIBLE_W = 30_000.0  # the go-e Charger charges with at most 22 kW
MAX_PLAUSIBLE_KWH = 200.0  # one charge
MAX_PLAUSIBLE_S = 7 * 86400  # one charge


class NotGoe(ValueError):
    """An answer that is not a go-e Charger's status."""


def _num(value) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value) if isinstance(value, (int, float, str)) else None
    except (ValueError, OverflowError):
        return None
    # JSON allows NaN and Infinity (1e400): no measurement, and int() of it would fail
    return number if number is not None and math.isfinite(number) else None


def _plausible(value: float | None, limit: float) -> float | None:
    return value if value is not None and 0 <= value <= limit else None


def parse(body: bytes, api: int) -> dict:
    """The answer as a dict with "api" added; NotGoe when it is not a go-e status."""
    try:
        answer = json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        raise NotGoe("no JSON") from None
    if not isinstance(answer, dict) or "car" not in answer or not isinstance(answer.get("nrg"), list):
        raise NotGoe("no go-e status")
    return {**answer, "api": api}


class GoeHttp:
    """One go-e Charger at host:port, read with status(); it has no way to write. The API that answered last is asked
    first next time (a charger with only API v1 is not asked for v2 every 10 s)."""

    _known: dict[tuple[str, int], int] = {}  # (host, port) -> API that answered last

    def __init__(self, host: str, port: int = PORT) -> None:
        self.host, self.port = host, port

    def _url(self, path: str) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{host}:{self.port}{path}"

    def _get(self, path: str) -> tuple[int, bytes]:
        request = urllib.request.Request(self._url(path), method="GET", headers={"User-Agent": "OpenAmpere"})
        try:
            status, _, _, body = fetch_within(request, timeout=TIMEOUT_S, deadline=DEADLINE_S,
                                              limit=MAX_ANSWER_BYTES, redirect=False,
                                              handlers=(urllib.request.ProxyHandler({}),))
        except urllib.error.HTTPError as err:
            err.close()  # an error answer holds the connection open otherwise
            return err.code, b""
        return status, body

    def _status_sync(self) -> dict:
        """API v2 first, then v1 (hardware V2, or v2 not switched on); the one that answered last first."""
        answered = False
        order = ((V2_PATH, 2), (V1_PATH, 1))
        if self._known.get((self.host, self.port)) == 1:
            order = order[::-1]
        for path, api in order:
            try:
                status, body = self._get(path)
            except (TimeoutError, urllib.error.URLError, OSError, http.client.HTTPException):
                if answered:
                    break
                raise WallboxUnreachable(
                    f"Die Wallbox unter {self.host} antwortet nicht. Prüfe die Adresse und ob sie im WLAN oder per "
                    "Kabel mit dem Heimnetz verbunden ist.") from None
            answered = True
            if status == 200:
                try:
                    found = parse(body, api)
                except NotGoe:
                    continue
                if len(self._known) > 64:
                    self._known.clear()
                self._known[(self.host, self.port)] = api
                return found
        raise WallboxUnreachable(
            f"Unter {self.host} antwortet ein Gerät, aber nicht wie ein go-e Charger. Prüfe die Adresse. Ist es dein "
            "go-e Charger, schalte in der go-e App die lokale HTTP API ein (v2, bei älteren Geräten v1).")

    async def status(self) -> dict:
        return await asyncio.to_thread(self._status_sync)


def _cdi_s(value) -> int | None:
    """API v2 `cdi`: {"type": 1, "value": ms} while it counts, type 0 without a duration."""
    if isinstance(value, dict) and value.get("type") == 1:
        ms = _num(value.get("value"))
        if ms is not None and 0 <= ms / 1000 <= MAX_PLAUSIBLE_S:
            return int(ms / 1000)
    return None


class GoeChargerLive:
    """The go-e Charger as one OpenAmpere shows itself (native.py), read only: never written, only the mode "manual"."""
    key = "goe"
    label = "go-e Charger"
    sold_by: tuple[str, ...] = ()
    protocol = "http"
    read_only = True
    simulated = False
    modes = ("manual",)
    port = PORT

    @classmethod
    def interpret(cls, status: dict) -> Reading:
        r = Reading()
        v1 = status.get("api") == 1
        car = _num(status.get("car"))
        r.state = CAR_STATE.get(int(car), "unknown") if car is not None else "unknown"
        r.connected = r.state in ("charging", "plugged_in", "finished")
        nrg = status.get("nrg") if isinstance(status.get("nrg"), list) else []
        values = [_num(v) for v in nrg] + [None] * max(0, 12 - len(nrg))
        r.voltage_v = [_plausible(v, MAX_PLAUSIBLE_V) for v in values[0:3]]
        r.current_a = [_plausible(None if a is None else (a / 10 if v1 else a), MAX_PLAUSIBLE_A)
                       for a in values[4:7]]
        total = values[11]
        power = None if total is None else _plausible(total * 10 if v1 else total, MAX_PLAUSIBLE_W)
        found = [v for v in r.voltage_v if v is not None]
        r.installed_phases = max(1, sum(v >= PHASE_ON_V for v in found)) if found else 3
        if power is None and total is not None:  # implausible: nothing about this charge can be trusted
            r.state = "unknown"
        r.charging = r.state == "charging" and (power or 0.0) >= CHARGING_W
        r.power_w = round(power) if r.charging and power is not None else 0.0
        if r.charging:
            r.active_phases = sum((a or 0) >= PHASE_ACTIVE_A for a in r.current_a)
        else:
            r.current_a = [0.0 if a is not None else None for a in r.current_a]
        if v1:
            dws = _num(status.get("dws"))  # 10 Ws
            session = None if dws is None else round(dws / 360_000, 3)
        else:
            wh = _num(status.get("wh"))
            session = None if wh is None else round(wh / 1000, 3)
        r.session_kwh = _plausible(session, MAX_PLAUSIBLE_KWH) if r.connected else None
        r.session_s = None if v1 or not r.connected else _cdi_s(status.get("cdi"))
        amp = _num(status.get("amp"))
        r.set_current_a = int(amp) if amp is not None and 6 <= amp <= 32 else None
        highest = _num(status.get("ama"))
        if highest is not None and 6 <= highest <= 32:
            r.max_current_a = int(highest)
            r.steps_a = list(range(6, r.max_current_a + 1))
        firmware = status.get("fwv")
        r.firmware = str(firmware)[:20] if isinstance(firmware, (str, int, float)) and firmware != "" else None
        return r
