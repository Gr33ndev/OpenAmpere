# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Shared Modbus TCP plumbing for all drivers: one persistent, serialised connection, retries for
temporary errors (typical behind Modbus proxies), and block reads that fall back to single registers."""

from __future__ import annotations

import asyncio
import logging
import time

from pymodbus import FramerType
from pymodbus.client import AsyncModbusTcpClient
from pymodbus.exceptions import ModbusException

from ..privacy import shorten
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


class InverterBusy(DeviceUnreachable):
    """The device accepts the TCP connection and closes it right away, before any request: all its connection
    slots are taken. A FoxESS H3 allows very few Modbus TCP connections, often held by another program that reads the
    inverter (e.g. a Modbus setup in Home Assistant) or the old IQ box (#311)."""


class DetectionFailed(ConnectionError):
    """Something answers, but it is not a supported device. .message is a German text for the app."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class WrongDevice(ConnectionError):
    """Another device than the owner's inverter answers at the address (#567): a tablet in another network, or the
    router gave the address to another device. Nothing is read or written until the right one answers again."""

    def __init__(self, expected: str, found: str | None) -> None:
        # shortened: the text goes into the log, the connection events and the control log, which reports show
        super().__init__(f"serial number {shorten(found)!r} instead of {shorten(expected)!r}")
        self.expected, self.found = expected, found
        self.message = ("Unter der eingestellten Adresse antwortet ein anderer Wechselrichter als bisher. OpenAmpere "
                        "liest und steuert ihn nicht. Ist das Tablet oder der Server in einem anderen Netzwerk, "
                        "verbindet sich OpenAmpere von selbst wieder, sobald es zurück ist.")


class ModbusReadError(Exception):
    """Any failed Modbus request. .code is the Modbus exception code of the device's answer, if there was one."""

    code: int | None = None


class ModbusIllegalError(ModbusReadError):
    """The device rejected the request (illegal function/address/value): retrying will not help."""


class DeviceRefused(ModbusIllegalError):
    """The driver refuses a write for a reason the owner can act on; the message is shown in the app."""


class ModbusTransientError(ModbusReadError):
    """Timeout, busy device or a gateway/proxy that could not reach the inverter: try again later."""

    transient = True  # tells the collector to keep the connection and simply retry


# Modbus exception codes that mean "this request can never work"; everything else is transient
# (4 device failure, 5/6 acknowledge/busy, 10/11 gateway path unavailable / target failed to respond).
PERMANENT_EXCEPTION_CODES = {1, 2, 3}


BUSY_MESSAGE = ("Der Wechselrichter lässt gerade keine weitere Verbindung zu, er erlaubt nur sehr wenige. Prüfe "
                "zuerst, ob noch ein anderes Programm den Wechselrichter abfragt, zum Beispiel Home Assistant mit "
                "einer eigenen Modbus-Einbindung, ein Dashboard, ein eigenes Script oder eine zweite "
                "OpenAmpere-Installation, und beende es oder entferne dort die Verbindung zum Wechselrichter. Ist noch die bisherige "
                "IQ-Box verbunden und wird sie nicht mehr gebraucht, trenne sie danach vom Netzwerk (LAN-Kabel ziehen "
                "oder Box ausschalten). Dann verbindet sich OpenAmpere von selbst. Eine verbundene IQ-Box überschreibt "
                "außerdem Einstellungen, die du in OpenAmpere änderst (Anleitung: „Die bisherige Smartbox setzt "
                "Einstellungen zurück“).")


def friendly_error(err: BaseException) -> str:
    """German plain-language text for errors shown in the app (the technical text goes to the log)."""
    if isinstance(err, InverterBusy):
        return BUSY_MESSAGE
    if isinstance(err, DeviceUnreachable):
        # a driver without Modbus (Senec) says itself what to check
        return getattr(err, "message", None) or ("Keine Verbindung zum Wechselrichter. Ist er eingeschaltet, stimmt die "
                                                 "Adresse und ist Modbus TCP aktiviert?")
    if isinstance(err, DeviceRefused):
        return str(err)
    if isinstance(err, ModbusIllegalError):
        return "Der Wechselrichter hat die Anfrage abgelehnt (Register nicht vorhanden)."
    if isinstance(err, (ModbusTransientError, TimeoutError, asyncio.TimeoutError)):
        if getattr(err, "message", None):  # a driver without Modbus (Senec) says itself what happens
            return err.message
        return "Der Wechselrichter antwortet nicht rechtzeitig. Bei einem Modbus-Proxy oder WLAN hilft ein höheres Zeitlimit."
    if isinstance(err, OSError) and err.errno is not None:
        if err.errno in (113, 65, 51, 101):  # host/network unreachable (Linux, macOS)
            return "Unter dieser Adresse ist kein Gerät erreichbar. Hat der Wechselrichter eine neue IP-Adresse bekommen?"
        if err.errno in (111, 61):  # connection refused
            return "Das Gerät lehnt die Verbindung ab. Ist Modbus TCP aktiviert und der Port richtig?"
        return "Netzwerkfehler bei der Verbindung zum Wechselrichter."
    if isinstance(err, (DetectionFailed, WrongDevice)):
        return err.message
    return "Unerwarteter Fehler bei der Verbindung zum Wechselrichter (Details im Protokoll des Servers)."


def _error_from_response(response, what: str) -> ModbusReadError:
    code = getattr(response, "exception_code", None)
    if code in PERMANENT_EXCEPTION_CODES:
        err: ModbusReadError = ModbusIllegalError(f"{what}: Modbus exception {code}")
    else:
        err = ModbusTransientError(f"{what}: {response}")
    err.code = code if isinstance(code, int) else None
    return err


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
        # how the requests are framed on TCP: Modbus TCP, or RTU frames over TCP (M-TEC's Wi-Fi/LAN module, #520)
        self.framer = FramerType.SOCKET
        # the serial number of the owner's inverter (the collector sets it from what it knows, #567); None: the one
        # this driver detected
        self.expected_serial: str | None = None
        self._identity_checked = False  # the current TCP connection reaches that inverter
        self._identity_lock = asyncio.Lock()
        self._checker: asyncio.Task | None = None  # the task reading the serial number for that
        self._detect_lock = asyncio.Lock()
        self._detector: asyncio.Task | None = None  # the task detecting the device, which reads before info is set

    # ---- low level -------------------------------------------------------

    async def _ensure_connected(self) -> AsyncModbusTcpClient:
        if self._client is None:
            # created lazily: pymodbus needs a running event loop
            # retries=0 and reconnect_delay=0: retrying and reconnecting are handled here, not by pymodbus
            # (its own background reconnect could open a second socket and block one of the few slots)
            self._client = AsyncModbusTcpClient(self._host, port=self._port, timeout=self._timeout, retries=0,
                                                reconnect_delay=0, framer=self.framer)
        if not self._client.connected:
            if not await self._client.connect():
                self._client.close()
                self._client = None
                raise DeviceUnreachable(f"cannot connect to inverter at {self._host}:{self._port}")
            await asyncio.sleep(CONNECT_SETTLE_S)
            if not self._client.connected:  # closed by the device while settling, before any request (#311)
                self._client.close()
                self._client = None
                raise InverterBusy(f"{self._host}:{self._port} closed the connection right after connecting")
            self._identity_checked = False  # a new connection can reach another device (#567)
        return self._client

    async def _read(self, address: int, count: int, function: int) -> list[int]:
        """Read registers; temporary failures (typical behind a Modbus proxy) are retried right away."""
        if self.info is None and self._detector is not asyncio.current_task() and self._expected():
            await self.connect()  # forgotten after another device answered: detected and compared again (#567)
        # the serial number of the identity check is read once per attempt of the read it guards, not retried in itself
        attempts = 1 if self._checker is asyncio.current_task() else self._attempts
        for attempt in range(1, attempts + 1):
            try:
                await self.check_identity()
                return await self._read_once(address, count, function)
            except ModbusTransientError as err:
                raise_if_cancelled()
                log.debug("attempt %d/%d failed: %s", attempt, attempts, err)
                if attempt == attempts:
                    raise
                await asyncio.sleep(RETRY_DELAY_S)
        raise AssertionError("unreachable")

    async def read_once(self, address: int, count: int, function: int) -> list[int]:
        """One request without retries, for the diagnostics and the device query; checked like every read (#567).
        Another device raises WrongDevice, a ConnectionError."""
        if self.info is None and self._detector is not asyncio.current_task() and self._expected():
            await self.connect()
        await self.check_identity()
        return await self._read_once(address, count, function)

    async def _read_once(self, address: int, count: int, function: int) -> list[int]:
        async with self._lock:
            client = await self._ensure_connected()
            if self._unchecked():  # the connection was renewed after the check: checked on the next attempt
                raise ModbusTransientError(f"read {address}+{count}: new connection, not checked yet")
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
        # never to another device than the owner's inverter, also not the "safe" switch-off (#567)
        if self.info is None:  # detection only reads; forgotten after another device answered: compared again
            await self.connect()
        await self.check_identity()
        async with self._lock:
            client = await self._ensure_connected()
            if self._unchecked():  # the connection was renewed after the check
                raise ModbusTransientError(f"write {reg.address}={value}: new connection, not checked yet")
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
        async with self._detect_lock:  # one detection at a time: a reader or writer waits for the running one
            if self.info is not None:
                return self.info
            self._detector = asyncio.current_task()
            try:
                await self._detect()
                return await self.match_detected()
            except ModbusTransientError as err:
                await self.close()  # free the connection slot right away; the inverter allows only a few
                raise ConnectionError(f"device not answering during detection: {err}") from err
            except BaseException:
                await self.close()
                raise
            finally:
                self._detector = None

    async def _detect(self) -> DeviceInfo:
        raise NotImplementedError

    def _expected(self) -> str | None:
        return self.expected_serial or (self.info.serial if self.info else None)

    async def match_detected(self) -> DeviceInfo:
        """After a detection: the device must be the owner's inverter, otherwise it is forgotten again."""
        assert self.info is not None
        expected, found = self._expected(), self.info.serial
        if expected and found != expected:  # also none at all: the owner's inverter tells one
            self.expected_serial, self.info = expected, None  # still the owner's inverter that is expected
            await self.close()
            raise WrongDevice(expected, found)
        self._identity_checked = True  # detection read the serial number on this connection
        return self.info

    async def _read_serial(self) -> str | None:
        """The serial number as detection reads it (drivers whose detection reads one override this)."""
        raise NotImplementedError

    def _unchecked(self) -> bool:
        """On this connection the device was not confirmed as the owner's inverter yet. A device whose detection
        found no serial number (FoxESS legacy map) cannot be told apart and is not checked, as before."""
        return (not self._identity_checked and self._checker is not asyncio.current_task() and self.info is not None
                and bool(self.info.serial) and bool(self._expected()))

    async def check_identity(self) -> None:
        """Reads the serial number again on a new connection, before anything else is read or written (#567)."""
        if not self._unchecked():
            return
        async with self._identity_lock:  # a second caller waits for the first check
            if not self._unchecked():
                return
            expected = self._expected()
            self._checker = asyncio.current_task()
            try:
                found = await self._read_serial()
            except ModbusIllegalError:
                found = None  # it rejects where the owner's inverter tells its serial number
            finally:
                self._checker = None
            if found != expected:  # also none at all: the device told one before
                # detected again once a device answers here, still expecting the owner's inverter
                self.expected_serial, self.info = expected, None
                await self.close()
                raise WrongDevice(expected, found)
            self._identity_checked = True

    async def disconnect(self) -> None:
        """Close the TCP connection but keep the detected device info (for "connect per poll" mode)."""
        if self._client is not None:
            self._client.close()
            self._client = None
        self._identity_checked = False

    async def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
        self._identity_checked = False

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
