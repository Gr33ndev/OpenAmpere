# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Charging the battery from the grid when electricity is cheap (dynamic tariff) or in a fixed time window.

Safety rules:
- Only through the inverter's remote control with a short watchdog timeout. If OpenAmpere stops, the
  inverter returns to its normal mode by itself after REMOTE_TIMEOUT_S. Nothing is stored permanently.
  SAJ has no such remote control: a short charge slot that OpenAmpere extends ends by itself instead (#377).
- The watchdog may leave the remote control switched on (#141): OpenAmpere remembers in storage that it switched
  it on and switches it off until that worked, also after a restart or a new connection.
- The inverter may bring back OpenAmpere's values after its own restart (#143): for a few minutes after every new
  connection, they are switched off again while OpenAmpere does not charge.
- Needs the control switch, respects the test mode and logs every start and stop.
- Verifies after the first commands that the battery really charges; otherwise it stops and reports
  (the sign of the remote power command is not verified on every device yet).
- Never discharges into the grid: it only charges; afterwards the inverter runs as before.
- FoxESS: the setpoint targets the battery where the inverter has the newer layout of 46001, otherwise the AC side
  as before, where PV is curtailed once the battery takes no more; the control log says so once per charge (#509).
- Several inverters (#619): every battery that OpenAmpere may control is charged by a GridCharging of its own, with
  the same rules and a remote command of its own (REMOTE_COMMAND for the first inverter, REMOTE_COMMANDS_EXTRA by id
  for the others). The charging power and battery size of the settings are those of all batteries together, shared
  by the size of each battery (equally while one is unknown). Charging them together keeps one battery from
  discharging into the other one while it is charged.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

from .control import LOW_RESERVE_MIN, check_limits, control_lock
from .drivers.modbus import ModbusIllegalError, friendly_error
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
REMOTE_COMMANDS_EXTRA = "remote_command_extra"  # meta: the same for each further inverter, by its id (#619)
AFTER_CONNECT_S = 600  # after a new connection, look this long for OpenAmpere's values brought back by the inverter
STARTED = "Laden gestartet"  # start of the result logged when charging starts, see tick()
# meta of 0.23.1, which raised the backup reserve for grid charging (#315, rolled back in #320): a reserve still raised
# is set back, and what it had learned is removed
RAISED_RESERVE = "grid_charging_reserve"
STOPS_AT_RESERVE = "grid_charging_stops_at_reserve"


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
        # NaN or Infinity in the JSON: a maximum price of NaN would never match and Infinity none at all (#351)
        if not math.isfinite(s.battery_kwh) or (s.max_price_ct is not None and not math.isfinite(s.max_price_ct)):
            raise ValueError
    except (TypeError, ValueError, OverflowError):  # OverflowError: Infinity in the JSON
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


def chargeable(runtime: Runtime) -> list:
    """The further inverters whose battery OpenAmpere charges as well (#619): one with a battery that is not part of
    the first one's (a parallel system charges through the first one) and that may be controlled, as far as known."""
    return [e for e in runtime.collector.extras
            if e.has_battery and not e.entry.in_first and (e.device is None or e.device.supports_control)]


def battery_sizes(runtime: Runtime) -> dict | None:
    """The size (kWh) of each battery charged, by ExtraCollector (None for the first one), when every one is entered;
    None with one inverter or while one size is unknown."""
    extras = chargeable(runtime)
    sizes = {None: runtime.config.inverter.capacity_kwh, **{id(e): e.entry.capacity_kwh for e in extras}}
    return sizes if extras and all(size > 0 for size in sizes.values()) else None


def battery_share(runtime: Runtime, source=None) -> float:
    """The part of the charging power and battery size of the settings for one battery: by the size of each battery
    that is charged, equally while one size is unknown; 1 with one inverter."""
    extras = chargeable(runtime)
    if not extras:
        return 1.0
    sizes = {None: runtime.config.inverter.capacity_kwh, **{id(e): e.entry.capacity_kwh for e in extras}}
    mine = sizes.get(None if source is None else id(source))
    if mine is None:  # not charged (any more)
        return 0.0
    if all(size > 0 for size in sizes.values()):
        return mine / sum(sizes.values())
    return 1 / len(sizes)


def command_of(runtime: Runtime, source=None) -> dict:
    """OpenAmpere's last remote command to the first inverter, or to a further one (source: its ExtraCollector)."""
    if source is None:
        return runtime.storage.get_meta(REMOTE_COMMAND) or {}
    commands = runtime.storage.get_meta(REMOTE_COMMANDS_EXTRA)
    command = commands.get(source.entry.id) if isinstance(commands, dict) else None
    return command if isinstance(command, dict) else {}


def set_command_of(runtime: Runtime, command: dict, source=None) -> None:
    if source is None:
        runtime.storage.set_meta(REMOTE_COMMAND, command)
        return
    commands = runtime.storage.get_meta(REMOTE_COMMANDS_EXTRA)
    commands = dict(commands) if isinstance(commands, dict) else {}
    commands[source.entry.id] = command
    runtime.storage.set_meta(REMOTE_COMMANDS_EXTRA, commands)


class GridCharging:
    """Grid charging of one battery: the first inverter's, or a further one's (source: its ExtraCollector, #619)."""

    def __init__(self, runtime: Runtime, source=None) -> None:
        self.runtime = runtime
        self.source = source
        self.active = False  # remote control currently commanded by us
        self.started_at: float | None = None
        self.start_soc: float | None = None
        self.last_error: str | None = None
        self.verified = False  # the battery charged after the start of this session
        self.full_at_soc: float | None = None  # state of charge at which the battery took no more
        self._limit: tuple[float, float] | None = None  # (read at, the inverter's charge limit in %)
        self._dry_logged_quarter: int | None = None
        self._lock = asyncio.Lock()
        self._owner: asyncio.Task | None = None
        self._refused_quarter: int | None = None  # a refused command is not tried again in the same quarter
        self._target_logged: tuple | None = None  # remote control target and fallback last logged this charge (#509)
        self._restore_failed = False  # setting a reserve raised by 0.23.1 back failed: logged once, tried on every tick

    @property
    def collector(self):
        return self.source if self.source is not None else self.runtime.collector

    def _who(self) -> dict:
        """Which battery a log entry is about, once there is more than one inverter (#619)."""
        if self.source is not None:
            return {"inverter": self.source.entry.name or self.source.entry.id}
        if self.runtime.collector.extras:
            return {"inverter": self.runtime.config.inverter.name or "main"}
        return {}

    def _command(self) -> dict:
        return command_of(self.runtime, self.source)

    def pending(self) -> bool:
        """OpenAmpere's command to this battery is not released yet."""
        command = self._command()
        return self.active or (bool(command) and not command.get("released"))

    def _set_command(self, command: dict) -> None:
        set_command_of(self.runtime, command, self.source)

    def _mine(self, s: ChargingSettings) -> tuple[int, float]:
        """This battery's charging power (W) and size (kWh): its share of the settings, which are those of all
        batteries together (#619); never below the smallest power a command may have."""
        share = battery_share(self.runtime, self.source)
        power = round(s.power_w * share)
        device = self.collector.device
        if device is not None and device.rated_power_w:  # never more than this inverter takes
            power = min(power, device.rated_power_w)
        # the size entered for this inverter when all are known: the size on the charging page may still be the one
        # battery's from before a further inverter was added
        sizes = battery_sizes(self.runtime)
        size = sizes.get(None if self.source is None else id(self.source)) if sizes else None
        return max(MIN_CHARGE_W, power), size if size else s.battery_kwh * share

    # ---- settings ------------------------------------------------------------

    def _power_limits(self) -> tuple[int | None, int | None]:
        """The inverter's rated power (None while no device is known) and the battery's allowed charging power."""
        device = self.runtime.collector.device
        battery_max_w = round(self.runtime.config.battery.max_charge_kw * 1000) or None
        if self.runtime.collector.extras:  # all batteries together (#619): the first inverter does not limit the sum
            return None, battery_max_w
        return (device.rated_power_w if device else None), battery_max_w

    @property
    def settings(self) -> ChargingSettings:
        """Checked like a change in the app also when read: the stored values may come from a restored backup."""
        stored = self.runtime.storage.get_meta("grid_charging") or {}
        try:
            settings = validate(stored if isinstance(stored, dict) else {}, None)
        except ValueError as err:
            log.warning("stored grid charging settings ignored: %s", err)
            return ChargingSettings()  # off
        # never more than the inverter and the battery allow now: a restored backup or a lower battery limit set
        # after saving must not lead to a command above it (#351). Lowered, not refused: charging goes on.
        rated_w, battery_max_w = self._power_limits()
        limit = min(rated_w or 15_000, battery_max_w or 15_000)
        if settings.power_w > limit:
            if limit < MIN_CHARGE_W:
                log.warning("stored grid charging settings ignored: the allowed charging power is below %s W",
                            MIN_CHARGE_W)
                return ChargingSettings()  # off
            settings.power_w = limit
        return settings

    def save(self, raw: dict, source: str | None = None) -> ChargingSettings:
        """source: who asked for it if not the web app, e.g. the name of an app's access token."""
        settings = validate(raw, *self._power_limits())
        old = self.settings
        self.runtime.storage.set_meta("grid_charging", asdict(settings))
        self.full_at_soc, self._limit = None, None  # new settings: try again, read the charge limit again (#242)
        extra = {**({"source": source} if source else {}), **self._soc_now()}
        if old.enabled != settings.enabled:
            self.runtime.storage.log_control("grid_charging_switch", {"from": {"enabled": old.enabled},
                                                                      "to": {"enabled": settings.enabled}, **extra},
                                             False, "ok")
        # every other change too (e.g. "Laden bis" during a session), so the log explains why charging ended (#322)
        changed = {k for k, v in asdict(settings).items() if k != "enabled" and getattr(old, k) != v}
        if changed:
            self.runtime.storage.log_control("grid_charging_settings", {
                "from": {k: getattr(old, k) for k in sorted(changed)},
                "to": {k: getattr(settings, k) for k in sorted(changed)}, **extra}, False, "ok")
        return settings

    def _soc_now(self) -> dict:
        """The state of charge at the time, for the control log: it makes the entries easier to follow (#322)."""
        snap = self.collector.own  # this battery's, not the combined one (#619)
        soc = snap.battery_soc if snap else None
        return {"soc": round(soc)} if soc is not None else {}

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
        power_w, battery_kwh = self._mine(s)
        needed_wh = (target - soc) / 100 * battery_kwh * 1000 / EFFICIENCY
        count = math.ceil(needed_wh / (power_w * QUARTER / 3600))
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

    @contextlib.asynccontextmanager
    async def _exclusive(self):
        """One tick or stop at a time (the watchdog, the app and the API for other apps call them): otherwise a tick
        that started before grid charging was switched off could switch the remote control on again after the
        stop. Re-entrant, so a tick can stop."""
        task = asyncio.current_task()
        if self._owner is task:
            yield
            return
        async with self._lock:
            self._owner = task
            try:
                yield
            finally:
                self._owner = None

    async def tick(self, now: float | None = None) -> None:
        async with self._exclusive():
            await self._tick(now)

    async def stop(self, reason: str, *, error: bool = False) -> None:
        async with self._exclusive():
            await self._stop(reason, error=error)

    async def _tick(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        runtime, s = self.runtime, self.settings
        collector = self.collector
        snap = collector.own  # this battery's, not the combined one (#619)
        power_w = self._mine(s)[0]
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
        if self._refused_quarter == quarter:  # refused once this quarter: only switch off what may still be on
            await self.stop(self.last_error or "")
            return
        if control.dry_run:
            await self.stop("Testmodus ist an")  # also switched on during charging
            if self._dry_logged_quarter != quarter:
                self._dry_logged_quarter = quarter
                runtime.storage.log_control("grid_charging", {"from": {}, "to": {"power_w": power_w}, **self._who()},
                                            True, f"würde laden ({plan['reason']}) – Testmodus")
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
        # remembered before writing: a command that half worked must be switched off as well. With it what a driver
        # needs to put the owner's settings back (SAJ charges through a stored time slot, #377), also after a restart
        # in the control lock: a work mode the owner chooses meanwhile goes into what is put back (SAJ, #493) and must
        # not be overwritten by the values this command started with
        failed: Exception | None = None
        async with control_lock(runtime):
            previous = self._command()
            restore = None if previous.get("released") else previous.get("restore")
            command = {"ts": now, "power_w": CHARGE_SIGN * power_w, "timeout_s": REMOTE_TIMEOUT_S, "released": None,
                       **({"restore": restore} if restore else {})}
            self._set_command(command)
            try:
                restore_now = await collector.driver.set_remote_power(CHARGE_SIGN * power_w, REMOTE_TIMEOUT_S,
                                                                      restore=restore)
                if restore_now and restore_now != restore:
                    self._set_command({**command, "restore": restore_now})
            except Exception as exc:  # noqa: BLE001
                failed = exc
                # what the half-written command changed, so a release after a restart puts it back as well (#491)
                try:
                    kept = collector.driver.remote_restore()
                except (AttributeError, ConnectionError):  # a driver without it, or a device not detected yet
                    kept = None
                if kept and kept != restore:
                    self._set_command({**command, "restore": kept})
        if failed is not None:
            err = friendly_error(failed)  # the technical text goes to the log, not into the app
            log.warning("grid charging command failed: %s", failed)
            # a command that half worked (e.g. remote control switched on, the power refused) must not stay on with
            # whatever power is set in the inverter: switched off at once, logged, and not repeated this quarter
            self._refused_quarter = quarter
            if not self.active:
                runtime.storage.log_control("grid_charging", {"from": {}, "to": {"power_w": power_w}, **self._who()},
                                            False, f"Befehl abgelehnt: {err}")
            await self.stop(f"Befehl abgelehnt: {err}", error=True)
            return
        if not self.active:
            self.active, self.started_at, self.start_soc, self.last_error = True, now, snap.battery_soc if snap else None, None
            self.verified, self._target_logged = False, None
            runtime.storage.log_control("grid_charging", {"from": {}, "to": {"power_w": power_w,
                                                                              "target_soc": s.target_soc},
                                                          **self._soc_now(), **self._who()},
                                        False, f"Laden gestartet ({plan['reason']})")
        self._log_target(collector.driver)

    async def _stop(self, reason: str, *, error: bool = False) -> None:
        """Ends charging: switches the remote control off, then sets a reserve raised by 0.23.1 back (#320)."""
        await self._release(reason, error=error)
        await self._restore_reserve()

    async def _release(self, reason: str, *, error: bool = False) -> None:
        """Ends charging and switches the remote control off. Called on every tick without charging, so a switch-off
        that failed or was skipped (restart, new driver object after an IP change) is done later (#141), and
        OpenAmpere's values that come back after a new connection are switched off again (#143)."""
        if error:
            self.last_error = reason
        collector = self.collector
        command = self._command()
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
            async with control_lock(self.runtime):  # not while a work mode for the end of charging is being kept
                switched_off = await collector.driver.release_remote_power(command.get("timeout_s", REMOTE_TIMEOUT_S),
                                                                           restore=command.get("restore"))
        except ModbusIllegalError as err:  # cannot write at all (read-only proxy): trying again does not help
            switched_off, failed = False, err
        except Exception as err:  # noqa: BLE001 - tried again on the next tick
            log.warning("could not switch off the remote control: %s", err)
            if was_active:  # with the reason why charging ended, so the log tells what happened (#322)
                self._log_stop(f"Beenden fehlgeschlagen ({err}); OpenAmpere versucht es gleich noch einmal. "
                               f"Grund für das Ende: {reason}")
            return
        else:
            failed = None
        if command:
            self._set_command({**command, "released": time.time()})
        if failed is not None:
            if was_active or pending:
                self._log_stop(f"Beenden fehlgeschlagen ({failed}); die Fernsteuerung lässt sich nicht abschalten. "
                               f"Grund für das Ende: {reason}")
        elif was_active:
            self._log_stop(f"Laden beendet: {reason}")
        elif switched_off:
            reason = ("Sie war vom Laden aus dem Netz noch an" if pending else "Der Wechselrichter hatte die Werte von "
                      "OpenAmpere wieder eingeschaltet, zum Beispiel nach einem Neustart")
            self._log_stop(f"Fernsteuerung nachträglich abgeschaltet: {reason}")

    async def _restore_reserve(self) -> None:
        """0.23.1 raised the backup reserve for grid charging (#315, rolled back in #320). A reserve it left raised is
        set back to the owner's value on the next tick, also with the master switch off or in test mode (the safe
        direction, like releasing the remote control), and only if it still has the value OpenAmpere wrote: a reserve
        the owner or another device changed in the meantime stays. Tried again on the next tick if the inverter does
        not answer. Only a value within the limits is written back, and only to a device OpenAmpere may control. A
        reserve below 10 % counts as allowed here: it was the inverter's own value before 0.23.1 raised it (#313)."""
        runtime, collector = self.runtime, self.runtime.collector
        if self.source is not None:  # 0.23.1 knew only one inverter
            return
        if runtime.storage.get_meta(STOPS_AT_RESERVE):
            runtime.storage.delete_meta(STOPS_AT_RESERVE)
        raised = runtime.storage.get_meta(RAISED_RESERVE)
        if not raised or collector.driver is None or not collector.connected:
            return
        if collector.device is None or not collector.device.supports_control:
            return
        if not _reserve_pair(raised):
            log.warning("not setting the backup reserve back: the remembered values are not valid")
            runtime.storage.delete_meta(RAISED_RESERVE)
            return
        driver = collector.driver
        details: dict | None = None
        async with control_lock(runtime):
            try:
                settings = await driver.read_settings()
                current = settings.min_soc_on_grid
                if current is None:
                    return  # unreadable right now: tried again on the next tick
                if current == raised["raised"]:
                    try:
                        check_limits({"min_soc": settings.min_soc, "max_soc": settings.max_soc,
                                      "min_soc_on_grid": raised["original"]}, low_reserve=True)
                    except ValueError as err:  # e.g. the charge limit was lowered since: the reserve stays
                        log.warning("not setting the backup reserve back to %s %%: %s", raised["original"], err)
                        runtime.storage.delete_meta(RAISED_RESERVE)
                        return
                    await driver.write_soc_limits(min_soc_on_grid=raised["original"])
                    details = {"from": {"min_soc_on_grid": current}, "to": {"min_soc_on_grid": raised["original"]}}
                    result = await self._read_back_reserve(raised["original"])
                elif current != raised["original"]:  # changed by the owner or another device: their value stays
                    details = {"from": {"min_soc_on_grid": raised["raised"]}, "to": {"min_soc_on_grid": current},
                               "kept": True}
                    result = f"nicht zurückgestellt: die Notstrom-Reserve wurde inzwischen auf {current} % geändert"
                # current == original: the raise never took effect, nothing to set back
            except Exception as err:  # noqa: BLE001 - tried again on the next tick
                if not self._restore_failed:
                    self._restore_failed = True
                    log.warning("could not set the backup reserve back after grid charging: %s", err)
                return
        self._restore_failed = False
        runtime.storage.delete_meta(RAISED_RESERVE)
        if details is not None:
            runtime.storage.log_control("grid_charging_reserve", details, False, result)

    async def _read_back_reserve(self, expected: int) -> str:
        try:
            value = (await self.runtime.collector.driver.read_settings()).min_soc_on_grid
        except Exception:  # noqa: BLE001
            return "Rücklesen fehlgeschlagen"
        return "ok" if value == expected else f"Rücklesen abweichend: {value} %"

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
            limit = (await self.collector.driver.read_settings()).max_soc
        except Exception:  # noqa: BLE001 - only decides whether "not charging" is a fault
            limit = None
        return float(limit) if limit else 100.0

    def _log_target(self, driver) -> None:
        """A FoxESS that charges with target AC instead of the battery (#509): logged at the start and whenever the
        target or its reason changes during a charge (e.g. after a new connection). Then the setpoint applies to the
        inverter's AC side, and in the sun PV is curtailed once the battery takes no more. A device that did not
        answer for now is tried again with the next command and not logged."""
        target, fallback = getattr(driver, "remote_target", None), getattr(driver, "remote_fallback", None)
        if fallback == "unanswered" or (target, fallback) == self._target_logged:
            return
        self._target_logged = (target, fallback)
        if target != "ac" or not fallback:
            return
        if fallback == "legacy":
            reason = "Bei der älteren Registerbelegung ist nicht bekannt, ob der Wechselrichter das Ziel Speicher kennt"
        elif fallback == "no_countdown":
            reason = ("Der Wechselrichter meldet keinen Zähler der Zeitbegrenzung (46007), seine Firmware kennt das Ziel "
                      "Speicher vielleicht nicht")
        else:
            reason = "Der Wechselrichter hat das Ziel Speicher nicht übernommen"
        self.runtime.storage.log_control("grid_charging", {"from": {}, "to": {"target": "ac"}, **self._who()}, False,
                                         f"Lädt mit Ziel Wechselrichter-Ausgang statt Speicher: {reason}. Scheint die "
                                         "Sonne, regelt der Wechselrichter die Solaranlage ab, sobald der Speicher "
                                         "nichts mehr aufnimmt.")

    def _log_stop(self, result: str) -> None:
        snap = self.collector.own  # this battery's, not the combined one (#619)
        self.runtime.storage.log_control("grid_charging", {"from": {"soc": self.start_soc},
                                                           "to": {"soc": snap.battery_soc if snap else None},
                                                           **self._who()},
                                         False, result)

    def view(self, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        snap = self.collector.own  # this battery's, not the combined one (#619)
        plan = self.plan(now, snap.battery_soc if snap else None, self._limit[1] if self._limit else None)
        if self.full_at_soc is not None and not self.active:  # paused at the charge limit: say so (#242)
            plan = {"quarters": [], "reason": self._full_reason()}
        return {"settings": asdict(self.settings), "active": self.active, "last_error": self.last_error,
                "plan": {**plan, "quarters": plan["quarters"][:96]}}


class AllGridCharging:
    """Grid charging of every battery OpenAmpere controls (#619): the first inverter's and each further one's, with the
    settings of all together. With one inverter it is the first one's GridCharging and nothing else."""

    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime
        self.main = GridCharging(runtime)
        self._extra: dict[str, GridCharging] = {}
        # chargers of batteries no longer charged whose command is not released yet: released on the next ticks
        # (a SAJ has no watchdog, only its release puts back the owner's mode and slot, #377)
        self._dropped: list[GridCharging] = []

    def chargers(self) -> list[GridCharging]:
        """The first battery's and one for each further one charged now; one made again for a new ExtraCollector
        (its settings changed). One that drops out (marked as part of the first one, no control any more) is kept to
        be released while its command is pending."""
        current = {}
        for extra in chargeable(self.runtime):
            charger = self._extra.get(extra.entry.id)
            current[extra.entry.id] = charger if charger is not None and charger.source is extra else GridCharging(
                self.runtime, extra)
        for inverter_id, charger in self._extra.items():
            if current.get(inverter_id) is not charger and charger.pending():
                self._dropped.append(charger)
        self._extra = current
        return [self.main, *current.values()]

    async def release_dropped(self) -> None:
        """Releases the batteries no longer charged (see _dropped), as long as their inverter is still read."""
        for charger in list(self._dropped):
            if charger.collector.driver is not None and charger.collector.connected:
                await charger.stop("Speicher wird nicht mehr aus dem Netz geladen")
            if not charger.pending() or charger.collector._task is None:  # released, or no longer read at all
                self._dropped.remove(charger)

    async def release_removed(self, keep: set[str]) -> None:
        """Before further inverters are replaced (their settings changed or removed): releases the charge of every one
        not in `keep` while its collector still runs."""
        for inverter_id, charger in list(self._extra.items()):
            if inverter_id not in keep:
                await charger.stop("Wechselrichter entfernt oder als Teil des ersten markiert")

    @property
    def settings(self) -> ChargingSettings:
        return self.main.settings

    def save(self, raw: dict, source: str | None = None) -> ChargingSettings:
        settings = self.main.save(raw, source)
        for charger in self.chargers()[1:]:
            charger.full_at_soc, charger._limit = None, None
        return settings

    @property
    def active(self) -> bool:
        return any(c.active for c in self.chargers())

    @property
    def last_error(self) -> str | None:
        return next((c.last_error for c in self.chargers() if c.last_error), None)

    async def tick(self, now: float | None = None) -> None:
        for charger in self.chargers():
            await charger.tick(now)
        await self.release_dropped()

    async def stop(self, reason: str, *, error: bool = False) -> None:
        for charger in [*self.chargers(), *self._dropped]:
            try:
                await charger.stop(reason, error=error)
            except Exception:  # noqa: BLE001 - every battery is released, one error must not skip the others
                log.exception("could not stop grid charging")

    def view(self, now: float | None = None) -> dict:
        view = self.main.view(now)
        chargers = self.chargers()
        if len(chargers) > 1:
            view["active"], view["last_error"] = self.active, self.last_error
            view["batteries"] = [{"id": "main" if c.source is None else c.source.entry.id,
                                  "name": self.runtime.config.inverter.name if c.source is None else c.source.entry.name,
                                  "active": c.active, "last_error": c.last_error, "reason": c.view(now)["plan"]["reason"]}
                                 for c in chargers]
        return view


def _reserve_pair(raised) -> bool:
    """The reserve 0.23.1 remembered (#315): whole percentages within the limits of a reserve already set on the
    inverter (#313). Anything else (e.g. from an old or edited database) is not written to the inverter."""
    if not isinstance(raised, dict):
        return False
    original, value = raised.get("original"), raised.get("raised")
    return (all(type(v) is int for v in (original, value)) and LOW_RESERVE_MIN <= original <= 100
            and 0 <= value <= 100)
