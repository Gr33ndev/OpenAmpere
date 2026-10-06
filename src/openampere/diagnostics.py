"""Read-only diagnostics for the real device ("Liste E" of the on-site checks).

Everything here only reads. The result is a report that users can share (e.g. in a GitHub issue) so
register maps, scaling factors and device behaviour can be verified for more devices. The serial number
is masked unless the user explicitly includes it.
"""

from __future__ import annotations

import asyncio
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime

from .drivers.modbus import ModbusIllegalError, ModbusReadError, ModbusTransientError
from .runtime import Runtime

EXTRA_CONNECTIONS = 3


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


class Diagnostics:
    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime
        self.running = False
        self.last: dict | None = runtime.storage.get_meta("diagnostics_report")

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

    async def _remote_control(self, register_map) -> Check:
        """Who controls the battery from outside right now: nobody, OpenAmpere itself (charging from the grid) or
        another device, usually the previous smartbox following targets from its cloud."""
        enable = register_map.settings["remote_enable"]
        title = f"Fernsteuerung ({enable.address})"
        r = await self._try(enable.address, 4, 3)  # on/off, watchdog timeout, power (32 bit)
        if not r["ok"]:
            return Check("remote", title, "info", f"nicht lesbar ({r['error']})")
        on, timeout = r["words"][0], r["words"][1]
        power = (r["words"][2] << 16) | r["words"][3]
        power = power - 0x100000000 if power & 0x80000000 else power
        details = {"words": r["words"], "timeout_s": timeout, "power_w": power}
        if not on:
            return Check("remote", title, "ok", "aus – kein Gerät steuert den Speicher gerade von außen", details)
        # negative = the battery charges (sign not verified on every device, the raw value is in the details)
        what = f"{abs(power)} W Laden" if power < 0 else f"{power} W Entladen" if power > 0 else "0 W (Speicher ruht)"
        lapse = f"die Vorgabe endet von selbst, wenn sie nicht innerhalb von {timeout} s erneuert wird"
        if getattr(self._driver(), "_remote_owned", False):
            return Check("remote", title, "ok", f"an – OpenAmpere lädt aus dem Netz: {what}, {lapse}", details,
                         "Das ist das Laden aus dem Netz von OpenAmpere (Geräte → Speicher). Es endet zur geplanten Zeit.")
        return Check("remote", title, "warn", f"an – ein anderes Gerät gibt dem Speicher {what} vor, {lapse}", details,
                     "Meist ist das die bisherige Smartbox: Sie setzt Vorgaben aus der Cloud ihres Herstellers um und "
                     "kann dabei das Laden aus dem Netz und die Speicher-Einstellungen von OpenAmpere überschreiben. "
                     "Wenn du das nicht möchtest: der Smartbox im Router den Internetzugang sperren oder sie abklemmen "
                     "(vorher klären, ob sie für etwas anderes gebraucht wird), siehe README „Die bisherige Smartbox "
                     "setzt Einstellungen zurück“. Sonst kannst du den Hinweis ignorieren.")

    async def run(self, *, connection_test: bool = False, include_serial: bool = False) -> dict:
        if self.running:
            raise RuntimeError("Die Diagnose läuft bereits.")
        collector = self.runtime.collector
        if collector.driver is None or not collector.connected:
            raise RuntimeError("Der Wechselrichter ist nicht verbunden.")
        self.running = True
        try:
            checks = await self._checks(connection_test)
        finally:
            self.running = False
        device = collector.device
        info = asdict(device) if device else {}
        if not include_serial:
            info["serial"] = _mask(info.get("serial"))
        report = {"created": time.time(), "version": self._version(), "device": info,
                  "connection": {"mode": self.runtime.config.inverter.connection_mode,
                                 "timeout_s": self.runtime.config.inverter.timeout},
                  "checks": [asdict(c) for c in checks]}
        self.runtime.storage.set_meta("diagnostics_report", report)
        self.last = report
        return report

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
                checks.append(Check("export_limit", "Einspeisebegrenzung (46616)", "ok" if plausible else "warn",
                                    f"{value} W" + ("" if plausible else " – unplausibel, Einheit/Register prüfen"),
                                    {"words": r["words"], "rated_power_w": rated}))
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
                             "glitches": glitches[-10:]}))
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
    device = report.get("device", {})
    lines = [f"## OpenAmpere-Diagnose ({datetime.fromtimestamp(report['created']):%d.%m.%Y %H:%M})", "",
             f"- Version: {report.get('version')}",
             f"- Gerät: {device.get('manufacturer')} {device.get('model')} (Firmware {device.get('firmware')}, "
             f"Seriennr. {device.get('serial')})",
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
    return "\n".join(lines)
