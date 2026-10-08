"""Push notifications through ntfy (https://ntfy.sh or an own server): free apps for iPhone and Android,
no account needed, works without HTTPS on the OpenAmpere server. Each event is sent once, not on
every check."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from .collector import LOW_DISK_BYTES
from .runtime import Runtime

log = logging.getLogger(__name__)

UNREACHABLE_AFTER_S = 15 * 60
STORAGE_FAILING_AFTER_S = 5 * 60  # a single failed write (e.g. the database briefly locked) is no reason to warn


def send(url: str, token: str, title: str, message: str, tags: str = "") -> None:
    """Publishes as JSON to the server root (UTF-8 safe, unlike HTTP headers): https://ntfy.sh/topic."""
    parts = urllib.parse.urlsplit(url)
    topic = parts.path.strip("/")
    if parts.scheme not in ("http", "https") or not topic or "/" in topic:
        raise ValueError("Die ntfy-Adresse muss die Form https://server/thema haben.")
    payload = {"topic": topic, "title": title, "message": message, "tags": [t for t in tags.split(",") if t]}
    headers = {"Content-Type": "application/json", "User-Agent": "OpenAmpere"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(f"{parts.scheme}://{parts.netloc}/", data=json.dumps(payload).encode(),
                                     headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - user-configured URL
        response.read(256)


CELL_HOT_C = 45.0  # most lithium iron phosphate cells are specified for charging up to 45-55 °C
CELL_SPREAD_C = 5.0  # cells of a healthy pack stay within a few degrees of each other


def battery_problem(temperatures: dict) -> str | None:
    """A warning text if the battery cells are too warm or unusually far apart, else None."""
    hot = max((temperatures.get(k) for k in ("battery_cell_max", "battery2_cell_max") if temperatures.get(k) is not None),
              default=None)
    spreads = [temperatures[f"{p}_cell_max"] - temperatures[f"{p}_cell_min"] for p in ("battery", "battery2")
               if temperatures.get(f"{p}_cell_max") is not None and temperatures.get(f"{p}_cell_min") is not None]
    if hot is not None and hot >= CELL_HOT_C:
        return f"Die wärmste Batteriezelle hat {hot:.0f} °C. Für Lade- und Entladebetrieb ist das sehr warm."
    if spreads and max(spreads) >= CELL_SPREAD_C:
        return (f"Die Batteriezellen unterscheiden sich um {max(spreads):.1f} °C. Das kann auf eine schwache Zelle "
                "oder schlechte Belüftung hinweisen. Bleibt es so, den Installationsbetrieb fragen.")
    return None


class Notifier:
    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime
        self.sent: dict[str, object] = runtime.storage.get_meta("notify_state") or {}
        self.last_error: str | None = None

    def _remember(self, key: str, value: object) -> None:
        self.sent[key] = value
        try:
            self.runtime.storage.set_meta("notify_state", self.sent)
        except Exception as err:  # noqa: BLE001 - e.g. disk full (#170): kept in memory, so nothing is sent twice
            log.debug("could not store the notification state: %s", err)

    async def push(self, title: str, message: str, tags: str = "") -> bool:
        cfg = self.runtime.config.notify
        if not cfg.ntfy_url:
            return False
        try:
            await asyncio.to_thread(send, cfg.ntfy_url, cfg.ntfy_token, title, message, tags)
            self.last_error = None
            return True
        except Exception as err:  # noqa: BLE001
            self.last_error = f"Senden fehlgeschlagen: {err}"
            log.warning("notification failed: %s", err)
            return False

    async def check(self, now: float | None = None) -> list[str]:
        """Evaluates all events; returns the titles sent (for tests)."""
        now = time.time() if now is None else now
        cfg = self.runtime.config.notify
        if not cfg.ntfy_url:
            return []
        sent: list[str] = []
        collector = self.runtime.collector
        snap = collector.latest
        tz = self.runtime.tz
        today = datetime.fromtimestamp(now, tz).date().isoformat()

        async def notify(key: str, value: object, title: str, message: str, tags: str = "") -> None:
            if self.sent.get(key) != value and await self.push(title, message, tags):
                self._remember(key, value)
                sent.append(title)

        if cfg.on_unreachable and collector.configured:
            since = collector.disconnected_since
            if since and now - since > UNREACHABLE_AFTER_S:
                await notify("unreachable", since, "Wechselrichter nicht erreichbar",
                             f"Seit {datetime.fromtimestamp(since, tz):%H:%M} Uhr keine Verbindung. "
                             f"{collector.last_error or ''}".strip(), "warning")
            elif collector.connected and self.sent.get("unreachable"):
                await notify("unreachable", None, "Wechselrichter wieder erreichbar", "Die Verbindung steht wieder.",
                             "white_check_mark")

        if cfg.on_storage:
            await self._check_storage(notify, now)

        if cfg.on_alarm and snap is not None:
            codes = [a for a in snap.alarms if a]
            key = ",".join(f"{a:04X}" for a in codes) or None
            if key and self.sent.get("alarm") != key:
                await notify("alarm", key, "Störung am Wechselrichter",
                             f"Störungscode: {key}. Details in der App unter Mehr → Meine Anlage.", "rotating_light")
            elif not key and self.sent.get("alarm"):
                self._remember("alarm", None)

        if cfg.on_overwritten:
            last = float(self.sent.get("overwritten_ts") or 0)
            for entry in self.runtime.storage.control_log(20):
                if entry["action"].endswith("_check") and entry["ts"] > last:
                    await notify("overwritten_ts", entry["ts"], "Einstellung überschrieben",
                                 "Ein anderes Gerät (z. B. die bisherige Smartbox) hat eine Einstellung von OpenAmpere "
                                 "wieder geändert. Eine Smartbox holt sich ihre Einstellungen aus der Cloud: Ohne "
                                 "Internetzugang (im Router sperren) bleiben deine Änderungen bestehen.", "warning")
                    break

        if cfg.on_firmware:
            history = self.runtime.storage.get_meta("firmware_history") or []
            if history and history[-1]["ts"] > float(self.sent.get("firmware_ts") or 0):
                change = history[-1]
                await notify("firmware_ts", change["ts"], "Neue Firmware am Wechselrichter",
                             f"Firmware {change['old']} → {change['new']}. Ein Update kann Werte verändern: bitte einmal "
                             "die Diagnose ausführen (Mehr → Diagnose) und das Ergebnis bei Auffälligkeiten melden.",
                             "arrows_counterclockwise")

        if cfg.on_off_grid and snap is not None and snap.off_grid is not None:
            # the confirmed outage, not the flag of one reading, which can be wrong (#89)
            off_grid = self.runtime.collector.outages.current is not None
            if off_grid and not self.sent.get("off_grid"):
                soc = f" Der Speicher ist zu {snap.battery_soc:.0f} % geladen." if snap.battery_soc is not None else ""
                await notify("off_grid", now, "Stromausfall: Notstrombetrieb",
                             f"Das Netz ist weg, das Haus läuft über Speicher und Solaranlage.{soc} "
                             "Große Verbraucher jetzt besser ausschalten.", "warning")
            elif not off_grid and self.sent.get("off_grid"):
                await notify("off_grid", None, "Strom ist wieder da", "Das Netz ist zurück, der Notstrombetrieb ist beendet.",
                             "white_check_mark")

        if cfg.on_battery_health and snap is not None:
            problem = battery_problem(snap.temperatures)
            if problem:
                await notify("battery_health", today, "Speicher prüfen", problem, "warning")

        if cfg.on_battery_full and snap is not None and (snap.battery_soc or 0) >= 99:
            await notify("battery_full", today, "Speicher voll",
                         "Der Speicher ist voll geladen – guter Zeitpunkt für Waschmaschine & Co.", "battery")

        if cfg.on_cheap_power:
            tomorrow = (datetime.fromtimestamp(now, tz) + timedelta(days=1)).date()
            tariff = self.runtime.tariffs.at(tomorrow.isoformat())
            start = datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=tz).timestamp()
            prices = self.runtime.storage.prices(start, start + 86400)
            if tariff.kind == "dynamic" and len(prices) >= 88:  # tomorrow's prices are published (~14:00)
                best = min(prices, key=lambda ts: sum(prices.get(ts + i * 900, 1e9) for i in range(8)))
                price = tariff.import_price_ct(sum(prices.get(best + i * 900, 0) for i in range(8)) / 8)
                price_text = f"{price:.2f}".replace(".", ",")  # German notation, two decimals like everywhere (#87)
                await notify("cheap_power", tomorrow.isoformat(), "Strompreis morgen",
                             f"Am günstigsten: {datetime.fromtimestamp(best, tz):%H:%M}–"
                             f"{datetime.fromtimestamp(best + 7200, tz):%H:%M} Uhr, etwa {price_text} ct/kWh.",
                             "zap")
        return sent

    async def _check_storage(self, notify, now: float) -> None:
        """Once when readings cannot be stored or the disk is nearly full, once when it is fine again (#170)."""
        state = self.runtime.collector.storage_state()
        since = state["failing_since"]
        if since is not None and now - since >= STORAGE_FAILING_AFTER_S:
            reason = {"full": " Der Speicherplatz ist voll.",
                      "read_only": " Die Datenbank lässt sich nicht beschreiben (Zugriffsrechte oder Datenträger)."}
            await notify("storage_failing", since, "Messwerte werden nicht gespeichert",
                         f"Seit {datetime.fromtimestamp(since, self.runtime.tz):%H:%M} Uhr kann OpenAmpere keine "
                         f"Messwerte speichern.{reason.get(state['error'], '')} Bis das behoben ist, fehlen sie im "
                         "Verlauf und in den Auswertungen. Bitte Speicherplatz auf dem Gerät freigeben, auf dem "
                         "OpenAmpere läuft, und den Datenträger prüfen.", "warning")
        elif since is None and self.sent.get("storage_failing"):
            await notify("storage_failing", None, "Messwerte werden wieder gespeichert",
                         "OpenAmpere kann wieder Messwerte speichern.", "white_check_mark")

        free = state["free_bytes"]
        if state["low_space"] and not self.sent.get("storage_low"):
            await notify("storage_low", now, "Wenig Speicherplatz",
                         f"Auf dem Gerät, auf dem OpenAmpere läuft, sind nur noch {free / 1e6:.0f} MB frei. Ist der "
                         "Platz voll, werden keine Messwerte mehr gespeichert. Bitte Platz freigeben, zum Beispiel "
                         "alte Docker-Images löschen, oder die Aufbewahrungsdauer unter Mehr → Daten & Sicherung "
                         "verkürzen.", "warning")
        # a little more than the limit, so free space around the limit does not send a message every few minutes
        elif self.sent.get("storage_low") and (free is None or free >= LOW_DISK_BYTES * 1.25):
            await notify("storage_low", None, "Wieder genug Speicherplatz",
                         "Auf dem Gerät, auf dem OpenAmpere läuft, ist wieder genug Platz frei.", "white_check_mark")
