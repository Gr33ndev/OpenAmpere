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

from .runtime import Runtime

log = logging.getLogger(__name__)

UNREACHABLE_AFTER_S = 15 * 60


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


class Notifier:
    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime
        self.sent: dict[str, object] = runtime.storage.get_meta("notify_state") or {}
        self.last_error: str | None = None

    def _remember(self, key: str, value: object) -> None:
        self.sent[key] = value
        self.runtime.storage.set_meta("notify_state", self.sent)

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
                                 "wieder geändert.", "warning")
                    break

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
                await notify("cheap_power", tomorrow.isoformat(), "Strompreis morgen",
                             f"Am günstigsten: {datetime.fromtimestamp(best, tz):%H:%M}–"
                             f"{datetime.fromtimestamp(best + 7200, tz):%H:%M} Uhr, etwa {price:.1f} ct/kWh.",
                             "zap")
        return sent
