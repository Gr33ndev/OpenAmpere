"""Read-only diagnostics for the real device ("Liste E" of the on-site checks).

Everything here only reads. The result is a report that users can share (e.g. in a GitHub issue) so
register maps, scaling factors and device behaviour can be verified for more devices. The serial number
is masked unless the user explicitly includes it; addresses, host names and times of the user's actions are
pseudonymised or left out (#149). The report ends with the last lines of the server log, pseudonymised the same way,
so owners do not have to read and black out the console output themselves (#169).
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime

from . import logs
from .charging import REMOTE_COMMAND, REMOTE_TIMEOUT_S, STARTED
from .drivers.modbus import ModbusIllegalError, ModbusReadError, ModbusTransientError
from .runtime import Runtime

EXTRA_CONNECTIONS = 3
WATCH_S = 300  # how often the background job looks at the remote control
REMOTE_SEEN = "remote_seen"  # meta: since when the remote control was on at every visit


@dataclass
class Check:
    id: str
    title: str
    status: str = "info"  # ok | warn | error | info | skipped
    summary: str = ""
    details: dict = field(default_factory=dict)
    hint: str = ""  # what it means and what to do, shown below the result


def _mask(serial: str | None) -> str | None:
    if not serial:
        return serial
    return serial[:4] + "…" + serial[-2:] if len(serial) > 6 else "…"


_IPV4 = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
_IPV6 = re.compile(r"(?<![\w:])[0-9A-Fa-f]{0,4}(?::[0-9A-Fa-f]{0,4}){2,7}(?![\w:])")
_MAC = re.compile(r"(?<![\w:-])[0-9A-Fa-f]{2}(?:[:-][0-9A-Fa-f]{2}){5}(?![\w:-])")
# time stamps in the names of database copies (storage.py): when the owner restored or lost data
_STAMP = re.compile(r"(?<!\d)\d{8}-\d{6}(?!\d)")
_HOST = re.compile(r"(?<![\w.-])[\w-]+(?:\.[\w-]+)*\.(?:local|lan|home|internal|fritz\.box|home\.arpa|ts\.net)"
                   r"(?![\w-])", re.I)
_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_HOME = re.compile(r"(?<=/)(home|Users)/[^/\s'\"]+")  # user name in file paths of a traceback


class _Pseudonyms:
    """Replaces addresses and local host names in report texts, the same value always by the same pseudonym (#149):
    the report stays readable ("the same address twice") without telling anyone the user's network."""

    def __init__(self, inverter_host: str | None, known: dict[str, str] | None = None) -> None:
        """`known`: further values with their replacement, e.g. the serial number or the ntfy address."""
        self.known = {k.strip().lower(): v for k, v in (known or {}).items() if k and k.strip()}
        if inverter_host:
            self.known[inverter_host.lower()] = "<Wechselrichter>"
        self.counts: Counter = Counter()

    def _name(self, value: str, kind: str) -> str:
        key = value.lower()
        if key not in self.known:
            self.counts[kind] += 1
            self.known[key] = f"<{kind}-{self.counts[kind]}>"
        return self.known[key]

    def _ip(self, match: re.Match, kind: str = "IP") -> str:
        try:
            ipaddress.ip_address(match.group(0))
        except ValueError:  # e.g. a time like 12:34:56 or a version number
            return match.group(0)
        return self._name(match.group(0), kind)

    def text(self, text: str) -> str:
        for value, name in sorted(self.known.items(), key=lambda item: -len(item[0])):
            text = re.sub(rf"(?<![\w.-]){re.escape(value)}(?![\w-]|\.\w)", lambda _m, n=name: n, text, flags=re.I)
        text = _EMAIL.sub(lambda m: self._name(m.group(0), "E-Mail"), text)
        text = _HOME.sub(r"\1/<Benutzer>", text)
        text = _MAC.sub(lambda m: self._name(m.group(0), "MAC"), text)
        text = _IPV4.sub(self._ip, text)
        text = _IPV6.sub(self._ip, text)
        text = _STAMP.sub("<Zeitpunkt>", text)  # e.g. openampere.db.before-restore-20261010-153012 (#223)
        return _HOST.sub(lambda m: self._name(m.group(0), "Host"), text)

    def apply(self, value):
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self.apply(v) for v in value]
        if isinstance(value, dict):
            return {k: self.apply(v) for k, v in value.items()}
        return value


def _duration(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 120:
        return f"{minutes} Minuten"
    hours = minutes // 60
    return f"{hours} Stunden" if hours < 48 else f"{hours // 24} Tagen"


def _ago(seconds: float) -> str:
    """How long ago, instead of the time of the owner's action (#149)."""
    return "vor weniger als einer Minute" if seconds < 60 else f"vor {_duration(seconds)}"


class Diagnostics:
    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime
        self.running = False
        self._watched = 0.0
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
        """On/off, watchdog timeout and power (32 bit, negative = the battery charges) of the remote control."""
        r = await self._try(register_map.settings["remote_enable"].address, 4, 3)
        if not r["ok"]:
            return {"error": r["error"]}
        power = (r["words"][2] << 16) | r["words"][3]
        return {"on": bool(r["words"][0]), "timeout_s": r["words"][1],
                "power_w": power - 0x100000000 if power & 0x80000000 else power, "words": r["words"]}

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
        timeout, power = remote["timeout_s"], remote["power_w"]
        command = self.runtime.storage.get_meta(REMOTE_COMMAND) or {}
        # durations instead of times of the user's actions (#149)
        details = {"words": remote["words"], "timeout_s": timeout, "power_w": power,
                   "openampere_last_command_min_ago": round((now - command["ts"]) / 60) if command else None}
        if not remote["on"]:
            return Check("remote", title, "ok", "aus – kein Gerät steuert den Speicher gerade von außen", details)
        # negative = the battery charges (sign not verified on every device, the raw value is in the details)
        what = (f"{abs(power)} W Laden" if power < 0 else f"{power} W Entladen" if power > 0
                else "0 W, also weder Laden noch Entladen")
        # OpenAmpere switches it on with exactly 1 and its own timeout; the smartbox uses other values (#141)
        ours = bool(command) and remote["words"][0] == 1 and timeout == command.get("timeout_s", REMOTE_TIMEOUT_S)
        if ours and getattr(self._driver(), "_remote_owned", None):
            return Check("remote", title, "ok", f"an – OpenAmpere lädt aus dem Netz: {what}", details,
                         "Das ist das Laden aus dem Netz von OpenAmpere (Geräte → Speicher). Es endet zur geplanten Zeit.")
        if ours and not command.get("released"):
            ago = _ago(now - command["ts"])  # a duration, not the time of the owner's action (#149, #223)
            return Check("remote", title, "info", f"an – Rest des Ladens aus dem Netz von OpenAmpere ({what})", details,
                         f"OpenAmpere hat die Fernsteuerung zuletzt {ago} benutzt und am Ende nicht "
                         "abgeschaltet, zum Beispiel wegen eines Neustarts oder einer kurz unterbrochenen Verbindung. "
                         "Es schaltet sie innerhalb einer Minute selbst ab, du musst nichts tun. Ist sie danach noch "
                         "an, melde das bitte als Fehler.")
        seen = self.runtime.storage.get_meta(REMOTE_SEEN) or {}
        if seen.get("on_since") and now - seen.get("checked", 0) <= 2 * WATCH_S:
            duration = seen["checked"] - seen["on_since"]
            details["on_for_min"] = round(duration / 60)
            since = f" Seit mindestens {_duration(duration)} durchgehend an." if duration >= WATCH_S else ""
        else:
            since = ""
        started = self.runtime.storage.last_control("grid_charging", STARTED)
        last = f"{_ago(now - started)} aus dem Netz geladen" if started else "noch nie aus dem Netz geladen"
        who = (f"OpenAmpere benutzt dieselben Werte, hat zuletzt {last} und die Fernsteuerung danach abgeschaltet. "
               "Vielleicht hat der Wechselrichter die Werte nach einem Neustart wiederhergestellt: Dann schaltet "
               "OpenAmpere sie nach dem nächsten Verbindungsaufbau selbst ab. Oder ein zweites Programm steuert den "
               "Wechselrichter, zum Beispiel ein zweites OpenAmpere."
               if ours else f"OpenAmpere war es nicht: Es schaltet die Fernsteuerung mit anderen Werten ein (1 und "
               f"{REMOTE_TIMEOUT_S} s) und hat zuletzt {last}.")
        effect = ("Solange die Vorgabe gilt, folgt der Wechselrichter ihr statt seinem normalen Betrieb. Bei 0 W "
                  "lädt und entlädt der Speicher meist nicht: Schau in der Übersicht, ob die Speicherleistung dauerhaft "
                  "bei 0 W bleibt, obwohl die Sonne scheint oder das Haus Strom braucht. " if power == 0 else
                  "Solange die Vorgabe gilt, folgt der Wechselrichter ihr statt seinem normalen Betrieb. ")
        return Check("remote", title, "warn",
                     f"an – ein anderes Gerät gibt dem Speicher {what} vor (Wert {remote['words'][0]}, Zeitlimit "
                     f"{timeout} s).{since}", details,
                     (f"{who} {effect}" + ("" if ours else
                     "Meist ist das die bisherige Smartbox: Sie setzt Vorgaben aus der Cloud ihres "
                     "Herstellers um und kann dabei das Laden aus dem Netz und die Speicher-Einstellungen von "
                     "OpenAmpere überschreiben. Wenn du das nicht möchtest: der Smartbox im Router den Internetzugang "
                     "sperren oder sie abklemmen (vorher klären, ob sie für etwas anderes gebraucht wird), siehe README "
                     "„Die bisherige Smartbox setzt Einstellungen zurück“. Sonst kannst du den Hinweis ignorieren.")).strip())

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
            info["serial"] = _mask(info.get("serial"))
        now = time.time()
        report = self._pseudonymise({"created": now, "version": self._version(), "device": info,
                                     "connection": {"mode": self.runtime.config.inverter.connection_mode,
                                                    "timeout_s": self.runtime.config.inverter.timeout},
                                     "checks": [asdict(c) for c in checks], "log": logs.RECENT.lines(now)},
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
        number and the addresses of other services; the report is shared publicly."""
        config = self.runtime.config
        known = {config.notify.ntfy_url: "<ntfy-Adresse>", config.evcc.url: "<evcc-Adresse>",
                 config.meter.username: "<Benutzername>"}
        if not include_serial:
            device = self.runtime.collector.device
            stored = (self.runtime.storage.get_meta("firmware") or {}).get("serial")
            for serial in {device.serial if device else None, stored}:
                if serial:
                    known[serial] = _mask(serial)
        pseudonyms = _Pseudonyms(config.inverter.host, known)
        return {**report, "checks": pseudonyms.apply(report.get("checks", [])),
                "log": pseudonyms.apply(report.get("log", []))}

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
                value = (r["words"][0] << 16) | r["words"][1]
                rated = device.rated_power_w if device else None
                plausible = 0 <= value <= (rated or 30_000)
                # rounded: the exact limit tells the exact system size (60 or 70 % of the kWp, #149)
                rounded = round(value, -2)
                checks.append(Check("export_limit", "Einspeisebegrenzung (46616)", "ok" if plausible else "warn",
                                    f"etwa {rounded} W" + ("" if plausible else " – unplausibel, Einheit/Register prüfen"),
                                    {"high_word": r["words"][0], "rounded_w": rounded, "rated_power_w": rated}))
            else:
                checks.append(Check("export_limit", "Einspeisebegrenzung (46616)", "warn", f"{r['error']}"))

            # 10. bms1_connected
            r = await self._try(37002, 1, fc)
            connected = {0: "nein", 1: "ja"}.get(r["words"][0], f"unbekannter Wert {r['words'][0]}") if r["ok"] else None
            checks.append(Check("bms1", "Batterie verbunden (37002)", "info", connected or r["error"], {"words": r.get("words")}))

            checks.append(await self._remote_control(register_map))

        # 6. connection limit (optional: may briefly disturb other devices)
        if connection_test:
            checks.append(await self._connection_test())
        else:
            checks.append(Check("connections", "Gleichzeitige Verbindungen", "skipped",
                                "Nicht ausgeführt (kann andere Geräte kurz stören)."))

        return checks + self._history_checks()

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


def report_markdown(report: dict) -> str:
    """Plain text for copying into an issue."""
    device = report.get("device") or {}
    lines = [f"## OpenAmpere-Diagnose ({datetime.fromtimestamp(report['created']):%d.%m.%Y %H:%M})", "",
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
    lines.append(json.dumps(report["checks"], ensure_ascii=False, indent=1))
    lines += ["```", "", "</details>"]
    log = report.get("log") or []
    if log:  # times relative to the report (-HH:MM:SS)
        lines += ["", f"<details><summary>Protokoll (letzte {len(log)} Zeilen)</summary>", "", "```text",
                  *[line.replace("```", "` ` `") for line in log], "```", "", "</details>"]
    return "\n".join(lines)
