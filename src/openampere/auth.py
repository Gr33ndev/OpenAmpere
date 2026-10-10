# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Access protection: one local password, session cookies, host and origin checks.

Viewing data in the home network needs no login. Everything that changes something (settings,
inverter control, imports) and the database backup requires a session.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import os
import secrets
import threading
import time

from .storage import Storage

SESSION_COOKIE = "openampere_session"
CSRF_HEADER = "x-openampere"  # custom header: browsers cannot send it cross-site without a CORS preflight
SESSION_TTL_S = 90 * 24 * 3600
MIN_PASSWORD_LENGTH = 6
MAX_FAILED_LOGINS = 5  # per device and minute: one device trying passwords does not lock out the others
MAX_FAILED_TOTAL = 30  # all devices together per minute: many addresses do not make guessing faster
LOCKOUT_S = 60

# Host names that are typical for home networks. Anything else (e.g. evil.example resolving to a LAN IP)
# is rejected to defeat DNS rebinding. Users can allow more names via server.allowed_hosts.
# Only names nobody can register publicly: ".box" is a public top-level domain, so only ".fritz.box" (#163).
HOME_SUFFIXES = (".local", ".lan", ".home", ".home.arpa", ".fritz.box", ".internal", ".intranet",
                 ".localdomain", ".ts.net")


def _hash(password: str, salt: bytes) -> str:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2 ** 14, r=8, p=1, dklen=32).hex()


def _token_id(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def host_allowed(host_header: str | None, extra: list[str]) -> bool:
    if not host_header:
        return False
    if "*" in extra:
        return True
    host = host_header.strip().lower()
    if host.startswith("["):  # IPv6 literal with port
        host = host[1:host.find("]")]
    elif host.count(":") == 1:
        host = host.split(":", 1)[0]
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    if host == "localhost" or "." not in host or host.endswith(HOME_SUFFIXES):
        return True
    return host in {h.strip().lower() for h in extra}


def _client_key(client: str | None) -> str:
    """One device: its IPv4 address, or the /64 network of an IPv6 address (a device has many of those)."""
    try:
        address = ipaddress.ip_address(client or "")
    except ValueError:
        return client or "?"
    if address.version == 6 and not address.ipv4_mapped:
        return str(ipaddress.ip_network(f"{address}/64", strict=False))
    return str(address.ipv4_mapped or address) if address.version == 6 else str(address)


class Auth:
    def __init__(self, storage: Storage) -> None:
        self.storage = storage
        self._tries: dict[str, list[float]] = {}  # device -> times of failed (or still running) checks
        self._guard = threading.Lock()  # logins run in parallel threads
        self._sessions_lock = threading.Lock()

    # ---- password ----------------------------------------------------------

    @property
    def configured(self) -> bool:
        return bool(self.storage.get_meta("auth"))

    def set_password(self, password: str) -> None:
        if len(password) < MIN_PASSWORD_LENGTH:
            raise ValueError(f"Das Passwort muss mindestens {MIN_PASSWORD_LENGTH} Zeichen haben.")
        salt = os.urandom(16)
        self.storage.set_meta("auth", {"salt": salt.hex(), "hash": _hash(password, salt)})

    def check_password(self, password: str, client: str | None = None) -> bool:
        key, now = _client_key(client), time.monotonic()
        with self._guard:
            self._tries = {k: kept for k, v in self._tries.items() if (kept := [t for t in v if now - t < LOCKOUT_S])}
            if (len(self._tries.get(key, ())) >= MAX_FAILED_LOGINS
                    or sum(map(len, self._tries.values())) >= MAX_FAILED_TOTAL):
                raise PermissionError("Zu viele Fehlversuche. Bitte eine Minute warten.")
            # counted before the slow check: requests at the same time cannot get past the limit
            self._tries.setdefault(key, []).append(now)
        stored = self.storage.get_meta("auth")
        ok = bool(stored) and hmac.compare_digest(_hash(password, bytes.fromhex(stored["salt"])), stored["hash"])
        if ok:
            with self._guard:
                self._tries.pop(key, None)
        return ok

    def reset(self) -> None:
        """Forget the password and all sessions (command line `openampere reset-password`)."""
        self.storage.set_meta("auth", None)
        self.storage.set_meta("sessions", {})

    # ---- sessions -----------------------------------------------------------

    def _sessions(self) -> dict:
        now = time.time()
        return {k: v for k, v in (self.storage.get_meta("sessions") or {}).items() if v > now}

    def create_session(self) -> str:
        token = secrets.token_urlsafe(32)
        with self._sessions_lock:
            sessions = self._sessions()
            sessions[_token_id(token)] = time.time() + SESSION_TTL_S
            self.storage.set_meta("sessions", sessions)
        return token

    def valid(self, token: str | None) -> bool:
        return bool(token) and _token_id(token) in self._sessions()

    def revoke(self, token: str | None, *, everywhere: bool = False) -> None:
        with self._sessions_lock:
            sessions = {} if everywhere else {k: v for k, v in self._sessions().items()
                                              if not token or k != _token_id(token)}
            self.storage.set_meta("sessions", sessions)
