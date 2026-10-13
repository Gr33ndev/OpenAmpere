# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Connection to evcc (https://evcc.io), the open-source charge controller for wallboxes and heat pumps.

OpenAmpere does not control wallboxes itself. evcc does that, and OpenAmpere:
- serves evcc the site meters (grid, PV, battery) through /api/evcc/site, so evcc needs no own Modbus
  connection to the inverter,
- reads evcc's state through its REST API and shows the charge points in the app,
- forwards the user's choices (mode, charge limit, plan) to evcc.

evcc's /api/state "carries no compatibility promise", so everything is read defensively.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from .outbound import read_within
from .runtime import Runtime

log = logging.getLogger(__name__)

POLL_S = 10.0
MAX_ANSWER_BYTES = 16 * 1024 * 1024  # years of charging sessions are a few MB
DEADLINE_S = 15  # a request ends after this, also when the answer comes byte by byte (the surplus control waits)
MODES = ("off", "pv", "minpv", "now")  # accepted by old and new evcc versions (pv/minpv are aliases of smart)


def _whole(value) -> int:
    """A whole number from the app or an API client; ValueError (shown as 400) for anything else."""
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("Bitte eine ganze Zahl angeben.") from None


class EvccError(Exception):
    """A problem with evcc; .message is the text written for the owner, shown in the app."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _num(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def normalize_mode(mode: str | None, always_charge) -> str | None:
    """New evcc: smart (+ always charge) instead of pv / minpv."""
    if mode == "smart":
        return "minpv" if always_charge not in (None, "off", False) else "pv"
    return mode if mode in MODES else None


def normalize_loadpoint(index: int, lp: dict, vehicles: dict | None = None) -> dict:
    heating = bool(lp.get("chargerFeatureHeating"))
    vehicle = (vehicles or {}).get(lp.get("vehicleName") or "") or {}  # evcc settings of the connected car
    return {
        "id": index,
        "title": lp.get("title") or f"Ladepunkt {index}",
        "heating": heating,  # heat pump / heating rod in evcc: "soc" values are temperatures
        "mode": normalize_mode(lp.get("mode"), lp.get("alwaysCharge")),
        "connected": bool(lp.get("connected")),
        "charging": bool(lp.get("charging")),
        "enabled": bool(lp.get("enabled")),
        "power_w": _num(lp.get("chargePower")) or 0.0,
        "session_wh": _num(lp.get("sessionEnergy")) or _num(lp.get("chargedEnergy")),
        "session_solar_pct": _num(lp.get("sessionSolarPercentage")),
        "vehicle_name": lp.get("vehicleName") or None,
        "vehicle_title": lp.get("vehicleTitle") or None,
        "soc": _num(lp.get("vehicleSoc")),
        "range_km": _num(lp.get("vehicleRange")),
        "limit_soc": _num(lp.get("effectiveLimitSoc")) or _num(lp.get("limitSoc")),
        "phases": int(_num(lp.get("phasesActive")) or 1),
        "min_current_a": _num(lp.get("effectiveMinCurrent")) or _num(lp.get("minCurrent")) or 6.0,
        "pv_action": lp.get("pvAction"),
        "pv_remaining_s": _num(lp.get("pvRemaining")),
        "remaining_s": _num(lp.get("chargeRemainingDuration")),
        "plan_active": bool(lp.get("planActive")),
        "plan_time": lp.get("effectivePlanTime") or None,
        "plan_soc": _num(lp.get("effectivePlanSoc")),
        "min_soc": _num(vehicle.get("minSoc")) or 0.0,  # charge right away up to this, also from the grid
        "vehicle_capacity_kwh": _num(vehicle.get("capacity")),
    }


def normalize_sponsor(raw: object) -> dict | None:
    """evcc's sponsor token (#317): many wallboxes need one, the public test token ("trial") runs out after days.

    New evcc: {"status": {"name", "expiresAt", "expiresSoon"}}, old evcc: the name only. None if evcc does not say.
    """
    if isinstance(raw, str):
        return {"active": bool(raw), "trial": raw == "trial", "expires_at": None, "expires_soon": False}
    status = raw.get("status") if isinstance(raw, dict) else None
    if not isinstance(status, dict):
        return None
    name = status.get("name") if isinstance(status.get("name"), str) else ""
    expires = status.get("expiresAt") if isinstance(status.get("expiresAt"), str) else None
    if not name or (expires or "").startswith("0001-"):  # Go's zero time: no token, no date
        expires = None
    return {"active": bool(name), "trial": name == "trial", "expires_at": expires,
            "expires_soon": bool(status.get("expiresSoon"))}


def check_fatal(raw: dict) -> None:
    """EvccError if evcc could not start its devices; a missing or expired sponsor token gets its own text (#317)."""
    fatal = raw.get("fatal")
    if not fatal:
        return
    if "sponsor" in json.dumps(fatal).lower():  # "sponsorship required", class "sponsorship" (token expired)
        raise EvccError("evcc startet die Wallbox nicht, weil ein gültiges Sponsortoken fehlt. evcc verlangt es für "
                        "viele Wallboxen, und das Testtoken gilt nur wenige Tage. Trage in evcc unter Konfiguration → "
                        "Sponsoring ein gültiges Token ein und starte evcc neu.")
    raise EvccError("evcc meldet einen Fehler in seiner Einrichtung. Was genau fehlt, zeigt die Weboberfläche von evcc.")


def wants_surplus(lp: dict) -> bool:
    """A car that is plugged in, in solar mode and not yet full is waiting for (more) surplus."""
    if lp["heating"] or not lp["connected"] or lp["mode"] not in ("pv", "minpv"):
        return False
    return not (lp["soc"] is not None and lp["limit_soc"] is not None and lp["soc"] >= lp["limit_soc"])


def min_power_w(lp: dict) -> float:
    return lp["min_current_a"] * 230 * max(1, lp["phases"])


class Evcc:
    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime
        self.state: dict | None = None  # normalized
        self.error: str | None = None
        self.updated: float = 0.0
        self._cookie: str | None = None
        self._cookie_url: str | None = None  # the address the login cookie came from: never sent elsewhere

    @property
    def url(self) -> str:
        return self.runtime.config.evcc.url.rstrip("/")

    @property
    def configured(self) -> bool:
        return bool(self.url)

    # ---- HTTP ----------------------------------------------------------------

    def _http(self, method: str, path: str, body: dict | None = None) -> object:
        headers = {"Accept": "application/json", "User-Agent": "OpenAmpere"}
        if self._cookie_url != self.url:
            self._cookie, self._cookie_url = None, self.url
        if self._cookie:
            headers["Cookie"] = self._cookie
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.url + path, data=data, headers=headers, method=method)
        # the password and the login cookie go to this address only, and a huge answer must not fill the memory
        answer, raw = read_within(request, timeout=8, deadline=DEADLINE_S, limit=MAX_ANSWER_BYTES + 1, redirect=False)
        cookie = answer.get("Set-Cookie")
        if cookie and cookie.startswith("auth="):
            self._cookie = cookie.split(";", 1)[0]
        if len(raw) > MAX_ANSWER_BYTES:
            raise EvccError("Die Antwort von evcc ist zu groß.")
        return json.loads(raw) if raw.strip() else None

    def _call(self, method: str, path: str, body: dict | None = None) -> object:
        try:
            return self._http(method, path, body)
        except urllib.error.HTTPError as err:
            if err.code == 401 and self._login():
                return self._http(method, path, body)
            if err.code == 401:
                raise EvccError("evcc verlangt eine Anmeldung. Bitte das Admin-Passwort von evcc eintragen.") from None
            raise EvccError(f"evcc meldet einen Fehler ({err.code}).") from None
        except (urllib.error.URLError, TimeoutError, OSError) as err:
            raise EvccError(f"evcc ist unter {self.url} nicht erreichbar.") from err
        except json.JSONDecodeError:
            raise EvccError("Unter dieser Adresse antwortet kein evcc.") from None

    def _login(self) -> bool:
        password = self.runtime.config.evcc.password
        if not password:
            return False
        try:
            self._http("POST", "/api/auth/login", {"password": password})
            return bool(self._cookie)
        except (urllib.error.URLError, OSError):
            return False

    # ---- state -----------------------------------------------------------------

    async def refresh(self) -> dict | None:
        if not self.configured:
            self.state, self.error = None, None
            return None
        try:
            raw = await asyncio.to_thread(self._call, "GET", "/api/state")
        except EvccError as err:
            self.error = err.message
            return self.state
        fatal = None
        try:
            if isinstance(raw, dict):
                check_fatal(raw)
        except EvccError as err:
            fatal = err.message
        if not isinstance(raw, dict) or "loadpoints" not in raw:
            self.error = fatal or "Unter dieser Adresse antwortet kein evcc."
            return self.state
        vehicles = raw.get("vehicles") if isinstance(raw.get("vehicles"), dict) else {}
        loadpoints = [normalize_loadpoint(i + 1, lp, vehicles) for i, lp in enumerate(raw.get("loadpoints") or [])
                      if isinstance(lp, dict)]
        self.state = {"version": raw.get("version"), "site_title": raw.get("siteTitle"), "loadpoints": loadpoints,
                      "grid_configured": bool(raw.get("gridConfigured")), "sponsor": normalize_sponsor(raw.get("sponsor"))}
        self.error, self.updated = fatal, time.time()
        return self.state

    def fresh(self) -> dict | None:
        """State only if it is recent enough to base decisions on."""
        if self.state and time.time() - self.updated < 3 * POLL_S + 5:
            return self.state
        return None

    def view(self) -> dict:
        return {"configured": self.configured, "url": self.url or None, "error": self.error,
                "updated": self.updated or None, "state": self.state,
                "priority": self.runtime.config.evcc.priority}

    # ---- commands -----------------------------------------------------------------

    def _loadpoint(self, lp_id: int) -> dict:
        for lp in (self.state or {}).get("loadpoints", []):
            if lp["id"] == lp_id:
                return lp
        raise EvccError("Diesen Ladepunkt gibt es in evcc nicht.")

    async def command(self, lp_id: int, action: str, value=None) -> dict:
        if not self.configured:
            raise EvccError("evcc ist nicht verbunden.")
        control = self.runtime.config.control
        if not control.enabled:
            raise EvccError("Änderungen sind ausgeschaltet. Stelle oben auf „Testen“ oder „Aktiv“.")
        lp = self._loadpoint(lp_id)
        if action == "mode":
            if value not in MODES:
                raise ValueError("Unbekannter Lademodus.")
            path, method = f"/api/loadpoints/{lp_id}/mode/{value}", "POST"
        elif action == "limit_soc":
            value = _whole(value)
            if lp["heating"] and not 20 <= value <= 90:  # a temperature (#245)
                raise ValueError("Die Zieltemperatur muss zwischen 20 und 90 °C liegen.")
            if not 20 <= value <= 100:
                raise ValueError("Das Ladeziel muss zwischen 20 und 100 % liegen.")
            path, method = f"/api/loadpoints/{lp_id}/limitsoc/{value}", "POST"
        elif action in ("plan", "plan_delete", "min_soc"):
            vehicle = lp["vehicle_name"]
            if not vehicle:
                raise EvccError("Dafür muss in evcc ein Fahrzeug erkannt sein.")
            vehicle = urllib.parse.quote(vehicle, safe="")
            if action == "min_soc":
                value = _whole(value)
                if not 0 <= value <= 80:
                    raise ValueError("Die Mindestladung muss zwischen 0 und 80 % liegen.")
                path, method = f"/api/vehicles/{vehicle}/minsoc/{value}", "POST"
            elif action == "plan_delete":
                path, method = f"/api/vehicles/{vehicle}/plan/soc", "DELETE"
            else:
                try:
                    soc, when = _whole(value["soc"]), float(value["time"])
                except (TypeError, KeyError, OverflowError):
                    raise ValueError("Bitte ein Ziel zwischen 20 und 100 % und eine Zeit in der Zukunft wählen.") from None
                if not 20 <= soc <= 100 or when < time.time():
                    raise ValueError("Bitte ein Ziel zwischen 20 und 100 % und eine Zeit in der Zukunft wählen.")
                stamp = datetime.fromtimestamp(when, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                path, method = f"/api/vehicles/{vehicle}/plan/soc/{soc}/{stamp}", "POST"
        else:
            raise ValueError("Unbekannter Befehl.")
        details = {"from": {"loadpoint": lp["title"]}, "to": {"action": action, "value": value}}
        if control.dry_run:
            self.runtime.storage.log_control("evcc", details, True, "nicht gesendet (Testmodus)")
            return {**self.view(), "dry_run": True}
        await asyncio.to_thread(self._call, method, path)
        self.runtime.storage.log_control("evcc", details, False, "ok")
        await self.refresh()
        return self.view()

    def _cost(self, created, finished, energy_kwh, solar_pct) -> tuple[float, float] | None:
        """Cost of a session with the user's tariff (solar at the lost feed-in pay) and the same from the grid."""
        if not energy_kwh or not created:
            return None
        try:
            start = datetime.fromisoformat(str(created).replace("Z", "+00:00")).timestamp()
            end = datetime.fromisoformat(str(finished).replace("Z", "+00:00")).timestamp() if finished else start
        except ValueError:
            return None
        return self.runtime.tariffs.charge_cost(start, max(start, end), energy_kwh, (solar_pct or 0) / 100,
                                                self.runtime.tz)

    async def set_priority_soc(self, soc: int) -> bool:
        """evcc charges the home battery first up to this state of charge (a site setting in evcc). Respects the
        control switch and test mode and is logged, like the other commands (#219). Returns whether it was sent."""
        control = self.runtime.config.control
        if not self.configured or not control.enabled:
            return False
        details = {"from": {}, "to": {"action": "priority_soc", "value": int(soc)}}
        if control.dry_run:
            self.runtime.storage.log_control("evcc", details, True, "nicht gesendet (Testmodus)")
            return False
        await asyncio.to_thread(self._call, "POST", f"/api/prioritysoc/{int(soc)}")
        self.runtime.storage.log_control("evcc", details, False, "ok")
        return True

    async def sessions(self, limit: int = 50) -> list[dict]:
        if not self.configured:
            return []
        raw = await asyncio.to_thread(self._call, "GET", "/api/sessions")
        rows = []
        for s in raw if isinstance(raw, list) else []:
            if not isinstance(s, dict):
                continue
            duration = _num(s.get("chargeDuration"))
            energy, solar = _num(s.get("chargedEnergy")), _num(s.get("solarPercentage"))
            cost = self._cost(s.get("created"), s.get("finished"), energy, solar)
            rows.append({"created": s.get("created"), "finished": s.get("finished"),
                         "loadpoint": s.get("loadpoint"), "vehicle": s.get("vehicle") or None,
                         "energy_kwh": energy,
                         "duration_s": duration / 1e9 if duration else None,  # nanoseconds in evcc
                         "solar_pct": solar, "price": _num(s.get("price")),
                         "odometer_km": _num(s.get("odometer")), "soc_start": _num(s.get("socStart")),
                         "soc_end": _num(s.get("socEnd")), "added_range_km": _num(s.get("addedRange")),
                         "cost_eur": cost[0] if cost else None, "grid_cost_eur": cost[1] if cost else None})
        rows.sort(key=lambda r: r["created"] or "", reverse=True)
        return rows[:limit]
