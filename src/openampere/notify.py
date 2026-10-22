# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
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
from dataclasses import dataclass
from datetime import datetime, timedelta

from .collector import LOW_DISK_BYTES
from .outbound import open_without_redirect
from .runtime import Runtime

log = logging.getLogger(__name__)

UNREACHABLE_AFTER_S = 15 * 60
RELOCATED_RECENT_S = 24 * 3600  # notifications switched on later do not report an old change of address
STORAGE_FAILING_AFTER_S = 5 * 60  # a single failed write (e.g. the database briefly locked) is no reason to warn
# a fault that is gone by itself within this time is no reason to wake anyone, e.g. the about ten minutes of fault
# every night of SAJ E2 inverters (#404); a fault that lasts is still reported
ALARM_AFTER_S = 30 * 60
# surplus charging stops with every cloud and a car pauses on its own near the end: a pause this long while the car
# stays plugged in counts as the end of the charge, a shorter one is no reason for a message (#451)
CHARGE_PAUSE_S = 15 * 60
# after an end message the car charges on in the same session ("lädt wieder") only if the counter went on from a
# real figure within this time; otherwise the car may have been unplugged unseen, e.g. with a portable wallbox
AGAIN_MIN_KWH = 0.5
SHOWN_MIN_KWH = 0.05  # less is "0,0 kWh": not shown, and not worth leaving out the duration of a later charge for
AGAIN_WITHIN_S = 12 * 3600


@dataclass
class ChargePoint:
    """A wallbox or an evcc loadpoint, as the charge notifications see it."""
    name: str
    charging: bool
    connected: bool | None  # None: the wallbox does not tell
    power_w: float = 0.0
    kwh: float | None = None  # counted by the wallbox or evcc since the car was plugged in
    solar_pct: float | None = None  # evcc: the solar share of that energy


def _decimal(value: float) -> str:
    return f"{value:.1f}".replace(".", ",")  # German notation


def _duration(seconds: float) -> str:
    minutes = round(seconds / 60)
    hours, minutes = divmod(minutes, 60)
    if not hours:
        return f"{minutes} Min"  # the sentence's full stop ends "Min."
    return f"{hours} Std. {minutes} Min" if minutes else f"{hours} Std"


def charge_started(point: ChargePoint, again: bool) -> str:
    power = f", mit etwa {_decimal(point.power_w / 1000)} kW" if point.power_w >= 100 else ""
    return f"{point.name}: Das Auto lädt wieder{power}." if again else f"{point.name}: Das Laden hat begonnen{power}."


def charge_ended(point: ChargePoint, seconds: float | None) -> str:
    """seconds: from the start to the last check that saw the car charging; None when the energy since plugging in
    includes an earlier charge, or OpenAmpere was restarted during the charge."""
    text = f"{point.name}: Das Laden ist beendet."
    if point.kwh is not None and point.kwh >= SHOWN_MIN_KWH:
        during = f" in {_duration(seconds)}" if seconds is not None and seconds >= 60 else ""
        text += f" Seit dem Anstecken geladen: {_decimal(point.kwh)} kWh{during}."
        if point.solar_pct is not None:
            text += f" Davon etwa {point.solar_pct:.0f} % Solarstrom."
    elif point.kwh is None and seconds is not None and seconds >= 60:  # a wallbox that reports no energy
        text = f"{point.name}: Das Laden ist beendet, nach {_duration(seconds)}."
    return text


def send(url: str, token: str, title: str, message: str, tags: str = "") -> None:
    """Publishes as JSON to the server root (UTF-8 safe, unlike HTTP headers): https://ntfy.sh/topic."""
    if "@" in url:  # never part of a topic; urllib would take user name and password for the host name and quote
        # them in its error, which goes into the log and the shared diagnostics report
        raise ValueError("Benutzername und Passwort gehören nicht in die ntfy-Adresse. Bitte die Adresse ohne sie "
                         "eintragen. Für ein geschütztes Thema gehört ein Token von ntfy in das Feld „Zugangs-Token“.")
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
    with open_without_redirect(request, timeout=10) as response:  # the token goes to this server only
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
    def __init__(self, runtime: Runtime, evcc=None, wallboxes=None) -> None:
        self.runtime = runtime
        self.evcc = evcc  # evcc.Evcc
        self.wallboxes = wallboxes  # wallbox.native.NativeWallboxes
        self.sent: dict[str, object] = runtime.storage.get_meta("notify_state") or {}
        self.last_error: str | None = None
        self.fault_since: float | None = None  # first reading of the current fault, any code
        self.charging_at: dict[str, float] = {}  # per charge point: last check that saw it charging
        self.last_charge: dict[str, ChargePoint] = {}  # per charge point: as last seen charging, for the summary

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

        if cfg.on_unreachable:
            # found at a new address and switched over (discovery.Rediscovery): the owner should know (#351)
            relocated = self.runtime.storage.get_meta("relocated")
            ts = relocated.get("ts") if isinstance(relocated, dict) else None
            if (isinstance(ts, (int, float)) and now - ts < RELOCATED_RECENT_S
                    and ts > float(self.sent.get("relocated_ts") or 0)):
                await notify("relocated_ts", ts, "Wechselrichter unter neuer Adresse",
                             "OpenAmpere hat den Wechselrichter unter einer neuen Adresse im Heimnetz gefunden und "
                             "verbindet sich jetzt mit ihr. Das passiert, wenn der Router ihm eine neue Adresse gibt. "
                             "Die Adressen stehen in der App unter Mehr → Steuerung und Protokoll. Hast du nichts "
                             "geändert und kennst die neue Adresse nicht, schalte die Steuerung dort aus.", "warning")

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
            # since the first reading with any code: a fault whose code changes is still reported in time
            self.fault_since = (self.fault_since or now) if key else None
            if key and now - self.fault_since >= ALARM_AFTER_S and self.sent.get("alarm") != key:
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

        points = self._charge_points(now) if cfg.on_wallbox else []
        for key, point in points:
            await self._check_charge(notify, key, point, now)
        # a charge started while the switch was on, or at a wallbox removed since, ends without a late message
        checked = {key for key, _ in points}
        for key in [k for k, v in self.sent.items() if k.startswith("charge_") and v is not None and k not in checked]:
            if (cfg.on_wallbox and key.startswith("charge_evcc_") and self.evcc is not None and self.evcc.configured
                    and self.evcc.fresh() is None):
                continue  # evcc not answering for a while is no reason to forget its charge
            self._remember(key, None)
            self.charging_at.pop(key, None)
            self.last_charge.pop(key, None)
        return sent

    def _charge_points(self, now: float) -> list[tuple[str, ChargePoint]]:
        """Wallboxes and evcc loadpoints with a recent reading. A wallbox without one during a charge counts as
        pausing: a portable one is often unplugged together with the car. An evcc loadpoint without one is skipped."""
        points = []
        for wb in self.wallboxes.enabled() if self.wallboxes else []:
            key = f"charge_{wb.id}"
            reading = self.wallboxes.fresh(wb, now)  # the charger itself, also while its control is only simulated
            if reading is not None:
                points.append((key, ChargePoint(
                    wb.name, reading.charging, None if reading.state == "unknown" else reading.connected,
                    reading.power_w, reading.session_kwh)))
            elif self.sent.get(key) is not None:
                points.append((key, ChargePoint(wb.name, charging=False, connected=None)))
        state = self.evcc.fresh() if self.evcc is not None and self.evcc.configured else None
        for lp in (state or {}).get("loadpoints", []):
            if not lp["heating"]:  # a heat pump in evcc is no car
                kwh = lp["session_wh"] / 1000 if lp["session_wh"] is not None else None
                points.append((f"charge_evcc_{lp['id']}", ChargePoint(
                    lp["title"], lp["charging"], lp["connected"], power_w=lp["power_w"], kwh=kwh,
                    solar_pct=lp["session_solar_pct"])))
        return points

    async def _check_charge(self, notify, key: str, point: ChargePoint, now: float) -> None:
        """Once when a charge starts, once when it ends: unplugged, or not charging for CHARGE_PAUSE_S. "Complete"
        of a wallbox is no end of its own: the dé wallbox may report it also after a stop by OpenAmpere's solar
        control (docs/devices/tuya/de-portable.md), and a car may charge on after it. The end message gives the energy
        the wallbox or evcc counted since plugging in, so a charge that goes on after an end message ("lädt wieder")
        ends with the total of both. Then and whenever the counter includes an earlier charge, it gives no duration."""
        # {"since", "partial"}: a charge since this time; {"since", "ended", "kwh", "at"}: over, the car still plugged in
        state = self.sent.get(key)
        state = state if isinstance(state, dict) and "since" in state else None
        running = state is not None and not state.get("ended")
        if point.charging:
            self.charging_at[key] = now
            self.last_charge[key] = point
            if not running:
                # the counter went on from the end message: it includes that charge (lower: unplugged unseen)
                ended_kwh = state.get("kwh") if state is not None else None
                partial = (ended_kwh is not None and ended_kwh >= SHOWN_MIN_KWH and point.kwh is not None
                           and point.kwh >= ended_kwh)
                again = partial and ended_kwh >= AGAIN_MIN_KWH and now - state.get("at", 0) <= AGAIN_WITHIN_S
                await notify(key, {"since": now, "partial": partial}, "Auto lädt", charge_started(point, again),
                             "electric_plug")
            return
        if state is None:
            return
        if not running:
            if point.connected is False:
                self._remember(key, None)
            return
        seen_charging = key in self.last_charge  # not after a restart: OpenAmpere may have been down for hours
        last = self.charging_at.setdefault(key, now)  # after a restart the pause counts from now
        if point.connected is not False and now - last < CHARGE_PAUSE_S:
            return
        # as last seen charging: a wallbox gone or unplugged may report no counters or 0 (a precaution, not seen yet)
        seen = self.last_charge.get(key) or point
        ended = None if point.connected is False else {"since": state["since"], "ended": True, "kwh": seen.kwh,
                                                         "at": now}
        seconds = last - state["since"] if seen_charging and not state.get("partial") else None
        await notify(key, ended, "Laden beendet", charge_ended(seen, seconds), "white_check_mark")
        if self.sent.get(key) == ended:  # otherwise kept for the next try
            self.charging_at.pop(key, None)
            self.last_charge.pop(key, None)

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
