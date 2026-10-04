"""Charging the battery from the grid when electricity is cheap (dynamic tariff) or in a fixed time window.

Safety rules:
- Only through the inverter's remote control with a short watchdog timeout. If OpenAmpere stops, the
  inverter returns to its normal mode by itself after REMOTE_TIMEOUT_S. Nothing is stored permanently.
- Needs the control switch, respects the test mode and logs every start and stop.
- Verifies after the first commands that the battery really charges; otherwise it stops and reports
  (the sign of the remote power command is not verified on every device yet).
- Never discharges into the grid: it only charges; afterwards the inverter runs as before.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

from .runtime import Runtime
from .storage import QUARTER

log = logging.getLogger(__name__)

REMOTE_TIMEOUT_S = 180  # the inverter's watchdog: without a new command it ends remote control by itself
CHARGE_SIGN = -1  # remote power: negative = battery charges (verify on site, see diagnostics)
EFFICIENCY = 0.92
VERIFY_AFTER_S = 90  # the battery must be charging this long after the first command
MIN_CHARGE_W = 200


@dataclass
class ChargingSettings:
    enabled: bool = False
    mode: str = "cheapest"  # cheapest (dynamic tariff) | window (fixed times, e.g. a night tariff)
    target_soc: int = 80
    ready_by: int = 6  # cheapest: full by this hour
    window_start: int = 22  # window: charge between these hours
    window_end: int = 6
    max_price_ct: float | None = None  # cheapest: never charge above this price
    power_w: int = 3000
    battery_kwh: float = 10.0
    legal_confirmed: bool = False


def validate(raw: dict, rated_w: int | None, battery_max_w: int | None = None) -> ChargingSettings:
    try:
        s = ChargingSettings(**{**asdict(ChargingSettings()), **raw})
        s.target_soc, s.ready_by, s.window_start, s.window_end = (int(v) for v in
                                                                  (s.target_soc, s.ready_by, s.window_start, s.window_end))
        s.power_w, s.battery_kwh = int(s.power_w), float(s.battery_kwh)
        s.max_price_ct = None if s.max_price_ct in (None, "") else float(s.max_price_ct)
        s.enabled, s.legal_confirmed = bool(s.enabled), bool(s.legal_confirmed)
    except (TypeError, ValueError):
        raise ValueError("Ungültige Einstellungen für das Laden aus dem Netz.") from None
    if s.mode not in ("cheapest", "window"):
        raise ValueError("Unbekannte Lade-Art.")
    if not 20 <= s.target_soc <= 100:
        raise ValueError("Das Ladeziel muss zwischen 20 und 100 % liegen.")
    if not all(0 <= h <= 23 for h in (s.ready_by, s.window_start, s.window_end)):
        raise ValueError("Uhrzeiten bitte als volle Stunde 0–23 angeben.")
    if not 0.5 <= s.battery_kwh <= 200:
        raise ValueError("Die Speichergröße muss zwischen 0,5 und 200 kWh liegen.")
    # the battery may allow less than the inverter: small batteries charge slower (#23)
    limit = min(w for w in (rated_w or 15_000, battery_max_w or 15_000))
    if not MIN_CHARGE_W <= s.power_w <= limit:
        raise ValueError(f"Die Ladeleistung muss zwischen {MIN_CHARGE_W} und {limit} W liegen"
                         + (" (zulässige Ladeleistung des Speichers)." if battery_max_w and battery_max_w < (rated_w or 15_000) else "."))
    if s.enabled and not s.legal_confirmed:
        raise ValueError("Bitte zuerst die rechtlichen Hinweise bestätigen.")
    return s


class GridCharging:
    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime
        self.active = False  # remote control currently commanded by us
        self.started_at: float | None = None
        self.start_soc: float | None = None
        self.last_error: str | None = None
        self._dry_logged_quarter: int | None = None

    # ---- settings ------------------------------------------------------------

    @property
    def settings(self) -> ChargingSettings:
        return ChargingSettings(**{**asdict(ChargingSettings()), **(self.runtime.storage.get_meta("grid_charging") or {})})

    def save(self, raw: dict) -> ChargingSettings:
        device = self.runtime.collector.device
        battery_max_w = round(self.runtime.config.battery.max_charge_kw * 1000) or None
        settings = validate(raw, device.rated_power_w if device else None, battery_max_w)
        old = self.settings
        self.runtime.storage.set_meta("grid_charging", asdict(settings))
        if old.enabled != settings.enabled:
            self.runtime.storage.log_control("grid_charging_switch", {"from": {"enabled": old.enabled},
                                                                      "to": {"enabled": settings.enabled}}, False, "ok")
        return settings

    # ---- planning ------------------------------------------------------------

    def _deadline(self, now: float, hour: int) -> float:
        tz = self.runtime.tz
        local = datetime.fromtimestamp(now, tz)
        target = local.replace(hour=hour, minute=0, second=0, microsecond=0)
        if target <= local:
            target += timedelta(days=1)
        return target.timestamp()

    def plan(self, now: float, soc: float | None) -> dict:
        """Quarter hours in which to charge, and why."""
        s = self.settings
        quarter = int(now // QUARTER * QUARTER)
        if soc is None:
            return {"quarters": [], "reason": "Ladestand unbekannt"}
        if soc >= s.target_soc:
            return {"quarters": [], "reason": f"Ladeziel {s.target_soc} % erreicht"}
        needed_wh = (s.target_soc - soc) / 100 * s.battery_kwh * 1000 / EFFICIENCY
        count = math.ceil(needed_wh / (s.power_w * QUARTER / 3600))
        if s.mode == "window":
            in_window = self._in_window(now, s.window_start, s.window_end)
            quarters = [quarter] if in_window else []
            reason = "im Ladefenster" if in_window else f"Ladefenster {s.window_start}–{s.window_end} Uhr"
            return {"quarters": quarters, "reason": reason, "needed_wh": round(needed_wh)}
        deadline = self._deadline(now, s.ready_by)
        tariff = self.runtime.tariffs.at(datetime.fromtimestamp(now, self.runtime.tz).date().isoformat())
        if tariff.kind == "fixed":
            return {"quarters": [], "reason": "Braucht einen dynamischen oder zeitvariablen Stromtarif (Mehr → Stromtarif)"}
        prices = self.runtime.tariffs.quarter_prices(quarter, deadline, self.runtime.tz)
        candidates = [(p, ts) for ts, p in prices.items()]
        if s.max_price_ct is not None:
            candidates = [c for c in candidates if c[0] <= s.max_price_ct]
        if not candidates:
            return {"quarters": [], "reason": "Keine Preise bis zum Zielzeitpunkt bekannt" if not prices
                    else "Kein Zeitraum unter deinem Höchstpreis"}
        chosen = sorted(ts for _, ts in sorted(candidates)[:count])
        return {"quarters": chosen, "reason": "günstigste Viertelstunden", "needed_wh": round(needed_wh),
                "prices": {ts: round(p, 2) for p, ts in candidates if ts in chosen}}

    def _in_window(self, now: float, start: int, end: int) -> bool:
        hour = datetime.fromtimestamp(now, self.runtime.tz).hour
        return start <= hour < end if start < end else hour >= start or hour < end

    # ---- execution -------------------------------------------------------------

    async def tick(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        runtime, s = self.runtime, self.settings
        collector = runtime.collector
        snap = collector.latest
        control = runtime.config.control
        device = collector.device
        allowed = (s.enabled and s.legal_confirmed and control.enabled and collector.connected
                   and device is not None and device.supports_control)
        if not allowed:
            await self.stop("Laden aus dem Netz ist aus oder die Steuerung ist nicht freigegeben")
            return
        plan = self.plan(now, snap.battery_soc if snap else None)
        quarter = int(now // QUARTER * QUARTER)
        if quarter not in plan["quarters"]:
            await self.stop(plan["reason"])
            return
        if control.dry_run:
            if self._dry_logged_quarter != quarter:
                self._dry_logged_quarter = quarter
                runtime.storage.log_control("grid_charging", {"from": {}, "to": {"power_w": s.power_w}}, True,
                                            f"würde laden ({plan['reason']}) – Testmodus")
            return
        if self.active and self.started_at and now - self.started_at > VERIFY_AFTER_S and snap is not None:
            # battery_power: + = discharging, - = charging
            if snap.battery_power is None or snap.battery_power > -MIN_CHARGE_W / 2:
                await self.stop("Der Speicher lädt trotz Befehl nicht – Laden abgebrochen. Bitte in der Diagnose "
                                 "prüfen (Fernsteuerung).", error=True)
                self.runtime.storage.set_meta("grid_charging", {**asdict(s), "enabled": False})
                return
        try:
            await collector.driver.set_remote_power(CHARGE_SIGN * s.power_w, REMOTE_TIMEOUT_S)
        except Exception as err:  # noqa: BLE001
            self.last_error = f"Befehl abgelehnt: {err}"
            log.warning("grid charging command failed: %s", err)
            return
        if not self.active:
            self.active, self.started_at, self.start_soc, self.last_error = True, now, snap.battery_soc if snap else None, None
            runtime.storage.log_control("grid_charging", {"from": {}, "to": {"power_w": s.power_w,
                                                                              "target_soc": s.target_soc}},
                                        False, f"Laden gestartet ({plan['reason']})")

    async def stop(self, reason: str, *, error: bool = False) -> None:
        if error:
            self.last_error = reason
        if not self.active:
            return
        self.active = False
        driver = self.runtime.collector.driver
        try:
            if driver is not None:
                await driver.release_remote_power()
            result = f"Laden beendet: {reason}"
        except Exception as err:  # noqa: BLE001 - the watchdog ends it anyway after REMOTE_TIMEOUT_S
            result = f"Beenden fehlgeschlagen ({err}); der Wechselrichter beendet es nach {REMOTE_TIMEOUT_S} s selbst"
        snap = self.runtime.collector.latest
        self.runtime.storage.log_control("grid_charging", {"from": {"soc": self.start_soc},
                                                           "to": {"soc": snap.battery_soc if snap else None}},
                                         False, result)

    def view(self, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        snap = self.runtime.collector.latest
        plan = self.plan(now, snap.battery_soc if snap else None)
        return {"settings": asdict(self.settings), "active": self.active, "last_error": self.last_error,
                "plan": {**plan, "quarters": plan["quarters"][:96]}}
