# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Known device drivers and automatic detection.

Detection only reads identification registers and stops at the first match. Vendors use disjoint
register ranges, so probing the wrong vendor merely produces Modbus rejections.
"""

from __future__ import annotations

import asyncio
import logging

from .base import DeviceInfo
from .enphase.driver import EnphaseDriver
from .foxess.driver import FoxessDriver
from .modbus import (DetectionFailed, DeviceUnreachable, ModbusDevice, ModbusIllegalError, ModbusReadError,
                     WrongDevice)
from .mtec.driver import MtecDriver
from .saj.driver import SajDriver
from .senec.driver import SenecDriver
from .solarmax.driver import SolarmaxDriver

log = logging.getLogger(__name__)

# SenecDriver is no ModbusDevice (HTTP), but has the same face; it is only used when chosen (AUTO_DETECT)
DRIVERS: dict[str, type[ModbusDevice]] = {"foxess": FoxessDriver, "saj": SajDriver, "solarmax": SolarmaxDriver,
                                          "mtec": MtecDriver, "senec": SenecDriver,
                                          "enphase": EnphaseDriver}  # type: ignore[dict-item]

LABELS = {
    "auto": "Automatisch erkennen",
    "foxess": "FoxESS H3 / H3 Smart / H3 Pro",
    "saj": "SAJ H2 / HS2",
    "solarmax": "Solarmax MAX.STORAGE (nur Anzeige)",
    "mtec": "M-TEC Energy-Butler GEN3 (nur Anzeige)",
    "senec": "Senec.Home V3 / V2.1 (nur Anzeige)",
    "enphase": "Enphase IQ Gateway mit IQ Battery (nur Anzeige)",
}

DETECT_TIMEOUT_S = 2.5


def create(key: str, host: str, port: int, unit: int, *, timeout: float, register_map: str = "auto",
           read_function: str = "auto", token: str = "", cert_sha256: str = "") -> ModbusDevice:
    """token: the access token of a device that needs one (Enphase), cert_sha256 the certificate it may go to (#539);
    the other drivers ignore both."""
    cls = DRIVERS[key]
    unit = unit or cls.DEFAULT_UNITS[0]
    if cls is FoxessDriver:
        return FoxessDriver(host, port, unit, register_map=register_map, read_function=read_function, timeout=timeout)
    return cls(host, port, unit, timeout=timeout, token=token, cert_sha256=cert_sha256)


async def detect_driver(host: str, port: int, unit: int = 0, *, timeout: float = DETECT_TIMEOUT_S,
                        only: str | None = None, register_map: str = "auto", read_function: str = "auto",
                        token: str = "", cert_sha256: str = "", quick: bool | None = None):
    """Returns (info, connected driver) of the first driver that recognises the device; the caller owns the
    driver and must close it. Raises ConnectionError. quick: shortcuts for silent hosts (M-TEC), by default only in
    the automatic detection; a search through the network wants them with a fixed driver too (#547)."""
    quick = not only if quick is None else quick
    timeout = max(timeout, DETECT_TIMEOUT_S)  # never shorter than configured (slow proxies need more)
    keys = [only] if only else [key for key, cls in DRIVERS.items() if getattr(cls, "AUTO_DETECT", True)]
    failure: DetectionFailed | None = None  # a driver's own reason, shown when the owner chose that driver
    for key in keys:
        cls = DRIVERS[key]
        for candidate_unit in ([unit] if unit else list(cls.DEFAULT_UNITS)):
            if cls is FoxessDriver:
                driver = FoxessDriver(host, port, candidate_unit, timeout=timeout, read_attempts=2,
                                      register_map=register_map, read_function=read_function)
            else:
                driver = cls(host, port, candidate_unit, timeout=timeout, read_attempts=2, quick=quick, token=token,
                             cert_sha256=cert_sha256)
            try:
                info = await driver.connect()
                log.info("detected %s on unit %s", info.model, candidate_unit)
                driver._attempts = 3  # normal operation: full retries again
                return info, driver
            except DeviceUnreachable:
                await driver.close()
                raise  # nothing listens there: no point in trying other vendors
            except (ConnectionError, ModbusReadError, OSError, asyncio.TimeoutError) as err:
                log.debug("%s unit %s: %s", key, candidate_unit, err)
                failure = err if only and isinstance(err, DetectionFailed) else failure
                await driver.close()
    if failure is not None:
        raise failure
    raise DetectionFailed("Kein unterstütztes Gerät erkannt. Ist die Adresse richtig und Modbus TCP aktiviert?")


async def detect(host: str, port: int, unit: int = 0, *, timeout: float = DETECT_TIMEOUT_S,
                 only: str | None = None, token: str = "", cert_sha256: str = "",
                 quick: bool | None = None) -> DeviceInfo:
    """Returns the info of the first driver that recognises the device. Raises ConnectionError."""
    info, driver = await detect_driver(host, port, unit, timeout=timeout, only=only, token=token,
                                       cert_sha256=cert_sha256, quick=quick)
    await driver.close()
    return info


GATEWAY_CODES = (10, 11)  # Modbus exceptions "gateway path unavailable" and "gateway target failed to respond"
UNCONFIRMED_BEFORE_DETECTION = 3  # new connections in a row answered so before the make is detected anew (#567)


class AutoDriver:
    """Detects the device on first connect and then behaves exactly like the matching driver."""

    def __init__(self, host: str, port: int, unit: int = 0, *, timeout: float = 3.0, register_map: str = "auto",
                 read_function: str = "auto") -> None:
        self._args = dict(host=host, port=port, unit=unit, timeout=timeout, register_map=register_map,
                          read_function=read_function)
        self._driver: ModbusDevice | None = None
        self._expected_serial: str | None = None
        self._unconfirmed = 0  # new connections in a row on which a gateway reported the device as not answering

    @property
    def expected_serial(self) -> str | None:
        return self._expected_serial

    @expected_serial.setter
    def expected_serial(self, serial: str | None) -> None:
        self._expected_serial = serial
        if self._driver is not None:
            self._driver.expected_serial = serial

    async def connect(self) -> DeviceInfo:
        if self._driver is not None:
            # a new connection (after a lost one) must reach the same device (#567). Detected anew only when a device
            # answers but cannot be confirmed: another make may answer only with errors to this driver's requests.
            # Timeouts and network errors keep the driver (and what it learned): the same device is likely back later.
            forgot = self._driver.info is None  # after another device answered
            try:
                info = await self._driver.connect()
                await self._driver.check_identity()
                self._unconfirmed = 0
                return info
            except WrongDevice as err:
                if err.found is not None:  # the same make with another serial number: nothing to detect
                    raise
            except ModbusIllegalError:
                pass  # it answers, but has no such register
            except ModbusReadError as err:
                # a gateway's "target failed" also comes from a proxy whose inverter is gone for a moment
                self._unconfirmed += 1
                if err.code not in GATEWAY_CODES or self._unconfirmed < UNCONFIRMED_BEFORE_DETECTION:
                    raise
            except (ConnectionError, OSError, TimeoutError):
                if not forgot:
                    raise
            self._unconfirmed = 0
            # still the device detected before that is expected, also without the collector telling it
            self._expected_serial = self._expected_serial or self._driver._expected()
            await self._driver.close()
            self._driver = None
        a = self._args
        # keep the connection that detected the device instead of opening a second one
        info, driver = await detect_driver(a["host"], a["port"], a["unit"], timeout=a["timeout"],
                                           register_map=a["register_map"], read_function=a["read_function"])
        driver.expected_serial = self._expected_serial
        try:
            await driver.match_detected()  # detected without knowing the owner's inverter (#567)
        except BaseException:
            await driver.close()
            raise
        self._driver = driver
        return info

    async def disconnect(self) -> None:
        if self._driver is not None:
            await self._driver.disconnect()

    async def close(self) -> None:
        if self._driver is not None:
            await self._driver.close()

    def __getattr__(self, name):
        if self._driver is None:
            raise ConnectionError("device not detected yet")
        return getattr(self._driver, name)
