"""Access from anywhere with Tailscale (#83).

When asked for, install.sh sets up a tailscale container next to OpenAmpere. Like the updater, the app never gets
access to it: it only writes a request file into the data folder ("login" or "logout"), scripts/tailscale.sh in the
container carries it out and writes the state of Tailscale back next to it (the output of `tailscale status --json`).
Logging in happens with a link to Tailscale; afterwards the app is reachable under the Tailscale name of this
machine from every device that is logged in to the same Tailscale account.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from .runtime import Runtime

HELPER_ALIVE_S = 60  # the helper writes a sign of life every few seconds
STARTING_S = 60  # after "Einrichten", how long to wait for the login link before showing an error
STOPPING_S = 15  # after "Trennen", until the helper has logged out


class Remote:
    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime
        self.login_requested = 0.0
        self.logout_requested = 0.0

    @property
    def folder(self) -> Path:
        return Path(self.runtime.config.storage.path).resolve().parent / "remote"

    def helper_ready(self, now: float | None = None) -> bool:
        try:
            alive = float((self.folder / "alive").read_text().strip())
        except (OSError, ValueError):
            return False
        return (time.time() if now is None else now) - alive < HELPER_ALIVE_S

    def status(self) -> dict:
        try:
            data = json.loads((self.folder / "status.json").read_text())
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def view(self, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        port = self.runtime.config.server.port
        if not self.helper_ready(now):
            return {"available": False, "state": "unavailable", "port": port}
        status = self.status()
        backend = status.get("BackendState")
        me = status.get("Self") or {}
        name = str(me.get("DNSName") or "").rstrip(".")
        ipv4 = next((ip for ip in me.get("TailscaleIPs") or [] if "." in ip), None)
        user = (status.get("User") or {}).get(str(me.get("UserID"))) or {}
        login_url = str(status.get("AuthURL") or "")
        if not login_url.startswith("https://"):
            login_url = ""
        if backend == "Running" and now - self.logout_requested < STOPPING_S:
            state = "stopping"
        elif backend == "Running":
            state = "connected"
        elif backend == "NeedsMachineAuth":
            state = "approval"  # the tailnet wants new devices to be approved in the Tailscale admin console
        elif login_url:
            state = "login"
        elif now - self.login_requested < STARTING_S:
            state = "starting"
        elif self.login_requested and backend in ("NeedsLogin", "NoState", None):
            state = "failed"
        else:
            state = "off"
        host = name or ipv4
        return {"available": True, "state": state, "port": port,
                "login_url": login_url if state == "login" else None,
                "address": f"http://{host}:{port}" if state == "connected" and host else None,
                "name": name or None, "ip": ipv4, "account": user.get("LoginName") or None}

    def request(self, action: str, now: float | None = None) -> None:
        now = time.time() if now is None else now
        if action not in ("login", "logout"):
            raise ValueError("Unbekannte Aktion.")
        if not self.helper_ready(now):
            raise RuntimeError("Tailscale ist nicht eingerichtet. Bitte einmal das Install-Script erneut ausführen "
                               "und die Frage nach dem Zugriff von unterwegs mit Ja beantworten.")
        before = self.view(now)["state"]
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / "request").write_text(action)
        self.login_requested = now if action == "login" else 0.0
        self.logout_requested = now if action == "logout" else 0.0
        self.runtime.storage.log_control("remote_access", {"from": {"remote_access": before},
                                                           "to": {"remote_access": action}}, False, "ok")
