"""Writing battery settings to the inverter: guarded by the control switches, validated and logged."""

from __future__ import annotations

import asyncio
import itertools
import logging
from dataclasses import asdict

from .drivers.base import WorkMode
from .runtime import Runtime


class ControlDisabled(Exception):
    pass


class NotConnected(Exception):
    pass


class WriteFailed(Exception):
    """The inverter (or a Modbus proxy in front of it) did not accept the write."""


FIELDS = ("work_mode", "min_soc", "max_soc", "min_soc_on_grid")
SOC_FIELDS = ("min_soc", "max_soc", "min_soc_on_grid")
SOC_MIN = {"min_soc": 0, "max_soc": 10, "min_soc_on_grid": 10}  # lowest value per limit in %
VERIFY_AFTER_S = 45.0  # re-read written values after this time to detect a second master overwriting them

log = logging.getLogger(__name__)


def control_lock(runtime: Runtime) -> asyncio.Lock:
    """One lock per runtime: reading, checking and writing must not interleave between two users."""
    lock = getattr(runtime, "_control_lock", None)
    if lock is None:
        lock = runtime._control_lock = asyncio.Lock()
    return lock


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


def check_limits(values: dict) -> None:
    """FoxESS rules: 0 <= min_soc <= min_soc_on_grid < max_soc <= 100, reserve and upper limit at least 10.
    During an outage the battery may be emptied completely (#69)."""
    names = {"min_soc": "Die Untergrenze im Notstrombetrieb", "max_soc": "Die Ladegrenze",
             "min_soc_on_grid": "Die Notstrom-Reserve"}
    for key in SOC_FIELDS:
        value = values.get(key)
        low = SOC_MIN[key]
        if value is not None and not low <= value <= 100:
            raise ValueError(f"{names[key]} muss zwischen {low} und 100 % liegen")
    lo, reserve, hi = values.get("min_soc"), values.get("min_soc_on_grid"), values.get("max_soc")
    if lo is not None and reserve is not None and reserve < lo:
        raise ValueError("Die Notstrom-Reserve darf nicht unter der Untergrenze im Notstrombetrieb liegen")
    if reserve is not None and hi is not None and hi <= reserve:
        raise ValueError("Die Ladegrenze muss über der Notstrom-Reserve liegen")


class BatteryControl:
    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime
        self.external_change: dict | None = None  # values another device wrote back after our change
        self._verify_task: asyncio.Task | None = None

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

    async def read(self) -> dict:
        data = await self._read()
        data["unreadable"] = [k for k in FIELDS if data.get(k) is None]
        data["external_change"] = self.external_change
        return data

    async def write(self, changes: dict) -> dict:
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
        for key in SOC_FIELDS:
            if key in changes:
                changes[key] = int(changes[key])

        async with control_lock(self.runtime):
            driver = self._driver()
            current = await self._read()
            merged = {**current, **changes}
            if any(merged.get(k) is None for k in SOC_FIELDS) and any(k in changes for k in SOC_FIELDS):
                # without all three values the cross checks below cannot be made
                raise ValueError("Die aktuellen Speicher-Grenzen konnten nicht vollständig gelesen werden. "
                                 "Bitte später erneut versuchen.")
            check_limits(merged)
            diff = {k: v for k, v in changes.items() if current.get(k) != v}
            if not diff:
                return {"dry_run": control.dry_run, "written": {}, "settings": current}

            storage = self.runtime.storage
            details = {"from": {k: current.get(k) for k in diff}, "to": diff}
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
                    await driver.write_work_mode(WorkMode(diff["work_mode"]))
                for key in write_order(current, diff):
                    await driver.write_soc_limits(**{key: diff[key]})
            except Exception as err:  # noqa: BLE001 - logged and translated below
                error = err
            finally:
                # always record what the inverter actually holds now, also after a partial write
                try:
                    after = await self._read()
                except Exception:  # noqa: BLE001
                    after = None
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

    def _schedule_verify(self, written: dict) -> None:
        if self._verify_task is not None:
            self._verify_task.cancel()
        self._verify_task = asyncio.get_running_loop().create_task(self._verify(written))

    async def _verify(self, written: dict) -> None:
        """A second energy manager may write its own values back shortly after ours."""
        await asyncio.sleep(VERIFY_AFTER_S)
        try:
            now = await self._read()
        except Exception:  # noqa: BLE001 - not connected right now; nothing to report
            return
        changed = {k: now.get(k) for k, v in written.items() if now.get(k) != v}
        if changed:
            self.external_change = {"expected": written, "found": changed}
            self.runtime.storage.log_control("battery_settings_check", {"from": written, "to": changed}, False,
                                             "von einem anderen Gerät überschrieben")
            log.warning("battery settings were overwritten by another device: %s", changed)


class ConfirmationRequired(Exception):
    """Raising the feed-in limit needs the grid operator's written confirmation."""


RULE_SHARE = {"limit_60": 0.6, "limit_70": 0.7}
RULE_LABEL = {"limit_60": "60 %", "limit_70": "70 %"}


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

    def _driver(self):
        collector = self.runtime.collector
        if collector.driver is None or not collector.connected:
            raise NotConnected("Wechselrichter ist nicht verbunden")
        return collector.driver

    def _rule(self) -> dict:
        config = self.runtime.config
        rule, kwp = config.grid.feed_in_rule, config.pv.installed_kwp
        return {"rule": rule, "installed_kwp": kwp, "legal_max_w": legal_max_w(rule, kwp)}

    async def read(self) -> dict:
        limit = await self._driver().read_export_limit()
        return {**asdict(limit), **self._rule(), "external_change": self.external_change}

    async def write(self, limit_w: int, *, confirmed: bool = False, reference: str = "") -> dict:
        control = self.runtime.config.control
        if not control.enabled:
            raise ControlDisabled("Steuerung ist deaktiviert")
        async with control_lock(self.runtime):
            current = await self.read()
            if not current["supported"]:
                raise ValueError("Die Einspeisebegrenzung lässt sich bei diesem Gerät nicht über Modbus ändern.")
            limit_w = int(limit_w)
            maximum = current["rated_power_w"] or 99_999
            if not 0 <= limit_w <= maximum:
                raise ValueError(f"Wert muss zwischen 0 und {maximum} W liegen")
            old = current["limit_w"]
            if limit_w == old:
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
                       "grid_operator_confirmation": reference if needs_consent else None}
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
            after = await self.read()
            result = "ok" if after["limit_w"] == limit_w else f"Rücklesen abweichend: {after['limit_w']} W"
            storage.log_control("export_limit", details, False, result)
            self.external_change = None
            self._schedule_verify(limit_w)
            return {"dry_run": False, "written": True, "result": result, **after}

    def _schedule_verify(self, limit_w: int) -> None:
        if self._verify_task is not None:
            self._verify_task.cancel()
        self._verify_task = asyncio.get_running_loop().create_task(self._verify(limit_w))

    async def _verify(self, limit_w: int) -> None:
        await asyncio.sleep(VERIFY_AFTER_S)
        try:
            now = (await self.read())["limit_w"]
        except Exception:  # noqa: BLE001
            return
        if now != limit_w:
            self.external_change = {"expected": limit_w, "found": now}
            self.runtime.storage.log_control("export_limit_check", {"from": {"export_limit_w": limit_w},
                                                                    "to": {"export_limit_w": now}},
                                             False, "von einem anderen Gerät überschrieben")
