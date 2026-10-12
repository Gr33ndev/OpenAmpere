# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Running as a Home Assistant app (#300).

The Supervisor starts the app with SUPERVISOR_TOKEN set and shows the web app in the Home Assistant sidebar through
its ingress proxy. That proxy has already checked the Home Assistant login and is the only client connecting from
INGRESS_PROXY. Docker installations never have SUPERVISOR_TOKEN, so nothing here changes them."""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import shutil
import socket
import sqlite3
import time
import urllib.request
from pathlib import Path

import websockets
from starlette.requests import HTTPConnection
from websockets.exceptions import WebSocketException

from . import i18n

INGRESS_PROXY = "172.30.32.2"
SUPERVISOR_URL = "http://supervisor"
USER_HEADER = "x-remote-user-id"  # set by the Supervisor for every request through the sidebar, never by the browser
ADMIN_GROUP = "system-admin"
USERS_REFRESH_S = 60  # an admin whose rights were taken away keeps them at most this long
UNKNOWN_USER_RETRY_S = 5  # a new admin is asked for again after a few seconds
BACKUP_COPY = "ha-backup.db"  # next to the database; the database itself is excluded from the backup (config.yaml)
WEB_PORT = "8080/tcp"  # inside the container; the port on the host is set in the app's network settings
HTTPS_PORT = "8443/tcp"
REFRESH_S = 600
# the Home Assistant integration that belongs to this version (Dockerfile stage ha-app) and where it goes, so that app
# users need no HACS (#300)
INTEGRATION_SOURCE = Path("/usr/share/openampere/integration")
HA_CONFIG = Path("/homeassistant")  # config folder of Home Assistant (map: homeassistant_config)
INTEGRATION_MARKER = ".installed-by-openampere-app"  # only a copy with this file is replaced on an update
# what the start did with the integration: from ha-app-start (root) to OpenAmpere in the environment, so the root step
# writes nothing into the data folder of the app user (a link placed there could redirect it, #300)
INTEGRATION_ENV = "OPENAMPERE_HA_INTEGRATION"
INTEGRATION_NOTIFIED = "ha-integration.json"  # next to the database, written by OpenAmpere: the last notice

log = logging.getLogger(__name__)


def active() -> bool:
    return bool(os.environ.get("SUPERVISOR_TOKEN"))


def via_ingress(request: HTTPConnection) -> bool:
    """A request through the sidebar. Only the TCP peer counts: uvicorn runs without proxy headers, so a client
    cannot pretend to be the proxy with X-Forwarded-For."""
    return active() and request.client is not None and request.client.host == INGRESS_PROXY


def is_admin_user(user: dict) -> bool:
    """An active owner or member of the admin group of Home Assistant (config/auth/list)."""
    return bool(user.get("is_active")) and (bool(user.get("is_owner")) or ADMIN_GROUP in (user.get("group_ids") or []))


async def home_assistant_users(timeout: float = 10.0) -> list[dict]:
    """The users of Home Assistant, through the WebSocket API of Home Assistant behind the Supervisor
    (homeassistant_api in config.yaml)."""
    async with websockets.connect(f"{SUPERVISOR_URL.replace('http', 'ws', 1)}/core/websocket", open_timeout=timeout,
                                  close_timeout=2) as ws:
        await asyncio.wait_for(ws.recv(), timeout)  # auth_required
        await ws.send(json.dumps({"type": "auth", "access_token": os.environ.get("SUPERVISOR_TOKEN", "")}))
        if json.loads(await asyncio.wait_for(ws.recv(), timeout)).get("type") != "auth_ok":
            raise OSError("Home Assistant refused the connection")
        await ws.send(json.dumps({"id": 1, "type": "config/auth/list"}))
        while True:
            answer = json.loads(await asyncio.wait_for(ws.recv(), timeout))
            if answer.get("id") == 1:
                break
    if not answer.get("success"):
        raise OSError(f"Home Assistant did not list its users: {answer.get('error')}")
    return answer.get("result") or []


class Admins:
    """Which users of Home Assistant are admins: only they may change anything through the sidebar (#300).
    panel_admin only hides the sidebar entry, the Supervisor forwards the requests of every user of Home Assistant.
    Without an answer from Home Assistant nobody counts as admin."""

    def __init__(self, fetch=None) -> None:
        self.fetch = fetch  # None: home_assistant_users
        self.admins: set[str] = set()
        self.names: dict[str, str] = {}
        self.checked: float | None = None
        self.lock = asyncio.Lock()

    def _due(self, user_id: str, now: float) -> bool:
        if self.checked is None:
            return True
        age = now - self.checked
        return age > USERS_REFRESH_S or (user_id not in self.admins and age > UNKNOWN_USER_RETRY_S)

    async def is_admin(self, user_id: str | None, now: float | None = None) -> bool:
        if not user_id:
            return False
        now = time.monotonic() if now is None else now
        if self._due(user_id, now):
            async with self.lock:
                if self._due(user_id, now):  # not already asked by a request that waited for the lock
                    try:
                        users = await (self.fetch or home_assistant_users)()
                        self.admins = {user["id"] for user in users if is_admin_user(user)}
                        self.names = {user["id"]: str(user.get("name") or user.get("username") or "")
                                      for user in users}
                    except (OSError, ValueError, asyncio.TimeoutError, WebSocketException) as err:
                        log.warning("could not read the users of Home Assistant: %s", err)
                        self.admins, self.names = set(), {}
                    self.checked = now
        return user_id in self.admins

    def name(self, user_id: str | None) -> str:
        """The name of a user of Home Assistant from the last list, for the control log (#305)."""
        return self.names.get(user_id or "", "")


def supervisor(path: str, body: dict | None = None, timeout: float = 10.0) -> dict:
    """GET (or POST with a body) on the Supervisor API, e.g. /network/info; returns its "data". Also reaches the API of
    Home Assistant under /core/api (homeassistant_api in config.yaml)."""
    headers = {"Authorization": f"Bearer {os.environ.get('SUPERVISOR_TOKEN', '')}"}
    data = None
    if body is not None:
        data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
    request = urllib.request.Request(f"{SUPERVISOR_URL}{path}", data=data, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed internal address
        answer = json.load(response)
    if not isinstance(answer, dict):
        return {}
    return answer.get("data", {}) if "result" in answer else answer  # /core/api/... answers without the envelope


def discovery(installation_id: str, port: int, fingerprint: str) -> dict:
    """What the app tells Home Assistant about itself, so the integration offers to set it up (#300). No token: the
    Supervisor keeps this where Home Assistant and admin apps can read it, the integration pairs as usual. The host
    name of the container is the address of the app in the internal network."""
    return {"service": "openampere", "config": {"host": socket.gethostname(), "port": port, "fingerprint": fingerprint,
                                                "installation_id": installation_id}}


def home_prefixes(network: dict) -> list[str]:
    """The /24 networks of the Home Assistant host from /network/info, e.g. ["192.168.178"]. The app itself only
    sees its internal network (172.30.x.x), so the device search asks the Supervisor."""
    prefixes = set()
    for interface in network.get("interfaces") or []:
        if not interface.get("connected", True):
            continue
        for address in (interface.get("ipv4") or {}).get("address") or []:
            try:
                ip = ipaddress.ip_interface(address).ip
            except ValueError:
                continue
            if ip.version == 4 and ip.is_private and not ip.is_loopback:
                prefixes.add(str(ip).rsplit(".", 1)[0])
    return sorted(prefixes)


def direct_url(app: dict, network: dict) -> str | None:
    """Address for direct access (evcc, phones), e.g. http://192.168.178.20:8780: the host's address in the home
    network and the port the owner chose for the app. None if direct access is turned off."""
    port = (app.get("network") or {}).get(WEB_PORT)
    if not port:
        return None
    interfaces = sorted(network.get("interfaces") or [], key=lambda i: not i.get("primary"))
    for interface in interfaces:
        for address in (interface.get("ipv4") or {}).get("address") or []:
            try:
                ip = ipaddress.ip_interface(address).ip
            except ValueError:
                continue
            if ip.version == 4 and not ip.is_loopback:
                return f"http://{ip}:{port}"
    return None


class HomeAssistant:
    """What the web app shows about the app, read from the Supervisor every few minutes (the background jobs)."""

    def __init__(self) -> None:
        self.direct_url: str | None = None
        self.ports: dict = {}
        self.checked = 0.0

    async def notify_integration(self, folder: Path) -> None:
        """After a start that installed or updated the integration (or found one from HACS): a notice in Home
        Assistant, in its language, once per result and version."""
        try:
            status = json.loads(os.environ.get(INTEGRATION_ENV) or "{}")
        except ValueError:
            return
        message = integration_notice(status) if isinstance(status, dict) else None
        if not active() or not message:
            return
        status = {"result": status.get("result"), "version": status.get("version")}
        path = folder / INTEGRATION_NOTIFIED
        try:
            if json.loads(path.read_text()) == status:
                return  # shown after an earlier start already
        except (OSError, ValueError):
            pass
        try:
            lang = (await asyncio.to_thread(supervisor, "/core/api/config")).get("language") or "de"
        except (OSError, ValueError):
            lang = "de"
        body = {"title": "OpenAmpere", "message": i18n.translate(message, lang),
                "notification_id": "openampere_integration"}
        try:
            await asyncio.to_thread(supervisor, "/core/api/services/persistent_notification/create", body)
        except (OSError, ValueError) as err:
            log.warning("could not show the notice in Home Assistant: %s", err)
            return
        path.write_text(json.dumps(status))

    async def announce(self, installation_id: str, port: int, fingerprint: str | None) -> None:
        """At the start: the Supervisor passes it on to Home Assistant (the same message only once)."""
        if not active() or not port or not fingerprint:
            return
        try:
            await asyncio.to_thread(supervisor, "/discovery", discovery(installation_id, port, fingerprint))
        except (OSError, ValueError) as err:
            log.warning("could not announce the app to Home Assistant: %s", err)

    def view(self) -> dict | None:
        """The ports on the host as the owner set them (None: turned off); the address only if the Supervisor knows
        the IP of the host."""
        if not active():
            return None
        return {"direct_url": self.direct_url, "web_port": self.ports.get(WEB_PORT),
                "https_port": self.ports.get(HTTPS_PORT)}

    async def refresh(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        if not active() or now - self.checked < REFRESH_S:
            return
        self.checked = now
        try:
            app, network = await asyncio.gather(asyncio.to_thread(supervisor, "/addons/self/info"),
                                                asyncio.to_thread(supervisor, "/network/info"))
            self.ports = app.get("network") or {}
            self.direct_url = direct_url(app, network)
        except (OSError, ValueError) as err:
            log.warning("could not read the network settings from the Supervisor: %s", err)


def install_integration(source: Path, config: Path) -> dict:
    """At the start of the app, as root (ha-app-start): puts the integration of this version into custom_components of
    Home Assistant. A copy the app installed itself is replaced when the version changes; one from HACS or copied by
    hand stays as it is. Result: installed, updated, current, foreign (not ours) or missing (no config folder)."""
    version = json.loads((source / "manifest.json").read_text())["version"]
    target = config / "custom_components" / "openampere"
    marker = target / INTEGRATION_MARKER
    if not config.is_dir():
        return {"result": "missing", "version": version}
    if target.exists() and not marker.is_file():
        return {"result": "foreign", "version": version}
    if marker.is_file() and marker.read_text().strip() == version:
        return {"result": "current", "version": version}
    outcome = "updated" if target.exists() else "installed"
    # copied next to it first, then swapped: Home Assistant never sees half a folder
    staging, previous = target.with_name(".openampere-new"), target.with_name(".openampere-old")
    for folder in (staging, previous):
        shutil.rmtree(folder, ignore_errors=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, staging, ignore=shutil.ignore_patterns("__pycache__"))
    (staging / INTEGRATION_MARKER).write_text(f"{version}\n")
    if target.exists():
        os.replace(target, previous)
    os.replace(staging, target)
    shutil.rmtree(previous, ignore_errors=True)
    return {"result": outcome, "version": version}


def integration_notice(status: dict) -> str | None:
    """The notice in Home Assistant after the start, in German (translated with the language of Home Assistant)."""
    outcome, version = status.get("result"), status.get("version")
    notice = None
    if outcome == "installed":
        notice = ("Die Integration OpenAmpere ist installiert. Bitte Home Assistant einmal neu starten (Einstellungen → "
                  "System → Neu starten). Danach steht unter Einstellungen → Geräte & Dienste „Entdeckt: OpenAmpere“.")
    elif outcome == "updated":
        notice = (f"Die Integration OpenAmpere ist jetzt auf Version {version}. Bitte Home Assistant einmal neu "
                  "starten, damit sie aktiv wird.")
    elif outcome == "foreign":
        notice = ("Die Integration OpenAmpere ist schon installiert, zum Beispiel über HACS. Die App hält sie deshalb "
                  "nicht selbst aktuell. Entferne sie in HACS und starte die App neu, dann übernimmt das die App.")
    return notice


def write_backup_copy(db: Path) -> Path:
    """Before Home Assistant makes a backup (backup_pre): a consistent copy of the whole database next to it. A copy of
    the file while it is written can be broken, so the backup leaves out the database and takes this copy. Unlike the
    download in the app, it keeps password, sessions and app tokens: a restore must not lock the owner out."""
    target = db.with_name(BACKUP_COPY)
    partial = target.with_name(f"{BACKUP_COPY}.part")
    source = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=30)  # the server keeps writing meanwhile
    try:
        copy = sqlite3.connect(str(partial))
        try:
            source.backup(copy)
        finally:
            copy.close()
    finally:
        source.close()
    os.replace(partial, target)
    return target


def remove_backup_copy(db: Path) -> None:
    """After the backup (backup_post)."""
    db.with_name(BACKUP_COPY).unlink(missing_ok=True)
    db.with_name(f"{BACKUP_COPY}.part").unlink(missing_ok=True)


def adopt_backup_copy(db: Path) -> bool:
    """At the start: after Home Assistant restored a backup, only the copy is there and becomes the database. A copy
    next to an existing database is a left-over of a backup that was not finished and is removed."""
    copy = db.with_name(BACKUP_COPY)
    if not copy.is_file():
        return False
    if db.exists():
        remove_backup_copy(db)
        return False
    os.replace(copy, db)
    return True
