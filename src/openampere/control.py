"""Writing battery settings to the inverter: guarded by the control switches, validated and logged."""

from __future__ import annotations

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


def check_limits(values: dict) -> None:
    """FoxESS rules: 10 <= min_soc <= min_soc_on_grid < max_soc <= 100."""
    for key in ("min_soc", "max_soc", "min_soc_on_grid"):
        value = values.get(key)
        if value is not None and not 10 <= value <= 100:
            raise ValueError(f"{key} muss zwischen 10 und 100 % liegen")
    lo, reserve, hi = values.get("min_soc"), values.get("min_soc_on_grid"), values.get("max_soc")
    if lo is not None and reserve is not None and reserve < lo:
        raise ValueError("Die Notstrom-Reserve darf nicht unter der Entladegrenze im Inselbetrieb liegen")
    if reserve is not None and hi is not None and hi <= reserve:
        raise ValueError("Die Ladegrenze muss über der Notstrom-Reserve liegen")


class BatteryControl:
    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime

    def _driver(self):
        collector = self.runtime.collector
        if collector.driver is None or not collector.connected:
            raise NotConnected("Wechselrichter ist nicht verbunden")
        return collector.driver

    async def read(self) -> dict:
        settings = await self._driver().read_settings()
        data = asdict(settings)
        data["work_mode"] = settings.work_mode.value if settings.work_mode else None
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
            raise ValueError(f"unbekannte Einstellung: {', '.join(sorted(unknown))}")

        changes = dict(changes)
        if "work_mode" in changes:
            changes["work_mode"] = WorkMode(changes["work_mode"]).value
        for key in ("min_soc", "max_soc", "min_soc_on_grid"):
            if key in changes:
                changes[key] = int(changes[key])

        driver = self._driver()
        current = await self.read()
        check_limits({**current, **changes})
        diff = {k: v for k, v in changes.items() if current.get(k) != v}
        if not diff:
            return {"dry_run": control.dry_run, "written": {}, "settings": current}

        storage = self.runtime.storage
        if control.dry_run:
            storage.log_control("battery_settings", {"from": current, "to": diff}, True, "nicht ausgeführt (Probemodus)")
            return {"dry_run": True, "written": diff, "settings": current}

        try:
            if "work_mode" in diff:
                await driver.write_work_mode(WorkMode(diff["work_mode"]))
            soc = {k: diff[k] for k in ("min_soc", "max_soc", "min_soc_on_grid") if k in diff}
            if soc:
                await driver.write_soc_limits(**soc)
        except Exception as err:
            storage.log_control("battery_settings", {"from": current, "to": diff}, False, f"Fehler: {err}")
            if getattr(err, "transient", False):
                raise WriteFailed("Keine Antwort vom Wechselrichter. Bitte später erneut versuchen.") from err
            raise WriteFailed("Der Wechselrichter hat die Änderung abgelehnt. Hängt er hinter einem "
                              "schreibgeschützten Modbus-Proxy?") from err
        after = await self.read()
        mismatch = {k: after.get(k) for k, v in diff.items() if after.get(k) != v}
        result = "ok" if not mismatch else f"Rücklesen abweichend: {mismatch}"
        storage.log_control("battery_settings", {"from": current, "to": diff}, False, result)
        return {"dry_run": False, "written": diff, "settings": after, "result": result}


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
        return {**asdict(limit), **self._rule()}

    async def write(self, limit_w: int, *, confirmed: bool = False, reference: str = "") -> dict:
        control = self.runtime.config.control
        if not control.enabled:
            raise ControlDisabled("Steuerung ist deaktiviert")
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
            storage.log_control("export_limit", details, True, "nicht ausgeführt (Probemodus)")
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
        return {"dry_run": False, "written": True, "result": result, **after}
