"""New versions: find them on GitHub and ask the updater container to install them.

The app looks up the latest release every few hours (it can be switched off). Installing needs the small updater
container that install.sh sets up: the app only writes a request file into the data folder, the updater pulls the
new image and restarts the containers (see scripts/updater.sh). The app itself never gets access to Docker.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import urllib.request
from datetime import datetime
from pathlib import Path

from .runtime import Runtime

log = logging.getLogger(__name__)

RELEASES_URL = "https://api.github.com/repos/Gr33ndev/OpenAmpere/releases/latest"
CHECK_EVERY_S = 6 * 3600
UPDATER_ALIVE_S = 60  # the updater writes a sign of life every few seconds
AUTO_HOURS = range(2, 5)  # automatic updates at night, when nobody looks at the app


def parse_version(text: str | None) -> tuple[int, ...] | None:
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", (text or "").strip())
    return tuple(int(x) for x in match.groups()) if match else None


def fetch_latest() -> dict:
    request = urllib.request.Request(RELEASES_URL, headers={"Accept": "application/vnd.github+json",
                                                            "User-Agent": "OpenAmpere"})
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - fixed https URL
        data = json.load(response)
    return {"version": str(data.get("tag_name", "")).lstrip("v"), "url": data.get("html_url"),
            "notes": data.get("body") or "", "published": data.get("published_at")}


class Updates:
    def __init__(self, runtime: Runtime, current: str) -> None:
        self.runtime = runtime
        self.current = current
        self.latest: dict | None = runtime.storage.get_meta("update_latest")
        self.checked = 0.0
        self.error: str | None = None

    @property
    def folder(self) -> Path:
        return Path(self.runtime.config.storage.path).resolve().parent / "update"

    @property
    def available(self) -> bool:
        new, now = parse_version((self.latest or {}).get("version")), parse_version(self.current)
        return bool(new and now and new > now)

    def updater_ready(self, now: float | None = None) -> bool:
        try:
            alive = float((self.folder / "updater-alive").read_text().strip())
        except (OSError, ValueError):
            return False
        return (time.time() if now is None else now) - alive < UPDATER_ALIVE_S

    def status(self) -> dict | None:
        try:
            return json.loads((self.folder / "status.json").read_text())
        except (OSError, ValueError):
            return None

    def view(self) -> dict:
        cfg = self.runtime.config.updates
        return {"current": self.current, "latest": self.latest, "available": self.available,
                "updater": self.updater_ready(), "requested": (self.folder / "request").exists(),
                "status": self.status(), "check": cfg.check, "auto": cfg.auto,
                "checked": self.checked or None, "error": self.error}

    async def check(self, force: bool = False, now: float | None = None) -> None:
        now = time.time() if now is None else now
        if not self.runtime.config.updates.check or (not force and self.checked and now - self.checked < CHECK_EVERY_S):
            return
        self.checked = now
        try:
            self.latest = await asyncio.to_thread(fetch_latest)
            self.error = None
            self.runtime.storage.set_meta("update_latest", self.latest)
        except Exception as err:  # noqa: BLE001 - offline or no release yet: try again later
            self.error = "Konnte nicht nach Updates suchen."
            log.info("update check failed: %s", err)

    def request(self, reason: str = "manual", now: float | None = None) -> None:
        now = time.time() if now is None else now
        if not self.updater_ready(now):
            raise RuntimeError("Der Update-Helfer läuft nicht. Bitte einmal das Install-Script erneut ausführen.")
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / "request").write_text(json.dumps({"ts": now, "from": self.current,
                                                          "to": (self.latest or {}).get("version"), "reason": reason}))
        self.runtime.storage.log_control("update", {"from": {"version": self.current},
                                                    "to": {"version": (self.latest or {}).get("version")}, "by": reason},
                                         False, "ok")

    async def tick(self, now: float | None = None) -> None:
        """Hourly job: look for a new version and, if wanted, install it at night."""
        now = time.time() if now is None else now
        await self.check(now=now)
        if not (self.runtime.config.updates.auto and self.available and self.updater_ready(now)):
            return
        local = datetime.fromtimestamp(now, self.runtime.tz)
        tried = self.runtime.storage.get_meta("update_auto_tried")
        target = (self.latest or {}).get("version")
        if local.hour in AUTO_HOURS and tried != target:
            self.runtime.storage.set_meta("update_auto_tried", target)  # once per version, also if it fails
            self.request("auto", now)
