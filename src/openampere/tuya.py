# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""The local protocol of Tuya devices, version 3.5 (TCP port 6668), e.g. a portable wallbox sold as "dé".

Tuya devices are set up in the Tuya or Smart Life app and then also answer in the home network, encrypted with a
"local key" of 16 characters per device. The owner gets the key once through Tuya's developer platform; after that
no cloud is needed. The key changes when the device is paired again in the app.

How a connection works (facts of the protocol as described by the tinytuya project, MIT; no code copied):
- Every frame: prefix 00 00 66 99, two reserved bytes, sequence number, command and length (each 4 bytes,
  big-endian), then a 12-byte nonce, the AES-128-GCM ciphertext and its 16-byte tag, suffix 00 00 99 66. The
  14 header bytes after the prefix are the additional data of the GCM tag.
- Session key: the client sends a 16-byte nonce (command 3) encrypted with the local key, the device answers with
  its own nonce and an HMAC-SHA256 of the client's nonce (command 4), the client confirms with an HMAC of the device's
  nonce (command 5). The session key is the XOR of both nonces, encrypted with AES-GCM under the local key with the
  first 12 bytes of the client's nonce as nonce (the ciphertext without the tag).
- Answers of the device start with a 4-byte return code. Commands other than the query (16), the heartbeat (9) and
  the key exchange carry "3.5" and 12 zero bytes before their JSON.

OpenAmpere opens one connection per request and closes it right away: a Tuya device accepts only one local
connection at a time, so holding it would lock out the owner's other tools.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import ipaddress
import json
import os
import socket
import struct
import sys
import time

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

PORT = 6668
DISCOVERY_PORTS = (6667, 7000)  # devices with protocol 3.3 and newer announce themselves here (UDP broadcast)
LIMITED_BROADCAST = "255.255.255.255"
SIOCGIFADDR, SIOCGIFBRDADDR = 0x8915, 0x8919  # Linux: address and broadcast address of a network card
VERSION = b"3.5"
VERSION_HEADER = VERSION + bytes(12)
PREFIX = b"\x00\x00\x66\x99"
SUFFIX = b"\x00\x00\x99\x66"
HEADER = struct.Struct(">4sHIII")  # prefix, reserved, sequence number, command, length
PREFIX_55AA = b"\x00\x00\x55\xaa"  # the older format, still used for announcements
HEADER_55AA = struct.Struct(">4sIII")  # prefix, sequence number, command, length (from here to the end)
NONCE_BYTES = 12
TAG_BYTES = 16
MAX_LENGTH = 8192  # a status answer is well below 1 KB
TIMEOUT_S = 5.0

KEY_START, KEY_ANSWER, KEY_FINISH = 3, 4, 5
STATUS = 8  # the device reports changed values
HEARTBEAT = 9
CONTROL = 13
QUERY = 16
REQUEST_INFO = 0x25  # asks devices to announce themselves
REQUEST_PORT = 7000
ASK_EVERY_S = 3.0
WITHOUT_VERSION_HEADER = frozenset({KEY_START, KEY_ANSWER, KEY_FINISH, HEARTBEAT, QUERY})
# the fixed key Tuya devices use for their UDP announcements (public in every Tuya library)
DISCOVERY_KEY = hashlib.md5(b"yGAdlopoPVldABfn").digest()


class TuyaError(Exception):
    """A problem with a Tuya device; .message is a German text for the app."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class TuyaUnreachable(TuyaError):
    """No connection, or the connection broke off (also when another tool holds the device's only connection)."""


class TuyaRefused(TuyaUnreachable):
    """The device closed the connection while logging in: a wrong local key or another tool holds its connection."""


class TuyaKeyRejected(TuyaError):
    """The device answered, but not with the local key OpenAmpere has."""


def key_bytes(local_key: str) -> bytes:
    key = local_key.encode("utf-8")
    if len(key) != 16:
        raise TuyaError("Der Local Key muss genau 16 Zeichen lang sein.")
    return key


def pack(key: bytes, sequence: int, command: int, payload: bytes, nonce: bytes | None = None) -> bytes:
    nonce = nonce or os.urandom(NONCE_BYTES)
    header = HEADER.pack(PREFIX, 0, sequence, command, NONCE_BYTES + len(payload) + TAG_BYTES)
    return header + nonce + AESGCM(key).encrypt(nonce, payload, header[4:]) + SUFFIX


def unpack(key: bytes, frame: bytes) -> tuple[int, int, bytes]:
    """(sequence number, command, decrypted payload); TuyaKeyRejected if the tag does not match the key."""
    _, _, sequence, command, length = HEADER.unpack_from(frame)
    body = frame[HEADER.size:HEADER.size + length]
    try:
        payload = AESGCM(key).decrypt(body[:NONCE_BYTES], body[NONCE_BYTES:], frame[4:HEADER.size])
    except InvalidTag:
        raise TuyaKeyRejected("Das Gerät antwortet, aber der Local Key passt nicht. Er ändert sich, wenn das Gerät "
                              "in der App neu verbunden wird; bitte den aktuellen Local Key eintragen.") from None
    return sequence, command, payload


async def read_frame(reader: asyncio.StreamReader) -> bytes:
    header = await reader.readexactly(HEADER.size)
    prefix, _, _, _, length = HEADER.unpack(header)
    if prefix != PREFIX or not NONCE_BYTES + TAG_BYTES <= length <= MAX_LENGTH:
        raise TuyaError("Das Gerät antwortet nicht im Tuya-Protokoll 3.5.")
    rest = await reader.readexactly(length + len(SUFFIX))
    if rest[-len(SUFFIX):] != SUFFIX:
        raise TuyaError("Das Gerät antwortet nicht im Tuya-Protokoll 3.5.")
    return header + rest


def session_key(local_key: bytes, own_nonce: bytes, device_nonce: bytes) -> bytes:
    mixed = bytes(a ^ b for a, b in zip(own_nonce, device_nonce, strict=True))
    return AESGCM(local_key).encrypt(own_nonce[:NONCE_BYTES], mixed, None)[:16]


def decode_json(payload: bytes) -> dict | None:
    """The JSON of an answer (return code and version header removed); None for an empty acknowledgement."""
    if payload.startswith(VERSION):
        payload = payload[len(VERSION_HEADER):]
    payload = payload.strip(b"\x00")
    if not payload:
        return None
    try:
        found = json.loads(payload)
    except (ValueError, UnicodeDecodeError):
        raise TuyaError("Das Gerät hat eine unlesbare Antwort geschickt.") from None
    return found if isinstance(found, dict) else None


def dps_of(answer: dict | None) -> dict[str, object] | None:
    """The data points of an answer: {"dps": …} or {"data": {"dps": …}}."""
    if not answer:
        return None
    dps = answer.get("dps")
    if not isinstance(dps, dict) and isinstance(answer.get("data"), dict):
        dps = answer["data"].get("dps")
    return {str(k): v for k, v in dps.items()} if isinstance(dps, dict) else None


class Connection:
    """One connection with a negotiated session key; use through TuyaDevice."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, local_key: bytes) -> None:
        self.reader, self.writer = reader, writer
        self.local_key = self.key = local_key
        self.sequence = 0

    async def send(self, command: int, payload: bytes) -> None:
        self.sequence += 1
        if command not in WITHOUT_VERSION_HEADER:
            payload = VERSION_HEADER + payload
        self.writer.write(pack(self.key, self.sequence, command, payload))
        await self.writer.drain()

    async def receive(self) -> tuple[int, bytes]:
        """(command, payload without the return code)."""
        _, command, payload = unpack(self.key, await read_frame(self.reader))
        return command, payload[4:]

    async def negotiate(self) -> None:
        own = os.urandom(16)
        await self.send(KEY_START, own)
        command, payload = await self.receive()
        expected = hmac.new(self.local_key, own, hashlib.sha256).digest()
        if command != KEY_ANSWER or len(payload) < 48 or not hmac.compare_digest(payload[16:48], expected):
            raise TuyaKeyRejected("Das Gerät hat den Local Key nicht bestätigt. Bitte den aktuellen Local Key "
                                  "eintragen.")
        device = payload[:16]
        await self.send(KEY_FINISH, hmac.new(self.local_key, device, hashlib.sha256).digest())
        self.key = session_key(self.local_key, own, device)


class TuyaDevice:
    """A Tuya device in the home network, protocol 3.5. Every call opens and closes its own connection."""

    def __init__(self, host: str, local_key: str, port: int = PORT, timeout: float = TIMEOUT_S) -> None:
        self.host, self.port, self.timeout = host, port, timeout
        self.local_key = key_bytes(local_key)

    @contextlib.asynccontextmanager
    async def _connection(self):
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(self.host, self.port), self.timeout)
        except (OSError, asyncio.TimeoutError):
            raise TuyaUnreachable(f"Das Gerät ist unter {self.host} nicht erreichbar.") from None
        try:
            connection = Connection(reader, writer, self.local_key)
            try:
                await connection.negotiate()
            except (asyncio.IncompleteReadError, ConnectionError, OSError):
                # a device closes the connection both for a wrong key and while another tool is connected
                raise TuyaRefused("Das Gerät hat die Anmeldung abgebrochen. Meist passt der Local Key nicht (er ändert "
                                  "sich, wenn das Gerät in der App neu verbunden wird), oder ein anderes Programm ist "
                                  "gerade verbunden; Tuya-Geräte erlauben nur eine Verbindung.") from None
            yield connection
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            raise TuyaUnreachable("Das Gerät hat die Verbindung beendet. Vielleicht ist es gerade mit einem anderen "
                                  "Programm verbunden; Tuya-Geräte erlauben nur eine Verbindung.") from None
        finally:
            writer.close()
            with contextlib.suppress(OSError, asyncio.TimeoutError):
                await asyncio.wait_for(writer.wait_closed(), 1)

    async def _until_dps(self, connection: Connection, commands: tuple[int, ...]) -> dict[str, object]:
        """Reads answers until one carries data points; acknowledgements without any are skipped."""
        while True:
            command, payload = await connection.receive()
            if command in commands:
                dps = dps_of(decode_json(payload))
                if dps is not None:
                    return dps

    async def status(self) -> dict[str, object]:
        """All data points, e.g. {"109": "WORKING", "150": 8, …}."""
        async def ask():
            async with self._connection() as connection:
                await connection.send(QUERY, b"{}")
                return await self._until_dps(connection, (QUERY, STATUS))
        return await self._within(ask())

    async def set(self, values: dict[str, object], *, read_back: bool = False) -> dict[str, object]:
        """Writes data points; returns the values the device reports back ({} if it only acknowledges), or with
        read_back all data points, asked over the same connection (a second one right after may be refused while
        the device still closes the first)."""
        body = json.dumps({"protocol": 5, "t": int(time.time()), "data": {"dps": values}}, separators=(",", ":"))

        async def write():
            async with self._connection() as connection:
                await connection.send(CONTROL, body.encode())
                try:
                    echo = await asyncio.wait_for(self._until_dps(connection, (STATUS, CONTROL)), self.timeout / 2)
                except asyncio.TimeoutError:
                    echo = {}  # some data points are only acknowledged
                if not read_back:
                    return echo
                await connection.send(QUERY, b"{}")
                return await self._until_dps(connection, (QUERY,))
        return await self._within(write())

    async def _within(self, work):
        try:
            return await asyncio.wait_for(work, self.timeout * 2)
        except asyncio.TimeoutError:
            raise TuyaUnreachable(f"Das Gerät unter {self.host} antwortet nicht.") from None


def decode_announcement(data: bytes) -> dict | None:
    """A UDP announcement of a Tuya device, e.g. {"ip", "gwId", "version", "productKey"}. Devices use two formats: the
    one of protocol 3.5 (prefix 6699, AES-GCM) and the older one (prefix 55AA, a return code, then the JSON encrypted
    with AES-ECB, or plain), which also devices with protocol 3.5 send."""
    try:
        if data.startswith(PREFIX):
            if len(data) < HEADER.size + NONCE_BYTES + TAG_BYTES + len(SUFFIX):
                return None
            _, _, payload = unpack(DISCOVERY_KEY, data)
            payload = payload[4:] if payload[:1] != b"{" else payload
        elif data.startswith(PREFIX_55AA):
            _, _, _, length = HEADER_55AA.unpack_from(data)
            if not 12 <= length <= MAX_LENGTH or len(data) < HEADER_55AA.size + length:
                return None
            # return code, payload, CRC and suffix
            payload = data[HEADER_55AA.size + 4:HEADER_55AA.size + length - 8]
            if payload[:1] != b"{":
                payload = _ecb_decrypt(DISCOVERY_KEY, payload)
        else:
            return None
        found = json.loads(payload.rstrip(b"\x00"))
    except (TuyaError, ValueError, UnicodeDecodeError, struct.error):
        return None
    return found if isinstance(found, dict) and found.get("gwId") else None


def _ecb_decrypt(key: bytes, data: bytes) -> bytes:
    if not data or len(data) % 16:
        raise ValueError("not a whole number of blocks")
    decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()  # noqa: S305 - the format of the announcement
    padded = decryptor.update(data) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    return unpadder.update(padded) + unpadder.finalize()


def discovery_request(own: str) -> bytes:
    """Asks devices with protocol 3.5 in the network to announce themselves (some only answer such a request)."""
    return pack(DISCOVERY_KEY, 0, REQUEST_INFO, json.dumps({"from": "app", "ip": own}).encode())


def announced(data: bytes, source: str) -> dict | None:
    """A device found by its announcement, only if the announcement comes from the address it names and that address
    is in the home network: anybody who can send a datagram could otherwise move a stored wallbox to another
    address."""
    info = decode_announcement(data)
    if not info or str(info.get("version")) != VERSION.decode() or str(info.get("ip") or source) != source:
        return None
    try:
        if ipaddress.ip_address(source).is_global:
            return None
    except ValueError:
        return None
    return {"device_id": str(info["gwId"]), "host": source, "product_key": str(info.get("productKey") or "") or None}


def _local_address() -> str | None:
    """The address of the network card the home network is reached over (no packet is sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.168.0.1", 9))
            return sock.getsockname()[0]
    except OSError:
        return None


def interfaces() -> list[tuple[str, str]]:
    """(own address, broadcast address) of every network card in the home network: on Linux (Docker, Home Assistant)
    read from the cards, elsewhere the card towards the home network with 255.255.255.255."""
    found: list[tuple[str, str]] = []
    if sys.platform.startswith("linux"):
        import fcntl
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            for _, name in socket.if_nameindex():
                request = struct.pack("256s", name.encode()[:15])
                try:
                    address = socket.inet_ntoa(fcntl.ioctl(sock.fileno(), SIOCGIFADDR, request)[20:24])
                    broadcast = socket.inet_ntoa(fcntl.ioctl(sock.fileno(), SIOCGIFBRDADDR, request)[20:24])
                except OSError:
                    continue  # no IPv4 address or no broadcast (loopback, Tailscale, VPN)
                found.append((address, broadcast))
    else:
        address = _local_address()
        found = [(address, LIMITED_BROADCAST)] if address else []
    def usable(address: str, broadcast: str) -> bool:
        try:
            own = ipaddress.ip_address(address)
        except ValueError:
            return False
        return own.is_private and not own.is_loopback and broadcast != "0.0.0.0"
    return [(a, b) for a, b in found if usable(a, b)]


def listen_addresses(cards: list[tuple[str, str]]) -> list[str]:
    """Where announcements and answers arrive: 255.255.255.255, the broadcast address of each card and the card's own
    address (a device may answer the request directly). Never all interfaces."""
    found = [LIMITED_BROADCAST]
    for own, broadcast in cards:
        found += [a for a in (broadcast, own) if a not in found]
    return found


async def search(seconds: float = 8.0, ports: tuple[int, ...] = DISCOVERY_PORTS,
                 addresses: tuple[str, ...] | None = None, ask: bool = True,
                 cards: list[tuple[str, str]] | None = None) -> list[dict]:
    """Tuya devices with protocol 3.5 in the home network, found within `seconds`.

    They announce themselves every few seconds on UDP 6667 or 7000; some only after a request, which is sent to the
    broadcast address of each card every few seconds (ask). Only devices with protocol 3.5 are kept, and only from the
    address they name (announced). The sockets are bound to the broadcast addresses and the card's own address in the
    home network, never to all interfaces. A port that is taken (another Tuya tool) is skipped; OSError if none could
    be opened. Only with a network of the host: in the Home Assistant app (its own network) the announcements do not
    arrive, the callers skip the search there. addresses and cards replace what is read from the network cards
    (tests).
    """
    found: dict[str, dict] = {}
    loop = asyncio.get_running_loop()
    cards = interfaces() if cards is None else cards

    class Listener(asyncio.DatagramProtocol):
        def datagram_received(self, data, addr):
            device = announced(data, addr[0])
            if device:
                found[device["device_id"]] = device

    transports, senders = [], []
    try:
        for address in addresses or listen_addresses(cards):
            for port in ports:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    sock.bind((address, port))
                except OSError:
                    sock.close()
                    continue
                transport, _ = await loop.create_datagram_endpoint(Listener, sock=sock)
                transports.append(transport)
        if not transports:
            raise OSError("no port for the search")
        if ask:
            for own, broadcast in cards:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                try:
                    sock.bind((own, 0))
                except OSError:
                    sock.close()
                    continue
                senders.append((sock, discovery_request(own), broadcast))
        deadline = loop.time() + seconds
        while (left := deadline - loop.time()) > 0:
            for sock, request, broadcast in senders:
                with contextlib.suppress(OSError):
                    sock.sendto(request, (broadcast, REQUEST_PORT))
            await asyncio.sleep(min(ASK_EVERY_S, left))
    finally:
        for transport in transports:
            transport.close()
        for sock, _, _ in senders:
            sock.close()
    return sorted(found.values(), key=lambda d: d["host"])
