# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Access tokens for other apps, e.g. the Home Assistant integration (#76).

A token is shown once when it is created; OpenAmpere only keeps its SHA-256 hash (it has 256 random bits, so a
plain hash is enough). Tokens work only for the external API (/api/external/v1/...) and only over HTTPS, never
for the web app. "read" tokens see the data, "control" tokens may also use the commands released for other apps.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time

from .storage import Storage

SCOPES = ("read", "control")
MAX_TOKENS = 10
PREFIX = "oa_"
CODE_PREFIX = "openampere1:"
LAST_USED_EVERY_S = 60  # "last used" is stored at most this often (the live connection uses the token constantly)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def connection_code(host: str, port: int, fingerprint: str, token: str) -> str:
    """Everything an app needs in one string: address, HTTPS port, certificate fingerprint and token."""
    data = json.dumps({"host": host, "port": port, "fingerprint": fingerprint, "token": token}, separators=(",", ":"))
    return CODE_PREFIX + base64.urlsafe_b64encode(data.encode()).decode().rstrip("=")


class ApiTokens:
    def __init__(self, storage: Storage) -> None:
        self.storage = storage
        # the check runs in the server's event loop, create and revoke in worker threads: without the lock a check
        # could write back the list it read before a revoke, and the revoked token would be valid again
        self._lock = threading.Lock()

    def _all(self) -> dict:
        return self.storage.get_meta("api_tokens") or {}

    def list(self) -> list[dict]:
        return [{"id": key, "name": t["name"], "scope": t["scope"], "created": t["created"],
                 "last_used": t.get("last_used")} for key, t in sorted(self._all().items(), key=lambda i: i[1]["created"])]

    def create(self, name: str, scope: str) -> tuple[str, dict]:
        name = " ".join(str(name or "").split())[:40]
        if not name:
            raise ValueError("Bitte einen Namen angeben, z. B. „Home Assistant“.")
        if scope not in SCOPES:
            raise ValueError("Unbekannte Berechtigung.")
        with self._lock:
            tokens = self._all()
            if len(tokens) >= MAX_TOKENS:
                raise ValueError(f"Höchstens {MAX_TOKENS} Zugänge. Bitte zuerst einen nicht mehr genutzten entfernen.")
            token = PREFIX + secrets.token_urlsafe(32)
            key = secrets.token_hex(4)
            tokens[key] = {"name": name, "scope": scope, "hash": _hash(token), "created": time.time(), "last_used": None}
            self.storage.set_meta("api_tokens", tokens)
        return token, next(t for t in self.list() if t["id"] == key)

    def revoke(self, key: str) -> None:
        with self._lock:
            tokens = self._all()
            if key not in tokens:
                raise KeyError(key)
            del tokens[key]
            self.storage.set_meta("api_tokens", tokens)

    def check(self, token: str | None) -> dict | None:
        """The token's entry ({"id", "name", "scope"}) if it is valid, otherwise None."""
        if not token or not token.startswith(PREFIX):
            return None
        digest = _hash(token)
        with self._lock:
            tokens = self._all()
            for key, entry in tokens.items():
                if secrets.compare_digest(entry["hash"], digest):
                    now = time.time()
                    if not entry.get("last_used") or now - entry["last_used"] > LAST_USED_EVERY_S:
                        entry["last_used"] = now
                        self.storage.set_meta("api_tokens", tokens)
                    return {"id": key, "name": entry["name"], "scope": entry["scope"]}
        return None


# ---- pairing: connect an app by comparing a code, without copying a secret -----------------------------------------

PAIRING_TTL_S = 600
MAX_PENDING = 3
PAIRING_STARTS_PER_HOUR = 10


def pairing_code(fingerprint: str, app_nonce: bytes, server_nonce: bytes) -> str:
    """Six digits both sides show: from the certificate each side sees and both random numbers.

    Someone in between sees a different certificate than the app, so the codes differ. The app commits to its
    number before it learns the server's, so nobody in between can search for numbers that make the codes match
    (the same idea as numeric comparison when pairing Bluetooth devices). The app has its own copy of this function.
    """
    digest = hashlib.sha256(bytes.fromhex(fingerprint) + app_nonce + server_nonce).digest()
    return f"{int.from_bytes(digest[:4], 'big') % 1_000_000:06d}"


class Pairing:
    """Pairing requests of apps, in memory: a restart drops them (they are short-lived anyway)."""

    def __init__(self, tokens: ApiTokens, fingerprint) -> None:
        self.tokens = tokens
        self.fingerprint = fingerprint  # () -> fingerprint of the own certificate
        self._requests: dict[str, dict] = {}
        self._starts: list[float] = []

    def _expire(self, now: float) -> None:
        for key in [k for k, r in self._requests.items() if r["expires"] < now]:
            del self._requests[key]

    def start(self, name: str, commitment: str, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        self._expire(now)
        self._starts = [t for t in self._starts if t > now - 3600]
        if len(self._starts) >= PAIRING_STARTS_PER_HOUR:
            raise PermissionError("Zu viele Kopplungsanfragen. Bitte später erneut versuchen.")
        if len(self._requests) >= MAX_PENDING:
            raise PermissionError("Es warten schon mehrere Kopplungsanfragen. Bitte zuerst in OpenAmpere bestätigen "
                                  "oder ablehnen, oder 10 Minuten warten.")
        name = " ".join(str(name or "").split())[:40] or "App"
        if len(str(commitment)) != 64:
            raise ValueError("Ungültige Anfrage.")
        bytes.fromhex(commitment)  # raises ValueError
        self._starts.append(now)
        key, poll_key, nonce = secrets.token_hex(6), secrets.token_urlsafe(32), secrets.token_bytes(32)
        self._requests[key] = {"name": name, "commitment": commitment.lower(), "poll_key": poll_key, "nonce": nonce,
                               "code": None, "created": now, "expires": now + PAIRING_TTL_S, "status": "pending",
                               "token": None, "scope": None}
        return {"request_id": key, "poll_key": poll_key, "nonce": nonce.hex(), "expires_in": PAIRING_TTL_S}

    def _get(self, key: str, poll_key: str) -> dict:
        self._expire(time.time())
        request = self._requests.get(key)
        if request is None or not secrets.compare_digest(request["poll_key"], str(poll_key or "")):
            raise KeyError(key)
        return request

    def reveal(self, key: str, poll_key: str, app_nonce: str) -> None:
        """The app's random number: must match what it committed to. Only now the code exists."""
        request = self._get(key, poll_key)
        nonce = bytes.fromhex(str(app_nonce))
        if request["code"] is not None or len(nonce) != 32 or \
                not secrets.compare_digest(hashlib.sha256(nonce).hexdigest(), request["commitment"]):
            del self._requests[key]
            raise ValueError("Ungültige Anfrage.")
        request["code"] = pairing_code(self.fingerprint(), nonce, request["nonce"])

    def status(self, key: str, poll_key: str) -> dict:
        """pending, approved (with the token, handed out once) or rejected."""
        request = self._get(key, poll_key)
        if request["status"] == "pending":
            return {"status": "pending"}
        del self._requests[key]
        if request["status"] == "approved":
            return {"status": "approved", "token": request["token"], "scope": request["scope"]}
        return {"status": "rejected"}

    def pending(self) -> list[dict]:
        """For the web app: requests waiting for a decision (only those with a code)."""
        self._expire(time.time())
        return [{"id": k, "name": r["name"], "code": r["code"], "created": r["created"], "expires": r["expires"]}
                for k, r in self._requests.items() if r["status"] == "pending" and r["code"]]

    def decide(self, key: str, approve: bool, scope: str = "read") -> dict:
        self._expire(time.time())
        request = self._requests.get(key)
        if request is None or request["status"] != "pending" or not request["code"]:
            raise KeyError(key)
        if not approve:
            request["status"] = "rejected"
            return {"name": request["name"]}
        token, entry = self.tokens.create(request["name"], scope)
        request.update(status="approved", token=token, scope=scope)
        return entry
