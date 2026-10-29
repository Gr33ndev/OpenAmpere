# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Writing battery settings to the inverter: guarded by the control switches, validated and logged."""

from __future__ import annotations

import asyncio
import itertools
import logging
import time
from dataclasses import asdict

from .drivers.base import WorkMode
from .runtime import Runtime


class ControlDisabled(Exception):
    pass


class NotConnected(Exception):
    pass


class WriteFailed(Exception):
    """The inverter (or a Modbus proxy in front of it) did not accept the write."""


FIELDS = ("work_mode", "min_soc", "max_soc", "min_soc_on_grid", "backup_soc")
SOC_FIELDS = ("min_soc", "max_soc", "min_soc_on_grid")
# the reserve of the backup work mode, only on a device that keeps its own (SAJ 0x3651, #380); not a SOC_FIELD: the
# three limits above are checked against each other, this one only against the charge limit
BACKUP_SOC = "backup_soc"
BACKUP_SOC_MIN = 10
SOC_MIN = {"min_soc": 0, "max_soc": 10, "min_soc_on_grid": 10}  # lowest value per limit in %
# a backup reserve below SOC_MIN only with an explicit confirmation, every time (#313); the inverter accepts it
LOW_RESERVE_MIN = 0
# the read back is repeated after these seconds before it reports a mismatch: while the inverter is controlled remotely
# it takes a new value a few seconds later (#322)
READ_BACK_WAITS_S = (1, 2, 3)
# re-read written values at these seconds after a write to detect a second master overwriting them: an old smartbox
# fetches its values from the cloud about every 100 s, so the last check comes after more than one such round
VERIFY_AFTER_S = (45.0, 150.0)
SHARED_READ_S = 3.0  # a reading for the app is given to every request in this time, instead of one Modbus read each

log = logging.getLogger(__name__)


async def after_write():
    """Yields once at each time of VERIFY_AFTER_S after a write."""
    waited = 0.0
    for at in VERIFY_AFTER_S:
        await asyncio.sleep(at - waited)
        waited = at
        yield


def control_lock(runtime: Runtime) -> asyncio.Lock:
    """One lock per runtime: reading, checking and writing must not interleave between two users."""
    lock = getattr(runtime, "_control_lock", None)
    if lock is None:
        lock = runtime._control_lock = asyncio.Lock()
    return lock


class SharedRead:
    """Readings for the app (open without login on the home network): requests at the same time share one Modbus
    read, and its result is given out for SHARED_READ_S, so many requests cannot keep the inverter busy. Checks and
    read-backs around a write never use it; a write makes the shared result out of date."""

    def __init__(self, read) -> None:
        self._read = read
        self._task: asyncio.Task | None = None
        self._result: tuple[float, object, dict] | None = None  # (monotonic time, driver, values)

    def invalidate(self) -> None:
        self._task = None  # a reading still running belongs to the requests that already wait for it
        self._result = None

    async def get(self, driver) -> dict:
        if self._result and self._result[1] is driver and time.monotonic() - self._result[0] < SHARED_READ_S:
            return dict(self._result[2])
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(driver))
            # retrieved here, so a failed reading nobody waits for any more is not reported as never retrieved
            self._task.add_done_callback(lambda task: task.cancelled() or task.exception())
        task = self._task
        # shielded: a request that is given up (e.g. the page was closed) must not cancel the others' reading
        return dict(await asyncio.shield(task))

    async def _run(self, driver) -> dict:
        task = self._task
        values = await self._read()
        if self._task is task:  # not made out of date by a write in the meantime
            self._result = (time.monotonic(), driver, values)
        return values


def write_order(current: dict, diff: dict) -> list[str]:
    """Order of the SoC writes so that every intermediate state is valid (raising: upper limit first,
    lowering: lower limit first). Falls back to the plain order if no such sequence exists."""
    keys = [k for k in SOC_FIELDS if k in diff]
    for order in itertools.permutations(keys):
        state = dict(current)
        try:
            for key in order:
                state[key] = diff[key]
                check_limits(state)
            return list(order)
        except ValueError:
            continue
    return keys


async def _remote_active(driver) -> bool:
    check = getattr(driver, "remote_active", None)
    if check is None:
        return False
    try:
        return await check()
    except Exception:  # noqa: BLE001 - a failed check must not block the write
        return False


def check_limits(values: dict, low_reserve: bool = False) -> None:
    """FoxESS rules: 0 <= min_soc <= min_soc_on_grid < max_soc <= 100, reserve and upper limit at least 10.
    During an outage the battery may be emptied completely (#69). low_reserve: a reserve below 10 % is allowed
    (confirmed by the owner, or already set on the inverter, #313)."""
    names = {"min_soc": "Die Untergrenze im Notstrombetrieb", "max_soc": "Die Ladegrenze",
             "min_soc_on_grid": "Die Notstrom-Reserve"}
    for key in SOC_FIELDS:
        value = values.get(key)
        low = LOW_RESERVE_MIN if key == "min_soc_on_grid" and low_reserve else SOC_MIN[key]
        if value is not None and not low <= value <= 100:
            raise ValueError(f"{names[key]} muss zwischen {low} und 100 % liegen")
    lo, reserve, hi = values.get("min_soc"), values.get("min_soc_on_grid"), values.get("max_soc")
    if lo is not None and reserve is not None and reserve < lo:
        raise ValueError("Die Notstrom-Reserve darf nicht unter der Untergrenze im Notstrombetrieb liegen")
    if reserve is not None and hi is not None and hi <= reserve:
        raise ValueError("Die Ladegrenze muss über der Notstrom-Reserve liegen")


def check_backup_soc(value: int, max_soc: int | None) -> None:
    """The reserve of the backup work mode: at least 10 % like the reserve of normal operation, and not above the
    charge limit, which the battery would never reach."""
    if not BACKUP_SOC_MIN <= value <= 100:
        raise ValueError(f"Die Reserve im Backup-Modus muss zwischen {BACKUP_SOC_MIN} und 100 % liegen")
    if max_soc is not None and value > max_soc:
        raise ValueError("Die Reserve im Backup-Modus darf nicht über der Ladegrenze liegen")


class BatteryControl:
    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime
        self.external_change: dict | None = None  # values another device wrote back after our change
        self._verify_task: asyncio.Task | None = None
        self.known: dict | None = None  # the last settings read or read back after a change, for other apps (#336)
        self._known_task: asyncio.Task | None = None
        self._changes = 0  # a reading that started before a change is out of date (#342)
        self._shared = SharedRead(self._read)

    def _driver(self):
        collector = self.runtime.collector
        if collector.driver is None or not collector.connected:
            raise NotConnected("Wechselrichter ist nicht verbunden")
        return collector.driver

    async def _read(self) -> dict:
        settings = await self._driver().read_settings()
        data = asdict(settings)
        data["work_mode"] = settings.work_mode.value if settings.work_mode else None
        return data

    async def read(self, shared: bool = False) -> dict:
        """shared: for the app's requests, see SharedRead; never for a check before or after a write."""
        data = await self._shared.get(self._driver()) if shared else await self._read()
        device = self.runtime.collector.device
        has_backup = bool(device and device.backup_reserve)
        data["unreadable"] = [k for k in FIELDS if data.get(k) is None and (k != BACKUP_SOC or has_backup)]
        data["external_change"] = self.external_change
        return data

    async def latest(self, wait_s: float) -> dict | None:
        """The settings, waiting at most wait_s for a new reading, otherwise the last known ones: behind a slow Modbus
        proxy a reading can take longer than an app waits for its answer (#336). One reading at a time; a slow one
        keeps running for the next call instead of being cancelled."""
        if self._known_task is None or self._known_task.done():
            self._known_task = asyncio.create_task(self._read_known())
        try:
            await asyncio.wait_for(asyncio.shield(self._known_task), wait_s)
        except TimeoutError:
            pass
        return self.known

    async def _read_known(self) -> None:
        changes = self._changes
        try:
            settings = await self.read()
        except Exception:  # noqa: BLE001 - not connected or no answer
            settings = None
        if self._changes == changes:
            self.known = settings

    async def write(self, changes: dict, source: str | None = None, confirm_low_reserve: bool = False) -> dict:
        """source: who asked for it if not the web app, e.g. the name of an app's access token (#76).
        confirm_low_reserve: the owner confirmed a backup reserve below 10 % for this change (#313)."""
        try:
            result = await self._write(changes, source, confirm_low_reserve)
        finally:
            self._changes += 1
            self._shared.invalidate()
        self.known = result["settings"]  # what the inverter holds now, also for a change from the app (#342)
        return result

    async def _write(self, changes: dict, source: str | None, confirm_low_reserve: bool) -> dict:
        control = self.runtime.config.control
        if not control.enabled:
            raise ControlDisabled("Steuerung ist deaktiviert")
        device = self.runtime.collector.device
        if device is not None and not device.supports_control:
            raise ValueError(f"Für {device.manufacturer}-Geräte ist die Steuerung in OpenAmpere noch nicht freigegeben "
                             "(nur Anzeige).")
        unknown = set(changes) - set(FIELDS)
        if unknown:
            raise ValueError(f"Unbekannte Einstellung: {', '.join(sorted(unknown))}")

        changes = dict(changes)
        if "work_mode" in changes:
            try:
                changes["work_mode"] = WorkMode(changes["work_mode"]).value
            except ValueError:
                raise ValueError("Unbekannter Betriebsmodus") from None
            if device is not None and device.work_modes is not None and changes["work_mode"] not in device.work_modes:
                raise ValueError(f"Diesen Betriebsmodus gibt es bei {device.manufacturer}-Geräten nicht.")
        if BACKUP_SOC in changes and not (device is not None and device.backup_reserve):
            raise ValueError("Eine eigene Reserve für den Backup-Modus gibt es bei diesem Gerät nicht.")
        for key in (*SOC_FIELDS, BACKUP_SOC):
            if key in changes:
                try:
                    changes[key] = int(changes[key])
                except (TypeError, ValueError, OverflowError):  # e.g. a text, null or Infinity in the JSON
                    raise ValueError("Die Speicher-Grenzen bitte als ganze Prozentwerte angeben.") from None

        async with control_lock(self.runtime):
            driver = self._driver()
            current = await self._read()
            merged = {**current, **changes}
            if any(merged.get(k) is None for k in SOC_FIELDS) and any(k in changes for k in SOC_FIELDS):
                # without all three values the cross checks below cannot be made
                raise ValueError("Die aktuellen Speicher-Grenzen konnten nicht vollständig gelesen werden. "
                                 "Bitte später erneut versuchen.")
            reserve = changes.get("min_soc_on_grid")
            # a reserve below 10 % needs a confirmation every time it is set; one that is already on the inverter
            # does not block other changes
            lowering = (reserve is not None and LOW_RESERVE_MIN <= reserve < SOC_MIN["min_soc_on_grid"]
                        and reserve != current.get("min_soc_on_grid"))
            if lowering and not confirm_low_reserve:
                raise ConfirmationRequired("Eine Notstrom-Reserve unter 10 % bitte ausdrücklich bestätigen: Der Speicher "
                                           "wird dann tiefer entladen, das kann seine Lebensdauer verkürzen und die "
                                           "Garantie gefährden.")
            check_limits(merged, low_reserve=lowering or merged.get("min_soc_on_grid") == current.get("min_soc_on_grid"))
            if BACKUP_SOC in changes:
                check_backup_soc(changes[BACKUP_SOC], merged.get("max_soc"))
            diff = {k: v for k, v in changes.items() if current.get(k) != v}
            if not diff:
                return {"dry_run": control.dry_run, "written": {}, "settings": current}

            storage = self.runtime.storage
            snap = self.runtime.collector.latest  # the state of charge at the time, for the log (#322)
            details = {"from": {k: current.get(k) for k in diff}, "to": diff, **({"source": source} if source else {}),
                       **({"low_reserve_confirmed": True} if lowering else {}),
                       **({"soc": round(snap.battery_soc)} if snap and snap.battery_soc is not None else {})}
            if control.dry_run:
                storage.log_control("battery_settings", details, True, "nicht ausgeführt (Testmodus)")
                return {"dry_run": True, "written": diff, "settings": current}

            warning = None
            if await _remote_active(driver):
                warning = ("Ein anderes Gerät steuert den Wechselrichter gerade aktiv (Fernsteuerung). "
                           "Es kann deine Änderung überschreiben.")
            error: Exception | None = None
            result = ""
            after: dict | None = None
            try:
                if "work_mode" in diff:
                    await self._adopt_remote_restore(driver)
                    await driver.write_work_mode(WorkMode(diff["work_mode"]))
                    self._keep_remote_restore(driver)
                for key in write_order(current, diff):
                    await driver.write_soc_limits(**{key: diff[key]})
                if BACKUP_SOC in diff:
                    await driver.write_backup_soc(diff[BACKUP_SOC])
            except Exception as err:  # noqa: BLE001 - logged and translated below
                error = err
            finally:
                # always record what the inverter actually holds now, also after a partial write
                after = await self._read_back(diff if error is None else {})  # after an error: no waiting
                if after is None:
                    result = "Rücklesen fehlgeschlagen"
                else:
                    mismatch = {k: after.get(k) for k, v in diff.items() if after.get(k) != v}
                    result = "ok" if not mismatch else f"Rücklesen abweichend: {mismatch}"
                if error is not None:
                    result = f"Fehler: {error} – {result}"
                storage.log_control("battery_settings", details, False, result)

            if error is not None:
                if getattr(error, "transient", False):
                    raise WriteFailed("Keine Antwort vom Wechselrichter. Bitte prüfe die Werte und versuche es "
                                      "später erneut.") from error
                raise WriteFailed("Der Wechselrichter hat die Änderung (ganz oder teilweise) abgelehnt. Hängt er "
                                  "hinter einem schreibgeschützten Modbus-Proxy?") from error
            self.external_change = None
            self._schedule_verify(diff)
            return {"dry_run": False, "written": diff, "settings": after or current, "result": result,
                    "warning": warning}

    def _pending_command(self) -> dict:
        """OpenAmpere's last remote command while it is not released, else {}."""
        from .charging import REMOTE_COMMAND  # charging imports this module

        command = self.runtime.storage.get_meta(REMOTE_COMMAND) or {}
        return command if command and not command.get("released") else {}

    async def _adopt_remote_restore(self, driver) -> None:
        """After a restart the driver knows nothing of a charge it still runs until the next charging command: given
        what was kept, a new work mode is kept for the end of charging as before the restart (#497)."""
        restore = self._pending_command().get("restore")
        if not restore:
            return
        try:
            adopt = driver.adopt_remote_restore
        except (AttributeError, ConnectionError):  # a driver without it, or a device not detected yet
            return
        await adopt(restore)

    def _keep_remote_restore(self, driver) -> None:
        """A driver that keeps a new work mode for the end of grid charging (SAJ) has it only in memory: stored with
        the rest of what the release puts back right away, so a release after a restart puts back the mode chosen
        last (#493)."""
        from .charging import REMOTE_COMMAND  # charging imports this module

        try:
            kept = driver.remote_restore()
        except (AttributeError, ConnectionError):  # a driver without it, or a device not detected yet
            return
        command = self._pending_command()
        if kept and command:
            self.runtime.storage.set_meta(REMOTE_COMMAND, {**command, "restore": kept})

    async def _read_back(self, written: dict) -> dict | None:
        """The values after writing. A value the inverter takes a few seconds later (while it is controlled remotely)
        is read again before it counts as a mismatch (#322)."""
        try:
            after = await self._read()
        except Exception:  # noqa: BLE001
            after = None
        for wait_s in READ_BACK_WAITS_S:
            if after is not None and all(after.get(k) == v for k, v in written.items()):
                break
            await asyncio.sleep(wait_s)
            try:
                after = await self._read()
            except Exception:  # noqa: BLE001
                continue
        return after

    def _schedule_verify(self, written: dict) -> None:
        if self._verify_task is not None:
            self._verify_task.cancel()
        self._verify_task = asyncio.get_running_loop().create_task(self._verify(written))

    async def _verify(self, written: dict) -> None:
        """A second energy manager may write its own values back some time after ours."""
        async for _ in after_write():
            try:
                now = await self._read()
            except Exception:  # noqa: BLE001 - not connected right now; nothing to report
                continue
            # a value that could not be read (None, e.g. a single timeout) is no change by another device (#219)
            changed = {k: now.get(k) for k, v in written.items() if now.get(k) is not None and now.get(k) != v}
            if changed:
                self.external_change = {"expected": written, "found": changed}
                self.runtime.storage.log_control("battery_settings_check", {"from": written, "to": changed}, False,
                                                 "von einem anderen Gerät überschrieben")
                log.warning("battery settings were overwritten by another device: %s", changed)
                return


class ConfirmationRequired(Exception):
    """A change that needs an explicit confirmation: raising the feed-in limit (the grid operator's written
    confirmation) or a backup reserve below 10 % (#313)."""


RULE_SHARE = {"limit_60": 0.6, "limit_70": 0.7}
RULE_LABEL = {"limit_60": "60 %", "limit_70": "70 %"}


def same_limit(found: int | None, limit_w: int, step: int) -> bool:
    """The inverter holds limit_w: exactly, or rounded down to its step (SAJ: per mille of the rated power), never
    more feed-in than asked for."""
    return found is not None and 0 <= limit_w - found < step


def legal_max_w(rule: str, installed_kwp: float) -> int | None:
    """Highest feed-in power the declared rule allows (percentages refer to the installed module power)."""
    share = RULE_SHARE.get(rule)
    if share is None or not installed_kwp:
        return None
    return int(installed_kwp * 1000 * share)


class ExportLimitControl:
    """Feed-in (export) power limit. The limit is part of the grid connection approval: raising or lifting
    it is only allowed with the grid operator's written confirmation, which the user must declare and
    reference. Lowering it is always allowed. Every change is logged."""

    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime
        self.external_change: dict | None = None
        self._verify_task: asyncio.Task | None = None
        self._shared = SharedRead(self._read_limit)

    def _driver(self):
        collector = self.runtime.collector
        if collector.driver is None or not collector.connected:
            raise NotConnected("Wechselrichter ist nicht verbunden")
        return collector.driver

    def _rule(self) -> dict:
        config = self.runtime.config
        rule, kwp = config.grid.feed_in_rule, config.pv.installed_kwp
        return {"rule": rule, "installed_kwp": kwp, "legal_max_w": legal_max_w(rule, kwp)}

    async def _read_limit(self) -> dict:
        return asdict(await self._driver().read_export_limit())

    async def read(self, shared: bool = False) -> dict:
        """shared: for the app's requests, see SharedRead; never for a check before or after a write."""
        limit = await self._shared.get(self._driver()) if shared else await self._read_limit()
        return {**limit, **self._rule(), "external_change": self.external_change}

    async def write(self, limit_w: int, *, confirmed: bool = False, reference: str = "", source: str | None = None) -> dict:
        """source: who asked for it if not the web app, e.g. an admin of Home Assistant through its sidebar (#305)."""
        try:
            return await self._write(limit_w, confirmed, reference, source)
        finally:
            self._shared.invalidate()

    async def _write(self, limit_w: int, confirmed: bool, reference: str, source: str | None) -> dict:
        control = self.runtime.config.control
        if not control.enabled:
            raise ControlDisabled("Steuerung ist deaktiviert")
        # like the battery settings: not only rely on the driver reporting the limit as unsupported (#113)
        device = self.runtime.collector.device
        if device is not None and not device.supports_control:
            raise ValueError(f"Für {device.manufacturer}-Geräte ist die Steuerung in OpenAmpere noch nicht freigegeben "
                             "(nur Anzeige).")
        async with control_lock(self.runtime):
            current = await self.read()
            if not current["supported"]:
                raise ValueError("Die Einspeisebegrenzung lässt sich bei diesem Gerät nicht über Modbus ändern.")
            limit_w = int(limit_w)
            maximum = current["rated_power_w"] or 99_999
            if not 0 <= limit_w <= maximum:
                raise ValueError(f"Wert muss zwischen 0 und {maximum} W liegen")
            old = current["limit_w"]
            step = current.get("step_w") or 1
            if same_limit(old, limit_w, step):
                return {"dry_run": control.dry_run, "written": False, **current}

            raising = old is None or limit_w > old
            reference = reference.strip()
            rule = self._rule()
            legal_max = rule["legal_max_w"]
            if legal_max is not None and limit_w > legal_max:
                raise ValueError(
                    f"Nach der eingestellten Regel ({RULE_LABEL[rule['rule']]} von {rule['installed_kwp']:g} kWp) sind "
                    f"höchstens {legal_max} W erlaubt. Gilt die Regel nicht mehr (z. B. weil ein intelligentes "
                    "Messsystem mit Steuerbox eingebaut wurde), ändere zuerst die Regel.")
            # Within a declared legal percentage, or with "no limit" declared, raising needs no further consent.
            needs_consent = raising and legal_max is None and rule["rule"] != "none"
            if needs_consent and (not confirmed or len(reference) < 3):
                raise ConfirmationRequired(
                    "Zum Erhöhen oder Aufheben der Einspeisebegrenzung musst du bestätigen, dass die schriftliche "
                    "Zustimmung deines Netzbetreibers vorliegt, und Datum/Zeichen dieser Bestätigung angeben.")

            storage = self.runtime.storage
            details = {"from": {"export_limit_w": old}, "to": {"export_limit_w": limit_w},
                       "rule": rule["rule"], "installed_kwp": rule["installed_kwp"],
                       "grid_operator_confirmation": reference if needs_consent else None,
                       **({"source": source} if source else {})}
            if control.dry_run:
                storage.log_control("export_limit", details, True, "nicht ausgeführt (Testmodus)")
                return {"dry_run": True, "written": False, **current}

            driver = self._driver()
            try:
                await driver.write_export_limit(limit_w)
            except Exception as err:
                storage.log_control("export_limit", details, False, f"Fehler: {err}")
                if getattr(err, "transient", False):
                    raise WriteFailed("Keine Antwort vom Wechselrichter. Bitte später erneut versuchen.") from err
                raise WriteFailed("Der Wechselrichter hat die Änderung abgelehnt.") from err
            # written: always in the control log, also when the read-back fails (#219)
            try:
                after = await self.read()
            except Exception:  # noqa: BLE001 - logged below, checked again by _verify
                after = None
            if after is None:
                result = "Rücklesen fehlgeschlagen"
            else:
                result = ("ok" if same_limit(after["limit_w"], limit_w, step)
                          else f"Rücklesen abweichend: {after['limit_w']} W")
            storage.log_control("export_limit", details, False, result)
            self.external_change = None
            self._schedule_verify(limit_w, step)
            if after is None:
                raise WriteFailed("Keine Antwort vom Wechselrichter. Bitte prüfe die Werte und versuche es "
                                  "später erneut.")
            return {"dry_run": False, "written": True, "result": result, **after}

    def _schedule_verify(self, limit_w: int, step: int = 1) -> None:
        if self._verify_task is not None:
            self._verify_task.cancel()
        self._verify_task = asyncio.get_running_loop().create_task(self._verify(limit_w, step))

    async def _verify(self, limit_w: int, step: int = 1) -> None:
        async for _ in after_write():
            try:
                now = (await self.read())["limit_w"]
            except Exception:  # noqa: BLE001
                continue
            if now is not None and not same_limit(now, limit_w, step):
                self.external_change = {"expected": limit_w, "found": now}
                self.runtime.storage.log_control("export_limit_check", {"from": {"export_limit_w": limit_w},
                                                                        "to": {"export_limit_w": now}},
                                                 False, "von einem anderen Gerät überschrieben")
                return
