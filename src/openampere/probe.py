# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Read any Modbus TCP device in the home network and identify it, read-only (#324).

The base for supporting new devices (wallboxes, inverters OpenAmpere does not know yet): an owner reads registers of
the device and shares the result in the diagnostics report, so register addresses can be confirmed on a real device.

Only the function codes 3 and 4 are ever sent; there is no code path for writing here. The limits keep it from being
a network scanner and from overloading a device: only addresses in the home network, one host at a time, small
blocks, one connection, pauses between requests. Some devices accept a single Modbus client only: a wallbox that evcc
reads may refuse the connection or drop evcc's for a moment.
"""

from __future__ import annotations

import asyncio
import html
import ipaddress
import json
import re
import socket
import ssl
import struct
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field

from pymodbus.client import AsyncModbusTcpClient
from pymodbus.exceptions import ModbusException

from . import ha_app, privacy
from .drivers.modbus import ModbusReadError
from .outbound import in_home_network, open_without_redirect
from .runtime import Runtime

MAX_BLOCK = 60  # registers per request
MAX_REGISTERS = 600  # per run, at most 10 requests
REQUEST_GAP_S = 0.5
CONNECT_SETTLE_S = 0.5  # a device whose connections are all taken closes a new one right away
TIMEOUT_S = 3.0
PORTS = (502, 1502, 80, 443, 8080)  # checked to identify a device: Modbus TCP (two usual ports) and a web page
WEB_PORTS = ((80, "http"), (8080, "http"), (443, "https"))
ARP = "/proc/net/arp"
TITLE_BYTES = 64 * 1024
MDNS = ("224.0.0.251", 5353)
MDNS_SERVICES = ("_modbus._tcp.local",)
SEARCH_S = 2.0
HOST = re.compile(r"[A-Za-z0-9.-]{1,253}|\[?[0-9A-Fa-f:.]{2,45}\]?")


class ProbeError(Exception):
    """.message is a German text for the app."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ProbeUnreachable(ProbeError):
    """Nothing accepts a connection at host:port (another port may work)."""


class ProbeBusy(ProbeError):
    """The device closed the connection right away: all its connections are taken."""


@dataclass
class Block:
    start: int
    count: int
    ok: bool = False
    words: list[int] | None = None
    error: str | None = None  # "rejected" (exception 1–3) | "no_answer" (timeout, exception 4–11)
    exception: int | None = None  # Modbus exception code of the device's answer
    ms: int | None = None


@dataclass
class ReadResult:
    host: str
    port: int
    unit: int
    function: int
    blocks: list[Block] = field(default_factory=list)
    shared_connection: bool = False  # read over OpenAmpere's connection to the inverter


def blocks_of(start: int, count: int) -> list[tuple[int, int]]:
    return [(a, min(MAX_BLOCK, start + count - a)) for a in range(start, start + count, MAX_BLOCK)]


def _valid_host(host: str) -> str:
    host = host.strip()
    if not HOST.fullmatch(host):
        raise ProbeError("Bitte eine Adresse im Heimnetz angeben.")
    return host.strip("[]")


async def _check_home(host: str) -> str:
    host = _valid_host(host)
    if not await asyncio.to_thread(in_home_network, host):
        raise ProbeError("Bitte eine Adresse im Heimnetz angeben.")
    return host


def _addresses(host: str) -> set[str]:
    try:
        return {info[4][0].split("%", 1)[0] for info in socket.getaddrinfo(host, None)}
    except (socket.gaierror, UnicodeError):
        return {host}


class Probe:
    """One run at a time; the last results go into the next diagnostics report."""

    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime
        self._lock = asyncio.Lock()
        self.last_read: ReadResult | None = None
        self.last_identify: dict | None = None
        self.last_search: dict | None = None

    async def _exclusive(self, work):
        if self._lock.locked():
            raise ProbeError("Es läuft schon eine Abfrage. Bitte kurz warten.")
        async with self._lock:
            return await work()

    # ---- reading registers ------------------------------------------------------

    async def read(self, host: str, port: int, unit: int, function: int, start: int, count: int) -> ReadResult:
        result = await self.read_ranges(host, port, unit, function, [(start, count)])
        self.last_read = result
        return result

    async def read_ranges(self, host: str, port: int, unit: int, function: int,
                          ranges: list[tuple[int, int]]) -> ReadResult:
        """Several ranges over one connection (e.g. the blocks of a known wallbox, #325), within the same limits."""
        if function not in (3, 4):
            raise ProbeError("Nur die Funktionscodes 3 und 4 (lesen) sind möglich.")
        if (sum(count for _, count in ranges) > MAX_REGISTERS
                or any(count < 1 or start < 0 or start + count > 0x10000 for start, count in ranges)):
            raise ProbeError(f"Bitte höchstens {MAX_REGISTERS} Register zwischen 0 und 65535 lesen.")
        host = await _check_home(host)
        blocks = [block for start, count in ranges for block in blocks_of(start, count)]
        return await self._exclusive(lambda: self._read(host, port, unit, function, blocks))

    async def _inverter_driver(self, host: str, port: int):
        """OpenAmpere's driver if host:port is the connected inverter: it allows only a few connections, so the
        registers are read over the existing one instead of opening a second."""
        inverter, collector = self.runtime.config.inverter, self.runtime.collector
        if port != inverter.port or not inverter.host or collector.driver is None or not collector.connected:
            return None
        same = await asyncio.to_thread(lambda: bool(_addresses(host) & _addresses(inverter.host)))
        if not same:
            return None
        driver = collector.driver
        return getattr(driver, "_driver", None) or driver  # AutoDriver wraps the detected driver

    async def _read(self, host: str, port: int, unit: int, function: int, blocks: list[tuple[int, int]]) -> ReadResult:
        result = ReadResult(host, port, unit, function)
        driver = await self._inverter_driver(host, port)
        if driver is not None:
            own_unit = getattr(driver, "_unit", None)
            if unit not in (0, own_unit):
                raise ProbeError(f"Am Wechselrichter liest OpenAmpere nur über die bestehende Verbindung, mit der "
                                 f"Geräteadresse {own_unit}.")
            result.unit, result.shared_connection = own_unit, True
            for address, n in blocks:
                result.blocks.append(await self._read_shared(driver, address, n, function))
            return result
        client = AsyncModbusTcpClient(host, port=port, timeout=TIMEOUT_S, retries=0, reconnect_delay=0)
        try:
            if not await client.connect():
                raise ProbeUnreachable(f"Keine Verbindung zu {host}:{port}. Stimmt die Adresse, und ist der Port richtig?")
            await asyncio.sleep(CONNECT_SETTLE_S)
            if not client.connected:
                raise ProbeBusy("Das Gerät hat die Verbindung sofort wieder geschlossen. Vermutlich sind alle "
                                 "Verbindungen belegt, zum Beispiel durch evcc oder ein anderes Programm.")
            for i, (address, n) in enumerate(blocks):
                if i:
                    await asyncio.sleep(REQUEST_GAP_S)
                result.blocks.append(await self._read_block(client, unit, address, n, function))
        finally:
            client.close()
        return result

    @staticmethod
    async def _read_block(client: AsyncModbusTcpClient, unit: int, address: int, count: int, function: int) -> Block:
        block = Block(address, count)
        reader = client.read_input_registers if function == 4 else client.read_holding_registers
        started = time.monotonic()
        try:
            response = await reader(address, count=count, device_id=unit)
        except (ModbusException, OSError, asyncio.TimeoutError):
            block.error = "no_answer"
            return block
        block.ms = round((time.monotonic() - started) * 1000)
        if response.isError():
            code = getattr(response, "exception_code", None)
            block.exception = code if isinstance(code, int) else None
            block.error = "rejected" if code in (1, 2, 3) else "no_answer"
        elif len(response.registers) != count:
            block.error = "no_answer"
        else:
            block.ok, block.words = True, list(response.registers)
        return block

    @staticmethod
    async def _read_shared(driver, address: int, count: int, function: int) -> Block:
        block = Block(address, count)
        started = time.monotonic()
        try:
            block.words, block.ok = await driver._read_once(address, count, function), True
            block.ms = round((time.monotonic() - started) * 1000)
        except ModbusReadError as err:
            block.exception = err.code
            block.error = "rejected" if err.code in (1, 2, 3) else "no_answer"
        except (OSError, asyncio.TimeoutError):
            block.error = "no_answer"
        return block

    # ---- identifying a device -----------------------------------------------------

    async def identify(self, host: str) -> dict:
        host = await _check_home(host)
        result = await self._exclusive(lambda: self._identify(host))
        self.last_identify = result
        return result

    async def _identify(self, host: str) -> dict:
        inverter = self.runtime.config.inverter
        is_inverter = bool(inverter.host) and await asyncio.to_thread(
            lambda: bool(_addresses(host) & _addresses(inverter.host)))

        async def check(port: int) -> bool:
            if is_inverter and port == inverter.port and self.runtime.collector.connected:
                return True  # no extra connection: the inverter allows only a few, and OpenAmpere is connected
            return await _port_open(host, port)

        ports = dict(zip(PORTS, await asyncio.gather(*(check(p) for p in PORTS))))
        title = None
        for port, scheme in WEB_PORTS:
            if ports.get(port) and title is None:
                title = await asyncio.to_thread(_page_title, f"{scheme}://{_url_host(host)}:{port}/")
        return {"host": host, "open_ports": [p for p, is_open in ports.items() if is_open], "title": title,
                "mac_vendor": None if ha_app.active() else await asyncio.to_thread(_oui, host),
                "mac_available": not ha_app.active()}

    # ---- mDNS --------------------------------------------------------------------------

    async def search(self) -> dict:
        """Devices that announce Modbus TCP in the home network (a FoxESS wallbox: EVC-<serial>)."""
        if ha_app.active():  # the app has no host network: multicast does not reach the home network
            result = {"available": False, "devices": []}
        else:
            result = {"available": True,
                      "devices": await self._exclusive(lambda: asyncio.to_thread(browse, MDNS_SERVICES, SEARCH_S))}
        self.last_search = result
        return result

    # ---- report -------------------------------------------------------------------------

    def section(self, include_text: bool = False) -> dict | None:
        """The last results for the diagnostics report. Readable text in register values (serial numbers, names) is
        shortened unless the owner includes it; addresses are pseudonymised with the rest of the report."""
        if not (self.last_read or self.last_identify or self.last_search):
            return None
        section: dict = {}
        if self.last_read:
            read = asdict(self.last_read)
            read["blocks"] = [privacy.masked_block(b, include_text) for b in read["blocks"]]
            section["read"] = read
        if self.last_identify:
            section["identify"] = self.last_identify
        if self.last_search:
            section["search"] = {**self.last_search, "devices": [
                {**d, "name": d["name"] if include_text else privacy.shorten(d["name"])} for d in self.last_search["devices"]]}
        return section


async def _port_open(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
        writer.close()
        return True
    except (OSError, asyncio.TimeoutError):
        return False


def _url_host(host: str) -> str:
    return f"[{host}]" if ":" in host else host


def _page_title(url: str) -> str | None:
    """The <title> of a device's web page: often the product name. Self-signed certificates are usual on devices in
    the home network; nothing is sent, only read."""
    context = ssl.create_default_context()
    context.check_hostname, context.verify_mode = False, ssl.CERT_NONE
    request = urllib.request.Request(url, headers={"User-Agent": "OpenAmpere"})
    try:  # no redirects: a device sending the request on to its maker's cloud is not followed
        with open_without_redirect(request, TIMEOUT_S, context if url.startswith("https") else None) as response:
            body = response.read(TITLE_BYTES)
    except urllib.error.HTTPError as err:  # an error or redirect page still has a title
        body = err.read(TITLE_BYTES) if err.fp else b""
    except (OSError, ValueError):
        return None
    match = re.search(rb"<title[^>]*>(.*?)</title>", body, re.I | re.S)
    if not match:
        return None
    title = " ".join(html.unescape(match.group(1).decode("utf-8", "replace")).split())
    return title[:100] or None


def _oui(host: str) -> str | None:
    """Vendor part of the device's MAC address (the first three bytes) from the neighbour table, Linux only. The full
    address identifies the device and never leaves this function."""
    try:
        ips = {a for a in _addresses(host) if ipaddress.ip_address(a).version == 4}
        with open(ARP, encoding="ascii") as f:
            lines = f.read().splitlines()[1:]
    except (OSError, ValueError):
        return None
    for line in lines:
        parts = line.split()
        if len(parts) >= 4 and parts[0] in ips and parts[3] != "00:00:00:00:00:00":
            return parts[3].upper()[:8]
    return None


# ---- minimal mDNS (RFC 6762 one-shot query, no dependency) ------------------------------------------------------


def _encode_name(name: str) -> bytes:
    return b"".join(bytes([len(label)]) + label.encode() for label in name.strip(".").split(".")) + b"\0"


def mdns_query(service: str) -> bytes:
    # a query from a port other than 5353 is answered by unicast to that port (legacy query, RFC 6762 6.7)
    return struct.pack(">HHHHHH", 0, 0, 1, 0, 0, 0) + _encode_name(service) + struct.pack(">HH", 12, 1)


def _read_name(data: bytes, offset: int) -> tuple[str, int]:
    labels, end, jumps = [], None, 0
    while True:
        if offset >= len(data):
            raise ValueError("name outside the packet")
        length = data[offset]
        if length & 0xC0 == 0xC0:  # compression pointer
            if jumps > 20 or offset + 1 >= len(data):
                raise ValueError("bad pointer")
            end = offset + 2 if end is None else end
            offset, jumps = ((length & 0x3F) << 8) | data[offset + 1], jumps + 1
            continue
        if length == 0:
            return ".".join(labels), (offset + 1 if end is None else end)
        labels.append(data[offset + 1:offset + 1 + length].decode("utf-8", "replace"))
        offset += 1 + length


def parse_mdns(data: bytes) -> list[tuple[str, int, object]]:
    """Records of an mDNS answer as (name, type, value): PTR -> name, SRV -> (port, target), A -> address."""
    _, _, qd, an, ns, ar = struct.unpack(">HHHHHH", data[:12])
    offset, records = 12, []
    for _ in range(qd):
        _, offset = _read_name(data, offset)
        offset += 4
    for _ in range(an + ns + ar):
        name, offset = _read_name(data, offset)
        rtype, _, _, length = struct.unpack(">HHIH", data[offset:offset + 10])
        offset += 10
        rdata = offset
        offset += length
        if rtype == 12:
            records.append((name, rtype, _read_name(data, rdata)[0]))
        elif rtype == 33:
            port = struct.unpack(">H", data[rdata + 4:rdata + 6])[0]
            records.append((name, rtype, (port, _read_name(data, rdata + 6)[0])))
        elif rtype == 1 and length == 4:
            records.append((name, rtype, socket.inet_ntoa(data[rdata:rdata + 4])))
    return records


def _local_address() -> str:
    """The address of the network card the home network is reached over (no packet is sent)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe_socket:
        probe_socket.connect(MDNS)
        return probe_socket.getsockname()[0]


def browse(services: tuple[str, ...], seconds: float) -> list[dict]:
    found: dict[str, dict] = {}
    local = _local_address()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        # only the home network's card: the query goes out there, and only answers to that address are received
        sock.bind((local, 0))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(local))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        for service in services:
            sock.sendto(mdns_query(service), MDNS)
        deadline = time.monotonic() + seconds
        while (left := deadline - time.monotonic()) > 0:
            sock.settimeout(left)
            try:
                data, (sender, _) = sock.recvfrom(9000)
            except (TimeoutError, socket.timeout):
                break
            try:
                records = parse_mdns(data)
            except (ValueError, struct.error):
                continue
            addresses = {name.lower(): value for name, rtype, value in records if rtype == 1}
            servers = {name.lower(): value for name, rtype, value in records if rtype == 33}
            for name, rtype, instance in records:
                if rtype != 12 or name.lower() not in services:
                    continue
                port, target = servers.get(str(instance).lower(), (None, None))
                host = addresses.get(str(target).lower()) if target else None
                found[str(instance).lower()] = {"service": name.rsplit(".local", 1)[0],
                                                "name": str(instance).split(".", 1)[0],
                                                "host": host or sender, "port": port}
    return sorted(found.values(), key=lambda d: (d["host"], d["name"]))


def section_markdown(section: dict) -> list[str]:
    """The probe's part of the diagnostics report as text for an issue (German, like the rest of the report)."""
    lines = []
    search = section.get("search")
    if search:
        found = ", ".join(f"{d['name']} ({d['host']}:{d['port']})" for d in search["devices"]) or "keine Geräte"
        lines.append(f"- Suche im Heimnetz (mDNS): {found}" if search["available"]
                     else "- Suche im Heimnetz (mDNS): in der Home-Assistant-App nicht verfügbar")
    identify = section.get("identify")
    if identify:
        ports = ", ".join(str(p) for p in identify["open_ports"]) or "keine"
        lines.append(f"- Gerät {identify['host']}: offene Ports {ports}"
                     + (f"; Titel der Webseite „{identify['title']}“" if identify.get("title") else "")
                     + (f"; Hersteller-Kennung der Netzwerkkarte {identify['mac_vendor']}"
                        if identify.get("mac_vendor") else ""))
    read = section.get("read")
    if read:
        how = " über die Verbindung zum Wechselrichter" if read.get("shared_connection") else ""
        lines.append(f"- Register von {read['host']}:{read['port']}, Geräteadresse {read['unit']}, "
                     f"Funktionscode {read['function']}{how}:")
        lines += blocks_markdown(read["blocks"], "Registerwerte")
    return lines


def blocks_markdown(blocks: list[dict], summary: str) -> list[str]:
    """Blocks of register values for the report (already masked): what each block answered, then the values."""
    lines = []
    for block in blocks:
        if block["ok"]:
            state = f"gelesen ({block['ms']} ms)"
        elif block["error"] == "rejected":
            state = f"abgelehnt (Modbus-Ausnahme {block['exception']})"
        else:
            state = "keine Antwort" + (f" (Modbus-Ausnahme {block['exception']})" if block["exception"] else "")
        lines.append(f"  - {block['start']}+{block['count']} (0x{block['start']:04X}): {state}")
    readable = [{k: b.get(k) for k in ("start", "words", "text")} for b in blocks if b["ok"]]
    if readable:
        lines += ["", f"<details><summary>{summary}</summary>", "", "```json",
                  json.dumps(readable, ensure_ascii=False), "```", "", "</details>"]
    return lines


def read_view(result: ReadResult) -> dict:
    """For the owner's own screen: all values and the readable text in full (shortened only in the report)."""
    view = asdict(result)
    view["blocks"] = [privacy.masked_block(b, include_text=True) for b in view["blocks"]]
    return view
