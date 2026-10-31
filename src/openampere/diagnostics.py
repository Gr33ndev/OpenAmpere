# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Read-only diagnostics for the real device ("Liste E" of the on-site checks).

Everything here only reads. The result is a report that users can share (e.g. in a GitHub issue) so
register maps, scaling factors and device behaviour can be verified for more devices. The serial number
is masked unless the user explicitly includes it; addresses, host names and times of the user's actions are
pseudonymised or left out (#149). The report ends with the last lines of the server log, pseudonymised the same way,
so owners do not have to read and black out the console output themselves (#169).
"""

from __future__ import annotations

import asyncio
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime

from . import logs, privacy
from .charging import REMOTE_COMMAND, REMOTE_TIMEOUT_S, STARTED
from .drivers.foxess.registers import REMOTE_OURS, remote_target
from .drivers.modbus import ModbusIllegalError, ModbusReadError, ModbusTransientError
from .ocpp import OcppListener, ocpp_markdown
from .probe import Probe, section_markdown
from .runtime import Runtime
from .wallbox import WallboxCheck, check_markdown
from .cars import report_markdown as car_markdown
from .wallbox.native import report_markdown as native_markdown

EXTRA_CONNECTIONS = 3
WATCH_S = 300  # how often the background job looks at the remote control
SENEC_STALE_S = 120  # values of the Senec's last read older than this are marked as such in the report
REMOTE_SEEN = "remote_seen"  # meta: since when the remote control was on at every visit
EXPORT_LIMIT = "Einspeisebegrenzung (46616)"


@dataclass
class Check:
    id: str
    title: str
    status: str = "info"  # ok | warn | error | info | skipped
    summary: str = ""
    details: dict = field(default_factory=dict)
    hint: str = ""  # what it means and what to do, shown below the result


def export_limit_check(words: list[int], rated: int | None) -> Check:
    """The export limit register 46616–46617 (I32, W). Above the rated power the inverter cannot export that much
    anyway: usually no limit is set (#413). Only a negative value points to a wrong unit or register."""
    value = (words[0] << 16) | words[1]
    if value >= 1 << 31:
        value -= 1 << 32
    details = {"high_word": words[0], "rated_power_w": rated}
    if value < 0:
        return Check("export_limit", EXPORT_LIMIT, "warn", "negativer Wert – unplausibel, Einheit/Register prüfen", details)
    # rounded: the exact limit tells the exact system size (60 or 70 % of the kWp, #149)
    rounded = round(value, -2)
    details["rounded_w"] = rounded
    if value <= (rated or 30_000):
        return Check("export_limit", EXPORT_LIMIT, "ok", f"etwa {rounded} W", details)
    return Check("export_limit", EXPORT_LIMIT, "info", f"etwa {rounded} W – über der Nennleistung, vermutlich keine "
                 "Begrenzung eingestellt", details,
                 "So viel kann der Wechselrichter gar nicht einspeisen. Meist heißt das: Am Wechselrichter ist keine "
                 "Einspeisebegrenzung eingestellt. Zeigt die App des Herstellers eine Begrenzung, teile bitte den "
                 "Bericht.")


class Diagnostics:
    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime
        self.running = False
        self._watched = 0.0
        self.probe = Probe(runtime)  # reading any device (#324); its last results go into the report
        self.wallbox = WallboxCheck(self.probe)
        self.ocpp = OcppListener(runtime)  # listening to a wallbox over OCPP (#328), likewise  # the read-only wallbox check of the setup (#325), likewise
        self.wallboxes = None  # wallbox.native.NativeWallboxes, set by the app: their state goes into the report
        self.cars = None  # cars.Cars, set by the app, likewise (#401)
        last = runtime.storage.get_meta("diagnostics_report")
        self.last: dict | None = self._pseudonymise(last) if last else None  # stored by an older version (#149)

    def _driver(self):
        driver = self.runtime.collector.driver
        inner = getattr(driver, "_driver", None)  # AutoDriver wraps the detected driver
        return inner or driver

    async def _try(self, address: int, count: int, function: int) -> dict:
        driver = self._driver()
        started = time.monotonic()
        try:
            words = await driver._read_once(address, count, function)
            return {"ok": True, "words": words, "ms": round((time.monotonic() - started) * 1000)}
        except ModbusIllegalError as err:
            return {"ok": False, "error": "abgelehnt", "detail": str(err)}
        except (ModbusTransientError, ModbusReadError, OSError, asyncio.TimeoutError) as err:
            return {"ok": False, "error": "keine Antwort", "detail": str(err)}

    async def _read_remote(self, register_map) -> dict:
        """On/off, watchdog timeout and power (32 bit, negative = the battery charges) of the remote control. On the
        new map also the controlled target encoded in 46001 and the watchdog countdown 46007, read on its own so a
        device that refuses it does not break the check (#509)."""
        r = await self._try(register_map.settings["remote_enable"].address, 4, 3)
        if not r["ok"]:
            return {"error": r["error"]}
        power = (r["words"][2] << 16) | r["words"][3]
        remote = {"on": bool(r["words"][0] & 1), "timeout_s": r["words"][1],
                  "power_w": power - 0x100000000 if power & 0x80000000 else power, "words": r["words"],
                  "target": None, "countdown_s": None}
        countdown = register_map.settings.get("remote_countdown")
        if countdown is not None:  # the bit field of 46001 is documented for the new map only (#509)
            remote["target"] = remote_target(r["words"][0])
            remote["consumer_sign"] = bool(r["words"][0] & 2)
            c = await self._try(countdown.address, 1, 3)
            remote["countdown_s"] = c["words"][0] if c["ok"] else None
            remote["countdown_error"] = None if c["ok"] else c["error"]
        return remote

    async def watch_remote(self, now: float | None = None) -> None:
        """Background job, read only: every few minutes, whether the remote control is on, so the check can say for
        how long (#135). A setpoint seen at every visit is renewed all the time, not left over once."""
        now = time.time() if now is None else now
        driver = self._driver()
        register_map = getattr(driver, "map", None)
        if (now - self._watched < WATCH_S or self.running or not self.runtime.collector.connected
                or register_map is None or "remote_enable" not in register_map.settings):
            return
        self._watched = now
        remote = await self._read_remote(register_map)
        if "error" in remote:
            return
        seen = self.runtime.storage.get_meta(REMOTE_SEEN) or {}
        on_since = (seen.get("on_since") or now) if remote["on"] else None
        self.runtime.storage.set_meta(REMOTE_SEEN, {"on_since": on_since, "checked": now, "power_w": remote["power_w"]})

    async def _remote_control(self, register_map) -> Check:
        """Who controls the battery from outside right now: nobody, OpenAmpere itself (charging from the grid) or
        another device, usually the previous smartbox following targets from its cloud."""
        title = f"Fernsteuerung ({register_map.settings['remote_enable'].address})"
        remote = await self._read_remote(register_map)
        if "error" in remote:
            return Check("remote", title, "info", f"nicht lesbar ({remote['error']})")
        now = time.time()
        timeout, power, target, countdown = (remote["timeout_s"], remote["power_w"], remote["target"],
                                             remote["countdown_s"])
        command = self.runtime.storage.get_meta(REMOTE_COMMAND) or {}
        driver = self._driver()
        # durations instead of times of the user's actions (#149)
        details = {"words": remote["words"], "timeout_s": timeout, "power_w": power,
                   "openampere_last_command_min_ago": round((now - command["ts"]) / 60) if command else None}
        if target is not None:
            details.update({"target": target, "consumer_sign": remote["consumer_sign"], "countdown_s": countdown,
                            "openampere_target": getattr(driver, "remote_target", None),
                            "openampere_target_fallback": getattr(driver, "remote_fallback", None)})
            if remote["countdown_error"]:
                details["countdown_error"] = remote["countdown_error"]
        if not remote["on"]:
            return Check("remote", title, "ok", "aus – kein Gerät steuert den Speicher gerade von außen", details)
        # negative = taken in (sign not verified on every device, the raw value is in the details); bit 1 of 46001
        # set means the consumer convention, where + is taken in (#509)
        if remote.get("consumer_sign"):
            power = -power
        if target in ("ac", "ac_grid_first"):  # the setpoint applies to the inverter's AC side (#509)
            what = (f"{abs(power)} W Bezug am Wechselrichter-Ausgang" if power < 0 else
                    f"{power} W Abgabe am Wechselrichter-Ausgang" if power > 0 else
                    "0 W am Wechselrichter-Ausgang, also kein Strom vom Wechselrichter ins Haus")
        elif target == "grid":
            what = (f"{abs(power)} W Bezug am Netzanschluss" if power < 0 else
                    f"{power} W Einspeisung am Netzanschluss" if power > 0 else "0 W am Netzanschluss")
        else:
            what = (f"{abs(power)} W Laden" if power < 0 else f"{power} W Entladen" if power > 0
                    else "0 W, also weder Laden noch Entladen")
        # OpenAmpere switches it on with 1 or 5 and its own timeout; the smartbox uses 5 with 30 s (#141, #509)
        ours = (bool(command) and remote["words"][0] in REMOTE_OURS
                and timeout == command.get("timeout_s", REMOTE_TIMEOUT_S))
        writing = ours and bool(getattr(driver, "_remote_owned", None))
        # a command OpenAmpere switched off afterwards cannot be the source of the countdown
        recent = ours and not command.get("released") and now - command["ts"] < timeout
        clock = self._countdown_text(countdown, writing, recent)
        if writing:
            hint = "Das ist das Laden aus dem Netz von OpenAmpere (Geräte → Speicher). Es endet zur geplanten Zeit."
            if getattr(driver, "remote_fallback", None) == "unanswered":  # not in the control log (#509)
                hint += (" Der Wechselrichter lädt gerade mit Ziel Wechselrichter-Ausgang statt Speicher, weil er "
                         "kurz nicht geantwortet hat; OpenAmpere versucht es mit dem nächsten Befehl wieder mit dem "
                         "Ziel Speicher.")
            elif getattr(driver, "remote_target", None) == "ac":
                hint += (" Der Wechselrichter lädt mit Ziel Wechselrichter-Ausgang statt Speicher (siehe "
                         "Steuerungsprotokoll): Scheint die Sonne, regelt er die Solaranlage ab, sobald der Speicher "
                         "nichts mehr aufnimmt.")
            return Check("remote", title, "ok", f"an – OpenAmpere lädt aus dem Netz: {what}", details,
                         f"{hint} {clock}".strip())
        if ours and not command.get("released"):
            ago = privacy.ago(now - command["ts"])  # a duration, not the time of the owner's action (#149, #223)
            return Check("remote", title, "info", f"an – Rest des Ladens aus dem Netz von OpenAmpere ({what})", details,
                         f"OpenAmpere hat die Fernsteuerung zuletzt {ago} benutzt und am Ende nicht "
                         "abgeschaltet, zum Beispiel wegen eines Neustarts oder einer kurz unterbrochenen Verbindung. "
                         "Es schaltet sie innerhalb einer Minute selbst ab, du musst nichts tun. Ist sie danach noch "
                         f"an, melde das bitte als Fehler. {clock}".strip())
        seen = self.runtime.storage.get_meta(REMOTE_SEEN) or {}
        if seen.get("on_since") and now - seen.get("checked", 0) <= 2 * WATCH_S:
            duration = seen["checked"] - seen["on_since"]
            details["on_for_min"] = round(duration / 60)
            since = f" Seit mindestens {privacy.duration(duration)} durchgehend an." if duration >= WATCH_S else ""
        else:
            since = ""
        started = self.runtime.storage.last_control("grid_charging", STARTED)
        last = f"{privacy.ago(now - started)} aus dem Netz geladen" if started else "noch nie aus dem Netz geladen"
        who = (f"OpenAmpere benutzt dieselben Werte, hat zuletzt {last} und die Fernsteuerung danach abgeschaltet. "
               "Vielleicht hat der Wechselrichter die Werte nach einem Neustart wiederhergestellt: Dann schaltet "
               "OpenAmpere sie nach dem nächsten Verbindungsaufbau selbst ab. Oder ein zweites Programm steuert den "
               "Wechselrichter, zum Beispiel ein zweites OpenAmpere."
               if ours else f"OpenAmpere war es nicht: Es schaltet die Fernsteuerung mit anderen Werten ein (1 oder 5 "
               f"und {REMOTE_TIMEOUT_S} s) und hat zuletzt {last}.")
        effect = "Solange die Vorgabe gilt, folgt der Wechselrichter ihr statt seinem normalen Betrieb. "
        if power == 0 and target in ("ac", "ac_grid_first"):
            effect += ("Bei 0 W am Wechselrichter-Ausgang bezieht das Haus seinen Strom ganz aus dem Netz, die Sonne "
                       "lädt nur den Speicher, und ist er voll, regelt der Wechselrichter die Solaranlage ab. Schau in "
                       "der Übersicht, ob die Solarleistung bei voller Batterie auf fast 0 W fällt, obwohl die Sonne "
                       "scheint. ")
        elif power == 0 and target in (None, "battery"):
            effect += ("Bei 0 W lädt und entlädt der Speicher meist nicht: Schau in der Übersicht, ob die "
                       "Speicherleistung dauerhaft bei 0 W bleibt, obwohl die Sonne scheint oder das Haus Strom "
                       "braucht. ")
        named = {"ac": "Ziel Wechselrichter-Ausgang", "ac_grid_first": "Ziel Wechselrichter-Ausgang",
                 "battery": "Ziel Speicher", "grid": "Ziel Netzanschluss"}.get(target)
        value = f"Wert {remote['words'][0]}" + (f" = {named}" if named else "")
        return Check("remote", title, "warn",
                     f"an – ein anderes Gerät gibt dem Speicher {what} vor ({value}, Zeitlimit "
                     f"{timeout} s).{since}", details,
                     (f"{who} {effect}{clock} " + ("" if ours else
                     "Meist ist das die bisherige Smartbox: Sie setzt Vorgaben aus der Cloud ihres "
                     "Herstellers um und kann dabei das Laden aus dem Netz und die Speicher-Einstellungen von "
                     "OpenAmpere überschreiben. Wenn du das nicht möchtest: der Smartbox im Router den Internetzugang "
                     "sperren oder sie abklemmen (vorher klären, ob sie für etwas anderes gebraucht wird), siehe README "
                     "„Die bisherige Smartbox setzt Einstellungen zurück“. Sonst kannst du den Hinweis ignorieren.")).strip())

    @staticmethod
    def _countdown_text(countdown: int | None, writing: bool, recent: bool) -> str:
        """What the watchdog countdown (46007) says about a remote control that is on (#141, #143, #509). recent: with
        OpenAmpere's values, its last command was sent within the time limit, so a running countdown is its own."""
        if countdown is None:
            return ""
        if countdown == 0 and (writing or recent):  # OpenAmpere's command is fresh: the counter does not count here
            return ("Der Zähler des Zeitlimits (46007) zeigt 0, obwohl der letzte Befehl von OpenAmpere gerade erst "
                    "gesendet wurde; dieser Wechselrichter zählt ihn offenbar nicht mit.")
        if countdown == 0:
            return ("Das Zeitlimit ist abgelaufen, die Fernsteuerung ist trotzdem noch an: Der Wechselrichter hat sie "
                    "nach Ablauf nicht abgeschaltet, und die Vorgabe gilt weiter, bis sie jemand abschaltet.")
        if writing:
            return f"Das Zeitlimit läuft (noch {countdown} s), OpenAmpere erneuert die Vorgabe laufend."
        if recent:
            return f"Das Zeitlimit läuft noch vom letzten Befehl von OpenAmpere (noch {countdown} s)."
        return (f"Das Zeitlimit läuft (noch {countdown} s), obwohl OpenAmpere gerade nichts sendet: Ein anderes Gerät "
                "schreibt die Vorgabe gerade, oder der Wechselrichter hat sie nach einem Neustart wiederhergestellt.")

    async def run(self, *, connection_test: bool = False, include_serial: bool = False) -> dict:
        if self.running:
            raise RuntimeError("Die Diagnose läuft bereits.")
        collector = self.runtime.collector
        self.running = True
        try:
            if collector.driver is not None and collector.connected:
                checks = await self._checks(connection_test)
            else:  # the report is still worth sharing: the log usually tells why the connection fails (#169)
                checks = [self._not_connected(), *self._history_checks()]
        finally:
            self.running = False
        device = collector.device
        info = asdict(device) if device else {}
        if not include_serial:
            info["serial"] = privacy.shorten(info.get("serial"))
        now = time.time()
        report = self._pseudonymise({"created": now, "version": self._version(), "full_serial": include_serial,
                                     "device": info,
                                     "connection": {"mode": self.runtime.config.inverter.connection_mode,
                                                    "timeout_s": self.runtime.config.inverter.timeout},
                                     "checks": [asdict(c) for c in checks], "log": logs.RECENT.lines(now),
                                     "probe": self.probe.section(include_text=include_serial),
                                     "wallbox": self.wallbox.section(include_text=include_serial),
                                     "ocpp": privacy.ocpp_for_report(self.ocpp.record, include_serial),
                                     "wallboxes": self.wallboxes.report() if self.wallboxes else [],
                                     "cars": self.cars.report() if self.cars else []},
                                    include_serial)
        self.runtime.storage.set_meta("diagnostics_report", report)
        self.last = report
        return report

    def _not_connected(self) -> Check:
        collector = self.runtime.collector
        if collector.driver is None:
            return Check("connection", "Verbindung zum Wechselrichter", "skipped", "Noch kein Wechselrichter eingerichtet.")
        return Check("connection", "Verbindung zum Wechselrichter", "error",
                     f"nicht verbunden: {collector.last_error or 'noch keine Antwort'}",
                     hint="Die Prüfungen am Gerät brauchen eine Verbindung. Die letzten Zeilen des Protokolls am Ende "
                          "des Berichts zeigen meist, woran es liegt.")

    def _pseudonymise(self, report: dict, include_serial: bool = False) -> dict:
        """The checks and the log can quote connection errors with the inverter's address, the log also the serial
        number and the addresses of other services; the report is shared publicly. One set of pseudonyms for all
        parts, so the same address has the same name everywhere in the report."""
        pseudonyms = privacy.for_report(self.runtime, include_serial)
        probe, wallbox = report.get("probe") or {}, report.get("wallbox") or {}
        pseudonyms.hosts((probe.get("identify") or {}).get("host"), (probe.get("read") or {}).get("host"),
                         wallbox.get("host"))  # names the owner typed, not only those with a home-network ending
        report = {**report, "log": privacy.log_for_report(report.get("log"))}
        return {**report, **{part: pseudonyms.apply(report.get(part)) for part in ("checks", "log", "probe", "wallbox", "ocpp",
                                                                          "wallboxes", "cars")}}

    @staticmethod
    def _version() -> str:
        from importlib.metadata import PackageNotFoundError, version
        try:
            return version("openampere")
        except PackageNotFoundError:
            return "dev"

    async def _checks(self, connection_test: bool) -> list[Check]:
        driver = self._driver()
        checks: list[Check] = []
        register_map = getattr(driver, "map", None)
        device = self.runtime.collector.device

        # 1. identity, register map and function code for every block
        c = Check("blocks", "Registerkarte und Funktionscode",
                  details={"model": device.model if device else None,
                           "map": register_map.name if register_map else None,
                           "read_function": getattr(driver, "read_function", None)})
        if register_map is not None:
            results = {}
            for start, count in register_map.blocks:
                results[f"{start}+{count}"] = {f"fc{fc}": {k: v for k, v in (await self._try(start, count, fc)).items()
                                                            if k != "words"} for fc in (3, 4)}
            c.details["blocks"] = results
            failing = [b for b, r in results.items() if not r[f"fc{register_map.read_function}"]["ok"]]
            c.status = "ok" if not failing else "warn"
            c.summary = ("Alle Blöcke mit dem erkannten Funktionscode lesbar." if not failing
                         else f"Nicht lesbar: {', '.join(failing)}")
        else:
            c.status, c.summary = "skipped", "Für diesen Gerätetyp noch nicht verfügbar."
        checks.append(c)

        if register_map is None or register_map.name != "foxess_h3_new":
            checks.append(Check("foxess", "FoxESS-Detailprüfungen", "skipped",
                                "Nur für FoxESS H3 mit neuer Registerkarte."))
        else:
            fc = register_map.read_function
            # 2. optional blocks without a second battery / with fewer MPPTs
            for start, label in ((38309, "Zweites Batteriemodul (38309)"), (39327, "MPPT-Details (39327)")):
                count = dict(register_map.blocks).get(start, 8)
                r = await self._try(start, count, fc)
                status = "ok" if r["ok"] else "info"
                summary = ("liefert nur Nullen (vermutlich nicht vorhanden)" if r["ok"] and not any(r["words"])
                           else "liefert Werte" if r["ok"] else f"{r['error']} – wird übersprungen")
                checks.append(Check(f"optional_{start}", label, status, summary, {"words": r.get("words")}))

            # 3. block read 37609-37632 compared with single reads
            block = await self._try(37609, 24, fc)
            singles = {a: await self._try(a, 1, fc) for a in (37609, 37612, 37624)}
            same = block["ok"] and all(s["ok"] and s["words"][0] == block["words"][a - 37609] for a, s in singles.items())
            checks.append(Check("block_37609", "Blocklesen (37609–37632)", "ok" if same else "warn",
                                "Block und Einzelwerte stimmen überein." if same else
                                "Block und Einzelwerte weichen ab – bitte Bericht teilen.",
                                {"block": block.get("words"), "single": {a: s.get("words") for a, s in singles.items()}}))

            # 4. scaling of the inverter temperature
            r = await self._try(39141, 1, fc)
            raw = r["words"][0] if r["ok"] else None
            if raw is None:
                checks.append(Check("temp_scale", "Wechselrichtertemperatur (39141)", "info", "nicht lesbar"))
            else:
                value = raw - 0x10000 if raw & 0x8000 else raw
                celsius = f"{value / 10:.1f}".replace(".", ",")
                # OpenAmpere reads this register in 0.1 °C; small raw values could also mean whole degrees
                clear = value > 90
                checks.append(Check("temp_scale", "Wechselrichtertemperatur (39141)", "ok" if clear else "warn",
                                    f"{celsius} °C (Rohwert {value}, Faktor 0,1)" if clear else
                                    f"Rohwert {value}: entweder {celsius} °C oder {value} °C. Bitte mit der Anzeige am "
                                    "Wechselrichter vergleichen und den Bericht teilen.", {"raw": value}))

            # 5. export limit register (read only)
            r = await self._try(46616, 2, 3)
            if r["ok"]:
                checks.append(export_limit_check(r["words"], device.rated_power_w if device else None))
            else:
                checks.append(Check("export_limit", "Einspeisebegrenzung (46616)", "warn", f"{r['error']}"))

            # 10. bms1_connected
            r = await self._try(37002, 1, fc)
            connected = {0: "nein", 1: "ja"}.get(r["words"][0], f"unbekannter Wert {r['words'][0]}") if r["ok"] else None
            checks.append(Check("bms1", "Batterie verbunden (37002)", "info", connected or r["error"], {"words": r.get("words")}))

            checks.append(await self._remote_control(register_map))

        if getattr(driver, "KEY", None) == "saj":
            checks.append(await self._saj_limits())
            checks.append(await self._saj_control())
            checks.append(await self._saj_meter_b())
        if getattr(driver, "KEY", None) == "solarmax":
            checks.append(await self._solarmax_registers())
        if getattr(driver, "KEY", None) == "senec":
            checks.append(await self._senec_values())

        # 6. connection limit (optional: may briefly disturb other devices); Modbus only
        if connection_test and getattr(driver, "PROTOCOL", "modbus") == "modbus":
            checks.append(await self._connection_test())
        else:
            checks.append(Check("connections", "Gleichzeitige Verbindungen", "skipped",
                                "Nicht ausgeführt (kann andere Geräte kurz stören)."))

        return checks + self._history_checks()

    async def _saj_control(self) -> Check:
        """What grid charging and the export limit of an SAJ use (#377): OpenAmpere's charge slot, the time-of-use
        mode outside the slots, the export limit and its mode, and the inverter's clock the slot follows."""
        from .drivers.saj.registers import (CHARGE_ENABLE_ADDRESS, CHARGE_SLOT, CHARGE_SLOT_ADDRESS,
                                            EXPORT_LIMIT_ADDRESS, READ_FUNCTION, TIME_ADDRESS, TOU_OUTSIDE_ADDRESS)
        enable = await self._try(CHARGE_ENABLE_ADDRESS, 1, READ_FUNCTION)
        slot = await self._try(CHARGE_SLOT_ADDRESS, 3, READ_FUNCTION)
        outside = await self._try(TOU_OUTSIDE_ADDRESS, 1, READ_FUNCTION)
        export = await self._try(EXPORT_LIMIT_ADDRESS, 3, READ_FUNCTION)
        clock = await self._try(TIME_ADDRESS, 4, READ_FUNCTION)
        parts = []
        # the slot without its times and days: they tell when the owner charged (only its length and power)
        length = power = on = None
        if enable["ok"] and slot["ok"]:
            start, end, days = slot["words"]
            on = bool(enable["words"][0] & (1 << (CHARGE_SLOT - 1)))
            length = (end >> 8) * 60 + (end & 0xFF) - (start >> 8) * 60 - (start & 0xFF)
            power = days & 0xFF
            parts.append(f"Ladezeit {CHARGE_SLOT} {'an' if on else 'aus'}, {length} min, {power} % "
                         f"(0x3604, 0x{CHARGE_SLOT_ADDRESS:04X})")
        else:
            parts.append(f"Ladezeiten {enable.get('error') or slot.get('error')}")
        parts.append(f"außerhalb der Ladezeiten {outside['words'][0]} (0x365F)" if outside["ok"]
                     else f"0x365F {outside['error']}")
        if export["ok"]:
            limit, _, mode = export["words"]
            parts.append(f"Einspeisebegrenzung {limit / 10:g} %, Modus {mode} (0x365A, 0x365C)")
        else:
            parts.append(f"0x365A–0x365C {export['error']}")
        parts.append("Uhr des Wechselrichters lesbar" if clock["ok"] else f"Uhr (0x4000) {clock['error']}")
        # the clock only as readable or not: a time of day is not needed and would be a personal detail
        return Check("saj_control", "Steuerung (SAJ)", "info", "; ".join(parts),
                     {"slot_on": on, "slot_minutes": length, "slot_power_percent": power,
                      "0x365F": outside.get("words"), "0x365A": export.get("words"), "clock": clock["ok"]})

    async def _saj_limits(self) -> Check:
        """All SAJ battery limits side by side: the settings (on grid, off grid, charge limit, work mode, reserve in
        backup mode) and the read-back registers, to confirm which register holds which limit (#334)."""
        from .drivers.saj.registers import BACKUP_SOC_ADDRESS, DEPTH_ADDRESS, READ_FUNCTION
        depths = await self._try(DEPTH_ADDRESS, 4, READ_FUNCTION)
        backup = await self._try(BACKUP_SOC_ADDRESS, 1, READ_FUNCTION)
        read_back = await self._try(0x4029, 4, READ_FUNCTION)
        parts = []
        if depths["ok"]:
            on_grid, off_grid, charge, mode = depths["words"]
            parts.append(f"Netzbetrieb {on_grid} %, Notstrombetrieb {off_grid} %, Ladegrenze {charge} %, "
                         f"Betriebsart {mode} (0x3644–0x3647)")
        else:
            parts.append(f"0x3644–0x3647 {depths['error']}")
        parts.append(f"Reserve im Backup-Modus {backup['words'][0]} % (0x3651)" if backup["ok"]
                     else f"0x3651 {backup['error']}")
        if read_back["ok"]:
            charge, low, dod, reserve = read_back["words"]
            parts.append(f"Rücklese-Register 0x4029 {charge} %, 0x402A {low} %, 0x402B {dod} %, 0x402C {reserve} %")
        else:
            parts.append(f"0x4029–0x402C {read_back['error']}")
        return Check("saj_limits", "Speicher-Grenzen (SAJ)", "info", "; ".join(parts),
                     {"0x3644": depths.get("words"), "0x3651": backup.get("words"), "0x4029": read_back.get("words")},
                     hint="Vergleiche die Werte mit der Hersteller-App: Notstrom-Reserve im Netzbetrieb und "
                          "Entladegrenze im Notstrombetrieb. Weichen sie ab, schreib uns die Werte aus der App dazu.")

    async def _saj_meter_b(self) -> Check:
        """Meter B (0x40A3): a second PV inverter the SAJ measures, counted only with the setting (#419). 0x4030 is the
        meter setting ("Meter Mode Set" at stanus74); its values are not known yet, the reports should tell."""
        from .drivers.saj.registers import READ_FUNCTION
        title = "Zweiter Wechselrichter an Meter B (0x40A3)"
        power = await self._try(0x40A3, 1, READ_FUNCTION)
        mode = await self._try(0x4030, 1, READ_FUNCTION)
        counted = self.runtime.config.pv.external
        details = {"0x4030": mode.get("words"), "counted": counted}
        if not power["ok"]:
            return Check("saj_meter_b", title, "info", f"0x40A3 {power['error']}", details)
        raw = power["words"][0]
        value = raw - 0x10000 if raw & 0x8000 else raw
        details["rounded_w"] = rounded = round(value, -2)  # rounded like every power in the report
        summary = (f"etwa {rounded} W, " + ("mitgezählt" if counted else "nicht mitgezählt")
                   + (f"; Zähler-Einstellung 0x4030 = {mode['words'][0]}" if mode["ok"] else ""))
        if counted or value <= 50:
            return Check("saj_meter_b", title, "info", summary, details)
        return Check("saj_meter_b", title, "warn", summary, details,
                     hint="Meter B misst Leistung, OpenAmpere zählt sie aber nicht mit. Ist das ein zweiter "
                          "PV-Wechselrichter, schalte ihn unter Mehr → Meine Anlage → Modulfelder benennen ein. Sonst "
                          "fehlt seine Leistung bei Erzeugung und Verbrauch.")

    async def _solarmax_registers(self) -> Check:
        """What the Solarmax driver reads (#519), built from the manufacturer's list without a device: the powers with
        their sign, rounded like every power in the report, to confirm the signs against the display, and whether the
        registers of "Connectivity+" (130–159) answer and are filled. No serial number (100–101) and no counter values:
        lifetime energy would identify the plant."""
        from .drivers.regs import decode
        from .drivers.solarmax.registers import COUNTER_BLOCK, READ_FUNCTION, VALUES
        title = "Register (Solarmax)"
        standard = await self._try(102, 25, READ_FUNCTION)
        if not standard["ok"]:
            return Check("solarmax_registers", title, "warn", f"102–126 {standard['error']}")
        words = dict(enumerate(standard["words"], start=102))

        def rounded(name: str) -> float:
            reg = VALUES[name]
            return round(decode(reg, [words[reg.address + i] for i in range(reg.count)]), -2)

        powers = {name: rounded(name) for name in ("pv_power", "pv_plant_power", "battery_power", "house_power",
                                                   "grid_power", "pv_ac_power")}
        details = {"firmware": words[102], "device_state": words[103], "rated_power_w": words[105],
                   "battery_soc": words[122], "powers_rounded_w": powers}
        counters = await self._try(*COUNTER_BLOCK, READ_FUNCTION)
        plus = await self._try(130, 14, READ_FUNCTION)
        details["counters"] = ("filled" if any(counters["words"]) else "zero") if counters["ok"] else counters["error"]
        details["130-143"] = ("filled" if any(plus["words"]) else "zero") if plus["ok"] else plus["error"]
        summary = (f"Zustand {words[103]}, Batterie {powers['battery_power']:+g} W (laden +), "
                   f"Netz {powers['grid_power']:+g} W (einspeisen +), Haus {powers['house_power']:g} W; "
                   f"Zähler (Connectivity+): {details['counters']}")
        return Check("solarmax_registers", title, "info", summary, details,
                     hint="OpenAmpere liest diesen Speicher nach der Liste von Solarmax, aber noch ohne Bestätigung von "
                          "einem echten Gerät. Vergleiche Batterie- und Netzleistung mit dem Display und schreib uns, "
                          "ob die Vorzeichen stimmen.")

    async def _senec_values(self) -> Check:
        """What the Senec driver reads from lala.cgi (#521), built without a device: the values of ENERGY with Senec's
        own signs (powers rounded like every power in the report) and how the device answered (scheme and port). Not
        the device id. The values of the collector's last read: no extra request outside its 30 s rhythm."""
        driver = self._driver()
        title = "Werte (Senec)"
        energy = getattr(driver, "last_energy", None)
        if not energy:
            return Check("senec_values", title, "skipped", "Noch keine Werte vom Senec-Speicher gelesen.")

        def shown(key: str, value):
            if not isinstance(value, float):
                return value
            return round(value, -2) if key.endswith(("_POW", "_POWER")) else round(value, 1)

        values = {key: shown(key, value) for key, value in energy.items()}
        url = getattr(driver, "_url", None) or ""
        scheme, _, rest = url.partition("://")
        read_at = getattr(driver, "last_energy_at", None)
        age = None if read_at is None else max(0, round(time.time() - read_at))
        details = {"values": values, "scheme": scheme or None, "port": rest.split("/")[0].rpartition(":")[2] or None,
                   "age_s": age}
        summary = (f"Batterie {values.get('GUI_BAT_DATA_POWER')} W (laden +), Netz {values.get('GUI_GRID_POW')} W "
                   f"(Bezug +), Ladestand {values.get('GUI_BAT_DATA_FUEL_CHARGE')} %")
        old = age is not None and age > SENEC_STALE_S
        if old:  # the device stopped answering: these are not the values of now
            summary = f"Letzte Werte von vor {age // 60} min: {summary}"
        return Check("senec_values", title, "warn" if old else "info", summary, details,
                     hint="OpenAmpere liest diesen Speicher nach offenen Projekten, aber noch ohne Bestätigung von einem "
                          "echten Gerät. Vergleiche Batterie- und Netzleistung mit der Senec-App und schreib uns, ob die "
                          "Vorzeichen stimmen.")

    def _history_checks(self) -> list[Check]:
        """What OpenAmpere observed while polling; needs no connection right now."""
        checks: list[Check] = []
        # 8. daily counter reset times (observed passively)
        tz = self.runtime.tz
        resets = self.runtime.storage.get_meta("daily_resets") or []
        times = [datetime.fromtimestamp(ts, tz).strftime("%H:%M") for ts in resets]
        late = [t for t in times if t not in ("00:00", "00:01", "00:02")]
        checks.append(Check("daily_reset", "Tageszähler-Rücksetzung", "info" if not times else "ok" if not late else "warn",
                            "Noch keine beobachtet (läuft nachts mit)." if not times else
                            f"Beobachtet um {', '.join(times[-5:])} Uhr" + (" – Uhr des Wechselrichters weicht ab?" if late else ""),
                            {"times": times}))

        # 9. night behaviour: disconnects per hour and counter glitches
        events = self.runtime.storage.get_meta("connection_events") or []
        by_hour = Counter(datetime.fromtimestamp(e["ts"], tz).hour for e in events if e["event"] == "getrennt")
        glitches = self.runtime.storage.get_meta("counter_glitches") or []
        checks.append(Check("night", "Verbindungsabbrüche und Zähler", "ok" if not events and not glitches else "info",
                            f"{plural(sum(by_hour.values()), 'Abbruch', 'Abbrüche')}, "
                            f"{plural(len(glitches), 'Zählerauffälligkeit', 'Zählerauffälligkeiten')} gespeichert",
                            {"disconnects_by_hour": dict(sorted(by_hour.items())),
                             "last_errors": [e["detail"] for e in events if e["event"] == "getrennt"][-5:],
                             "glitches": [{"kind": g["kind"], "hour": datetime.fromtimestamp(g["ts"], tz).hour}
                                          for g in glitches[-10:]]}))
        return checks

    async def _connection_test(self) -> Check:
        """Opens a few extra connections at the same time and counts how many get an answer."""
        from pymodbus.client import AsyncModbusTcpClient
        inverter = self.runtime.config.inverter
        driver = self._driver()
        unit = getattr(driver, "_unit", inverter.unit)
        clients = [AsyncModbusTcpClient(inverter.host, port=inverter.port, timeout=inverter.timeout, retries=0,
                                        reconnect_delay=0) for _ in range(EXTRA_CONNECTIONS)]
        answered = 0
        try:
            for client in clients:
                if not await client.connect():
                    continue
                try:
                    response = await client.read_holding_registers(30000, count=1, device_id=unit)
                    answered += 0 if response.isError() else 1
                except Exception:  # noqa: BLE001
                    pass
        finally:
            for client in clients:
                client.close()
        main_ok = True
        try:
            await driver._read_once(30000, 1, 3)
        except Exception:  # noqa: BLE001
            main_ok = False
        # no extra connection: OpenAmpere works, but nothing else (evcc, Home Assistant ...) can read at the same time
        status = "warn" if not main_ok else "ok" if answered else "info"
        return Check("connections", "Gleichzeitige Verbindungen", status,
                     f"{answered} von {EXTRA_CONNECTIONS} zusätzlichen Verbindungen beantwortet"
                     + ("; die Hauptverbindung wurde dabei getrennt" if not main_ok else
                        "" if answered else ". Andere Programme können dann nicht gleichzeitig mitlesen."),
                     {"extra_answered": answered, "main_connection_survived": main_ok})


def plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


# the report is meant for public issues; with the full serial number it identifies the installation (#428)
FULL_SERIAL_WARNING = ("> **Achtung:** Dieser Bericht enthält die vollständige Seriennummer. Bitte nicht öffentlich "
                       "posten, nur in einer privaten Nachricht an die Entwickler weitergeben.")


def report_markdown(report: dict) -> str:
    """Plain text for copying into an issue."""
    device = report.get("device") or {}
    # stored reports from before #428 have no "full_serial": they count as masked
    lines = [FULL_SERIAL_WARNING, ""] if report.get("full_serial") else []
    # no date and time: the report goes into public issues, and the time of an owner's action is personal (#149)
    lines += ["## OpenAmpere-Diagnose", "",
             f"- Version: {report.get('version')}",
             f"- Gerät: {device.get('manufacturer')} {device.get('model')} (Firmware {device.get('firmware')}, "
             f"Seriennr. {device.get('serial')})" if device.get("model") else "- Gerät: nicht erkannt",
             f"- Registerkarte: {device.get('register_map')}, Geräteadresse {device.get('unit')}",
             f"- Verbindung: {report['connection']['mode']}, Zeitlimit {report['connection']['timeout_s']} s", ""]
    attention = [c for c in report["checks"] if c["status"] == "warn"]
    if attention:
        lines += ["### Zu prüfen", ""] + [f"- **{c['title']}**: {c['summary']}" for c in attention] + ["", "### Alle Prüfungen", ""]
    for c in report["checks"]:
        lines.append(f"- **{c['title']}** [{c['status']}]: {c['summary']}")
    lines += ["", "<details><summary>Details</summary>", "", "```json"]
    import json
    # a text in the checks (e.g. a device name) must not close the block early
    lines.append(json.dumps(report["checks"], ensure_ascii=False, indent=1).replace("```", "` ` `"))
    lines += ["```", "", "</details>"]
    if report.get("probe"):  # reading any device (#324)
        lines += ["", "### Geräte-Abfrage", "", *section_markdown(report["probe"])]
    if report.get("wallbox"):  # the wallbox check of the setup (#325)
        lines += ["", "### Wallbox-Prüfung", "", *check_markdown(report["wallbox"])]
    for i, wallbox in enumerate(report.get("wallboxes") or [], 1):  # wallboxes OpenAmpere controls itself
        lines += ["", f"### Wallbox {i} (direkt)", "", *native_markdown(wallbox)]
    for i, car in enumerate(report.get("cars") or [], 1):  # cars and their manufacturer's data (#401)
        lines += ["", f"### Auto {i}", "", *car_markdown(car)]
    if report.get("ocpp"):  # listening to a wallbox over OCPP (#328)
        lines += ["", "### Wallbox über OCPP", "", *ocpp_markdown(report["ocpp"])]
    log = report.get("log") or []
    if log:  # times relative to the report (-HH:MM:SS)
        lines += ["", f"<details><summary>Protokoll (letzte {len(log)} Zeilen)</summary>", "", "```text",
                  *[line.replace("```", "` ` `") for line in log], "```", "", "</details>"]
    return "\n".join(lines)
