# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Charging the battery from the grid when electricity is cheap (dynamic tariff) or in a fixed time window.

Safety rules:
- Only through the inverter's remote control with a short watchdog timeout. If OpenAmpere stops, the
  inverter returns to its normal mode by itself after REMOTE_TIMEOUT_S. Nothing is stored permanently.
- The watchdog may leave the remote control switched on (#141): OpenAmpere remembers in storage that it switched
  it on and switches it off until that worked, also after a restart or a new connection.
- The inverter may bring back OpenAmpere's values after its own restart (#143): for a few minutes after every new
  connection, they are switched off again while OpenAmpere does not charge.
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

from .drivers.modbus import ModbusIllegalError
from .runtime import Runtime
from .storage import QUARTER

log = logging.getLogger(__name__)

REMOTE_TIMEOUT_S = 180  # the inverter's watchdog: without a new command it ends remote control by itself
CHARGE_SIGN = -1  # remote power: negative = battery charges (verify on site, see diagnostics)
EFFICIENCY = 0.92
VERIFY_AFTER_S = 90  # the battery must be charging this long after the first command (checked once per session)
NEAR_LIMIT = 5  # % below the inverter's own charge limit: no charging there is not an error (full, tapering, #218)
RESTART_BELOW = 5  # % below the level where the battery took no more: grid charging tries again
LIMIT_READ_S = 600  # the inverter's charge limit is read at most this often (#242)
MIN_CHARGE_W = 200
REMOTE_COMMAND = "remote_command"  # meta: OpenAmpere's last remote command and when it ended it (#135, #141)
AFTER_CONNECT_S = 600  # after a new connection, look this long for OpenAmpere's values brought back by the inverter
STARTED = "Laden gestartet"  # start of the result logged when charging starts, see tick()


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
        # only real yes/no: "false" as text must not switch grid charging on (#242)
        if not isinstance(s.enabled, bool) or not isinstance(s.legal_confirmed, bool):
            raise ValueError
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
        self.verified = False  # the battery charged after the start of this session
        self.full_at_soc: float | None = None  # state of charge at which the battery took no more
        self._limit: tuple[float, float] | None = None  # (read at, the inverter's charge limit in %)
        self._dry_logged_quarter: int | None = None

    # ---- settings ------------------------------------------------------------

    @property
    def settings(self) -> ChargingSettings:
        return ChargingSettings(**{**asdict(ChargingSettings()), **(self.runtime.storage.get_meta("grid_charging") or {})})

    def save(self, raw: dict, source: str | None = None) -> ChargingSettings:
        """source: who asked for it if not the web app, e.g. the name of an app's access token."""
        device = self.runtime.collector.device
        battery_max_w = round(self.runtime.config.battery.max_charge_kw * 1000) or None
        settings = validate(raw, device.rated_power_w if device else None, battery_max_w)
        old = self.settings
        self.runtime.storage.set_meta("grid_charging", asdict(settings))
        self.full_at_soc, self._limit = None, None  # new settings: try again, read the charge limit again (#242)
        if old.enabled != settings.enabled:
            self.runtime.storage.log_control("grid_charging_switch", {"from": {"enabled": old.enabled},
                                                                      "to": {"enabled": settings.enabled},
                                                                      **({"source": source} if source else {})}, False, "ok")
        return settings

    # ---- planning ------------------------------------------------------------

    def _deadline(self, now: float, hour: int) -> float:
        tz = self.runtime.tz
        local = datetime.fromtimestamp(now, tz)
        target = local.replace(hour=hour, minute=0, second=0, microsecond=0)
        if target <= local:
            target += timedelta(days=1)
        return target.timestamp()

    def plan(self, now: float, soc: float | None, limit: float | None = None) -> dict:
        """Quarter hours in which to charge, and why. limit: the inverter's own charge limit (max SoC) in %; the
        inverter does not respect it while it is charged by remote control, so the plan stops there (#242)."""
        s = self.settings
        quarter = int(now // QUARTER * QUARTER)
        if soc is None:
            return {"quarters": [], "reason": "Ladestand unbekannt"}
        target = s.target_soc if limit is None else min(s.target_soc, limit)
        if soc >= target:
            if target < s.target_soc:
                reason = f"Ladegrenze des Wechselrichters ({round(target)} %) erreicht"
                return {"quarters": [], "reason": reason}
            return {"quarters": [], "reason": f"Ladeziel {s.target_soc} % erreicht"}
        needed_wh = (target - soc) / 100 * s.battery_kwh * 1000 / EFFICIENCY
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
        plan = self.plan(now, snap.battery_soc if snap else None, await self._charge_limit(now))
        quarter = int(now // QUARTER * QUARTER)
        if quarter not in plan["quarters"]:
            await self.stop(plan["reason"])
            return
        if control.dry_run:
            await self.stop("Testmodus ist an")  # also switched on during charging
            if self._dry_logged_quarter != quarter:
                self._dry_logged_quarter = quarter
                runtime.storage.log_control("grid_charging", {"from": {}, "to": {"power_w": s.power_w}}, True,
                                            f"würde laden ({plan['reason']}) – Testmodus")
            return
        soc = snap.battery_soc if snap else None
        if self.full_at_soc is not None:
            if soc is None or soc > self.full_at_soc - RESTART_BELOW:
                await self.stop(self._full_reason())
                return
            self.full_at_soc = None
        if self.active and not self.verified and self.started_at and now - self.started_at > VERIFY_AFTER_S and snap is not None:
            # once per session: a battery that charged and then takes less near full is fine (#218)
            # battery_power: + = discharging, - = charging
            if snap.battery_power is not None and snap.battery_power <= -MIN_CHARGE_W / 2:
                self.verified = True
            elif soc is not None and soc >= await self._upper_limit() - NEAR_LIMIT:
                # the inverter's own charge limit or a full battery, not a fault: paused, not switched off
                self.full_at_soc = soc
                await self.stop(self._full_reason())
                return
            else:
                await self.stop("Der Speicher lädt trotz Befehl nicht – Laden abgebrochen. Bitte in der Diagnose "
                                 "prüfen (Fernsteuerung).", error=True)
                self.runtime.storage.set_meta("grid_charging", {**asdict(s), "enabled": False})
                return
        # remembered before writing: a command that half worked must be switched off as well
        runtime.storage.set_meta(REMOTE_COMMAND, {"ts": now, "power_w": CHARGE_SIGN * s.power_w,
                                                  "timeout_s": REMOTE_TIMEOUT_S, "released": None})
        try:
            await collector.driver.set_remote_power(CHARGE_SIGN * s.power_w, REMOTE_TIMEOUT_S)
        except Exception as err:  # noqa: BLE001
            self.last_error = f"Befehl abgelehnt: {err}"
            log.warning("grid charging command failed: %s", err)
            return
        if not self.active:
            self.active, self.started_at, self.start_soc, self.last_error = True, now, snap.battery_soc if snap else None, None
            self.verified = False
            runtime.storage.log_control("grid_charging", {"from": {}, "to": {"power_w": s.power_w,
                                                                              "target_soc": s.target_soc}},
                                        False, f"Laden gestartet ({plan['reason']})")

    async def stop(self, reason: str, *, error: bool = False) -> None:
        """Ends charging and switches the remote control off. Called on every tick without charging, so a switch-off
        that failed or was skipped (restart, new driver object after an IP change) is done later (#141), and
        OpenAmpere's values that come back after a new connection are switched off again (#143)."""
        if error:
            self.last_error = reason
        storage, collector = self.runtime.storage, self.runtime.collector
        command = storage.get_meta(REMOTE_COMMAND) or {}
        pending = bool(command) and not command.get("released")
        was_active, self.active = self.active, False
        if collector.driver is None:  # no inverter set up any more: switched off as soon as there is one again
            if was_active:
                self._log_stop(f"Laden beendet: {reason}")
            return
        reconnected = (bool(command) and collector.connected_at is not None
                       and time.time() - collector.connected_at <= AFTER_CONNECT_S)
        if not was_active and not ((pending or reconnected) and collector.connected):
            return
        try:
            switched_off = await collector.driver.release_remote_power(command.get("timeout_s", REMOTE_TIMEOUT_S))
        except ModbusIllegalError as err:  # cannot write at all (read-only proxy): trying again does not help
            switched_off, failed = False, err
        except Exception as err:  # noqa: BLE001 - tried again on the next tick
            log.warning("could not switch off the remote control: %s", err)
            if was_active:
                self._log_stop(f"Beenden fehlgeschlagen ({err}); OpenAmpere versucht es gleich noch einmal")
            return
        else:
            failed = None
        if command:
            storage.set_meta(REMOTE_COMMAND, {**command, "released": time.time()})
        if failed is not None:
            if was_active or pending:
                self._log_stop(f"Beenden fehlgeschlagen ({failed}); die Fernsteuerung lässt sich nicht abschalten")
        elif was_active:
            self._log_stop(f"Laden beendet: {reason}")
        elif switched_off:
            reason = ("Sie war vom Laden aus dem Netz noch an" if pending else "Der Wechselrichter hatte die Werte von "
                      "OpenAmpere wieder eingeschaltet, zum Beispiel nach einem Neustart")
            self._log_stop(f"Fernsteuerung nachträglich abgeschaltet: {reason}")

    def _full_reason(self) -> str:
        soc = round(self.full_at_soc or 0)
        reason = f"Der Speicher nimmt bei {soc} % keine Ladung mehr an (Ladegrenze des Wechselrichters oder voll)"
        return reason

    async def _charge_limit(self, now: float) -> float:
        """The inverter's charge limit, read again every LIMIT_READ_S (the owner may change it in the app)."""
        if self._limit is None or now - self._limit[0] > LIMIT_READ_S:
            self._limit = (now, await self._upper_limit())
        return self._limit[1]

    async def _upper_limit(self) -> float:
        """The inverter's own charge limit (max_soc), 100 if it cannot be read."""
        try:
            limit = (await self.runtime.collector.driver.read_settings()).max_soc
        except Exception:  # noqa: BLE001 - only decides whether "not charging" is a fault
            limit = None
        return float(limit) if limit else 100.0

    def _log_stop(self, result: str) -> None:
        snap = self.runtime.collector.latest
        self.runtime.storage.log_control("grid_charging", {"from": {"soc": self.start_soc},
                                                           "to": {"soc": snap.battery_soc if snap else None}},
                                         False, result)

    def view(self, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        snap = self.runtime.collector.latest
        plan = self.plan(now, snap.battery_soc if snap else None, self._limit[1] if self._limit else None)
        if self.full_at_soc is not None and not self.active:  # paused at the charge limit: say so (#242)
            plan = {"quarters": [], "reason": self._full_reason()}
        return {"settings": asdict(self.settings), "active": self.active, "last_error": self.last_error,
                "plan": {**plan, "quarters": plan["quarters"][:96]}}
