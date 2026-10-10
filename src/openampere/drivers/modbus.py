# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Shared Modbus TCP plumbing for all drivers: one persistent, serialised connection, retries for
temporary errors (typical behind Modbus proxies), and block reads that fall back to single registers."""

from __future__ import annotations

import asyncio
import logging
import time

from pymodbus.client import AsyncModbusTcpClient
from pymodbus.exceptions import ModbusException

from .base import DeviceInfo, raise_if_cancelled
from .regs import Reg, decode, encode

log = logging.getLogger(__name__)

REQUEST_GAP_S = 0.03  # pause between requests, helps inverter stability on LAN
CONNECT_SETTLE_S = 1.0
READ_ATTEMPTS = 3  # per request, for temporary errors (proxy could not reach the inverter, busy, ...)
RETRY_DELAY_S = 0.3
BAD_ADDRESS_TTL_S = 600  # rejected addresses are retried after this time (device may have been booting)


class DeviceUnreachable(ConnectionError):
    """Nothing accepts TCP connections at host:port."""


class DetectionFailed(ConnectionError):
    """Something answers, but it is not a supported device. The message is a German text for the app."""


class ModbusReadError(Exception):
    """Any failed Modbus request."""


class ModbusIllegalError(ModbusReadError):
    """The device rejected the request (illegal function/address/value): retrying will not help."""


class ModbusTransientError(ModbusReadError):
    """Timeout, busy device or a gateway/proxy that could not reach the inverter: try again later."""

    transient = True  # tells the collector to keep the connection and simply retry


# Modbus exception codes that mean "this request can never work"; everything else is transient
# (4 device failure, 5/6 acknowledge/busy, 10/11 gateway path unavailable / target failed to respond).
PERMANENT_EXCEPTION_CODES = {1, 2, 3}


def friendly_error(err: BaseException) -> str:
    """German plain-language text for errors shown in the app (the technical text goes to the log)."""
    if isinstance(err, DeviceUnreachable):
        return "Keine Verbindung zum Wechselrichter. Ist er eingeschaltet, stimmt die Adresse und ist Modbus TCP aktiviert?"
    if isinstance(err, ModbusIllegalError):
        return "Der Wechselrichter hat die Anfrage abgelehnt (Register nicht vorhanden)."
    if isinstance(err, (ModbusTransientError, TimeoutError, asyncio.TimeoutError)):
        return "Der Wechselrichter antwortet nicht rechtzeitig. Bei einem Modbus-Proxy oder WLAN hilft ein höheres Zeitlimit."
    if isinstance(err, OSError) and err.errno is not None:
        if err.errno in (113, 65, 51, 101):  # host/network unreachable (Linux, macOS)
            return "Unter dieser Adresse ist kein Gerät erreichbar. Hat der Wechselrichter eine neue IP-Adresse bekommen?"
        if err.errno in (111, 61):  # connection refused
            return "Das Gerät lehnt die Verbindung ab. Ist Modbus TCP aktiviert und der Port richtig?"
        return "Netzwerkfehler bei der Verbindung zum Wechselrichter."
    if isinstance(err, DetectionFailed):
        return str(err)
    return "Unerwarteter Fehler bei der Verbindung zum Wechselrichter (Details im Protokoll des Servers)."


def _error_from_response(response, what: str) -> ModbusReadError:
    code = getattr(response, "exception_code", None)
    if code in PERMANENT_EXCEPTION_CODES:
        return ModbusIllegalError(f"{what}: Modbus exception {code}")
    return ModbusTransientError(f"{what}: {response}")


def ascii_text(words: list[int]) -> str:
    chars = []
    for word in words:
        for byte in ((word >> 8) & 0xFF, word & 0xFF):
            if byte == 0:
                continue
            if not 32 <= byte < 127:
                return "".join(chars).strip()
            chars.append(chr(byte))
    return "".join(chars).strip()



_ascii = ascii_text  # backwards compatible name


class ModbusDevice:
    """Base class of all drivers. One persistent connection; all requests serialised
    (inverters allow only a few connections)."""

    def __init__(self, host: str, port: int, unit: int, *, timeout: float = 3.0,
                 read_attempts: int = READ_ATTEMPTS) -> None:
        self._host, self._port, self._timeout = host, port, timeout
        self._attempts = read_attempts
        self._client: AsyncModbusTcpClient | None = None
        self._unit = unit
        self._lock = asyncio.Lock()
        self.read_function: int = 3
        self.info: DeviceInfo | None = None
        self._bad_addresses: dict[int, float] = {}  # address -> time until it is skipped

    # ---- low level -------------------------------------------------------

    async def _ensure_connected(self) -> AsyncModbusTcpClient:
        if self._client is None:
            # created lazily: pymodbus needs a running event loop
            # retries=0 and reconnect_delay=0: retrying and reconnecting are handled here, not by pymodbus
            # (its own background reconnect could open a second socket and block one of the few slots)
            self._client = AsyncModbusTcpClient(self._host, port=self._port, timeout=self._timeout, retries=0,
                                                reconnect_delay=0)
        if not self._client.connected:
            if not await self._client.connect():
                self._client.close()
                self._client = None
                raise DeviceUnreachable(f"cannot connect to inverter at {self._host}:{self._port}")
            await asyncio.sleep(CONNECT_SETTLE_S)
        return self._client

    async def _read(self, address: int, count: int, function: int) -> list[int]:
        """Read registers; temporary failures (typical behind a Modbus proxy) are retried right away."""
        for attempt in range(1, self._attempts + 1):
            try:
                return await self._read_once(address, count, function)
            except ModbusTransientError as err:
                raise_if_cancelled()
                log.debug("attempt %d/%d failed: %s", attempt, self._attempts, err)
                if attempt == self._attempts:
                    raise
                await asyncio.sleep(RETRY_DELAY_S)
        raise AssertionError("unreachable")

    async def _read_once(self, address: int, count: int, function: int) -> list[int]:
        async with self._lock:
            client = await self._ensure_connected()
            reader = client.read_input_registers if function == 4 else client.read_holding_registers
            try:
                response = await reader(address, count=count, device_id=self._unit)
            except ModbusException as err:  # no (valid) answer, e.g. timeout
                raise ModbusTransientError(f"read {address}+{count}: {err}") from err
            finally:
                await asyncio.sleep(REQUEST_GAP_S)
        if response.isError():
            raise _error_from_response(response, f"read {address}+{count} (fc{function})")
        if len(response.registers) != count:  # e.g. a proxy answering with a cached, shorter block
            raise ModbusTransientError(f"read {address}+{count}: got {len(response.registers)} registers")
        return list(response.registers)

    async def _write(self, reg: Reg, value: int) -> None:
        words = encode(reg, value)
        async with self._lock:
            client = await self._ensure_connected()
            try:
                if len(words) == 1:
                    response = await client.write_register(reg.address, words[0], device_id=self._unit)
                else:
                    response = await client.write_registers(reg.address, words, device_id=self._unit)
            except ModbusException as err:
                raise ModbusTransientError(f"write {reg.address}={value}: {err}") from err
            finally:
                await asyncio.sleep(REQUEST_GAP_S)
        if response.isError():
            # a read-only Modbus proxy typically answers with "illegal function"
            raise _error_from_response(response, f"write {reg.address}={value}")

    async def _read_reg(self, reg: Reg, function: int = 3) -> float:
        return decode(reg, await self._read(reg.address, reg.count, function))

    async def _probe(self, address: int, count: int, functions: tuple[int, ...]) -> tuple[int, list[int]] | None:
        """Try function codes in order. Only a rejection moves on to the next one; transient errors
        (timeouts, proxy cannot reach the inverter) abort detection so it is retried later instead of
        guessing wrong."""
        for function in functions:
            try:
                return function, await self._read(address, count, function)
            except ModbusIllegalError:
                continue
        return None

    async def connect(self) -> DeviceInfo:
        if self.info is not None:  # already detected: only the TCP connection is re-established (lazily)
            return self.info
        try:
            return await self._detect()
        except ModbusTransientError as err:
            await self.close()  # free the connection slot right away; the inverter allows only a few
            raise ConnectionError(f"device not answering during detection: {err}") from err
        except BaseException:
            await self.close()
            raise

    async def _detect(self) -> DeviceInfo:
        raise NotImplementedError

    async def disconnect(self) -> None:
        """Close the TCP connection but keep the detected device info (for "connect per poll" mode)."""
        if self._client is not None:
            self._client.close()
            self._client = None

    async def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    # ---- reading ---------------------------------------------------------

    async def _read_block(self, start: int, count: int) -> dict[int, int]:
        """Read a block. If the device rejects it, fall back to single registers and remember the
        addresses it rejects. Transient errors are raised unchanged so nothing is blacklisted by mistake."""
        try:
            words = await self._read(start, count, self.read_function)
            return {start + i: w for i, w in enumerate(words)}
        except ModbusIllegalError:
            log.debug("block %s+%s rejected, reading the registers one by one", start, count)
            values = {}
            now = time.monotonic()
            for address in range(start, start + count):
                if self._bad_addresses.get(address, 0) > now:
                    continue
                try:
                    values[address] = (await self._read(address, 1, self.read_function))[0]
                    self._bad_addresses.pop(address, None)
                except ModbusIllegalError:
                    self._bad_addresses[address] = now + BAD_ADDRESS_TTL_S
            if not values:
                raise
            return values
