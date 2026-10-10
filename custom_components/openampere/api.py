# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Client for the OpenAmpere server: /api/external/v1 over HTTPS with a pinned certificate."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import logging
import ssl
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import aiohttp

from .const import CODE_PREFIX

_LOGGER = logging.getLogger(__name__)
TIMEOUT = aiohttp.ClientTimeout(total=20)
RECONNECT_S = (5, 10, 30, 60)


class OpenAmpereError(Exception):
    """Base error; the message is the server's German explanation where there is one."""


class CannotConnect(OpenAmpereError):
    pass


class InvalidAuth(OpenAmpereError):
    pass


class CertificateMismatch(OpenAmpereError):
    """The server shows a different certificate than the one in the connection code."""


@dataclass
class Connection:
    host: str
    port: int
    fingerprint: str  # SHA-256 of the server certificate, hex
    token: str


def pairing_code(fingerprint: str, app_nonce: bytes, server_nonce: bytes) -> str:
    """Six digits, computed the same way as in OpenAmpere (apitokens.pairing_code): if someone sits in between,
    this side sees a different certificate and the codes differ."""
    digest = hashlib.sha256(bytes.fromhex(fingerprint) + app_nonce + server_nonce).digest()
    return f"{int.from_bytes(digest[:4], 'big') % 1_000_000:06d}"


async def fetch_fingerprint(host: str, port: int) -> str:
    """SHA-256 of the certificate the server shows (first contact when pairing; checked by comparing the code)."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)  # no CA store: nothing blocking to load
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        _reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port, ssl=context), 10)
    except (OSError, asyncio.TimeoutError, ssl.SSLError) as err:
        raise CannotConnect(str(err) or "keine Verbindung") from err
    try:
        der = writer.get_extra_info("ssl_object").getpeercert(binary_form=True)
    finally:
        writer.close()
    return hashlib.sha256(der).hexdigest()


def parse_code(code: str) -> Connection:
    """The connection code from OpenAmpere (Mehr → Verbundene Apps); raises ValueError."""
    code = "".join(code.split())
    if not code.startswith(CODE_PREFIX):
        raise ValueError("not an OpenAmpere connection code")
    raw = code[len(CODE_PREFIX):]
    try:
        data = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
        connection = Connection(str(data["host"]), int(data["port"]), str(data["fingerprint"]).lower(),
                                str(data["token"]))
        if len(bytes.fromhex(connection.fingerprint)) != 32 or not connection.token:
            raise ValueError
    except (binascii.Error, KeyError, TypeError, ValueError, json.JSONDecodeError) as err:
        raise ValueError("damaged connection code") from err
    return connection


class OpenAmpereClient:
    def __init__(self, session: aiohttp.ClientSession, connection: Connection) -> None:
        self._session = session
        self.connection = connection
        # pinning: no certificate authority, no host name check, but exactly this certificate
        self._ssl = aiohttp.Fingerprint(bytes.fromhex(connection.fingerprint))

    @property
    def _base(self) -> str:
        host = self.connection.host
        if ":" in host and not host.startswith("["):  # IPv6 literal
            host = f"[{host}]"
        return f"https://{host}:{self.connection.port}/api/external/v1"

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.connection.token}"} if self.connection.token else {}

    async def _request(self, method: str, path: str, body: dict | None = None) -> Any:
        try:
            async with self._session.request(method, self._base + path, json=body, headers=self._headers,
                                             ssl=self._ssl, timeout=TIMEOUT) as response:
                try:
                    data = await response.json(content_type=None)
                except (aiohttp.ContentTypeError, json.JSONDecodeError):
                    data = {}
                if response.status == 401:
                    raise InvalidAuth(data.get("detail") or "Zugang ungültig")
                if response.status >= 400:
                    raise OpenAmpereError(data.get("detail") or f"Fehler {response.status}")
                return data
        except aiohttp.ServerFingerprintMismatch as err:
            raise CertificateMismatch("Zertifikat passt nicht zum Verbindungscode") from err
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise CannotConnect(str(err) or "keine Verbindung") from err

    async def pair_start(self, name: str, commitment: str) -> dict:
        return await self._request("POST", "/pair/start", {"name": name, "commitment": commitment})

    async def pair_reveal(self, request_id: str, poll_key: str, nonce: str) -> dict:
        return await self._request("POST", f"/pair/{request_id}/reveal", {"poll_key": poll_key, "nonce": nonce})

    async def pair_status(self, request_id: str, poll_key: str) -> dict:
        return await self._request("POST", f"/pair/{request_id}/status", {"poll_key": poll_key})

    async def info(self) -> dict:
        return await self._request("GET", "/info")

    async def state(self) -> dict:
        return await self._request("GET", "/state")

    async def set_battery(self, changes: dict) -> dict:
        return await self._request("PUT", "/battery", changes)

    async def set_grid_charging(self, changes: dict) -> dict:
        return await self._request("PUT", "/grid-charging", changes)

    async def set_device_mode(self, device_id: str, mode: str) -> dict:
        return await self._request("PUT", f"/devices/{device_id}/mode", {"mode": mode})

    async def listen(self, on_message: Callable[[dict], None], on_connection: Callable[[bool], Awaitable[None] | None]
                     | None = None) -> None:
        """Live values by push until cancelled; reconnects with a growing pause."""
        attempt = 0
        while True:
            try:
                async with self._session.ws_connect(self._base + "/ws", headers=self._headers, ssl=self._ssl,
                                                    heartbeat=30, timeout=aiohttp.ClientWSTimeout(ws_close=10)) as ws:
                    attempt = 0
                    if on_connection:
                        await _maybe_await(on_connection(True))
                    async for message in ws:
                        if message.type == aiohttp.WSMsgType.TEXT:
                            on_message(json.loads(message.data))
                        elif message.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - network, certificate, server restart: try again later
                _LOGGER.debug("OpenAmpere live connection: %s", err)
            if on_connection:
                await _maybe_await(on_connection(False))
            await asyncio.sleep(RECONNECT_S[min(attempt, len(RECONNECT_S) - 1)])
            attempt += 1


async def _maybe_await(value: Awaitable[None] | None) -> None:
    if value is not None:
        await value
