"""Known device drivers and automatic detection.

Detection only reads identification registers and stops at the first match. Vendors use disjoint
register ranges, so probing the wrong vendor merely produces Modbus rejections.
"""

from __future__ import annotations

import asyncio
import logging

from .base import DeviceInfo
from .foxess.driver import FoxessDriver
from .modbus import DeviceUnreachable, ModbusDevice, ModbusReadError
from .saj.driver import SajDriver

log = logging.getLogger(__name__)

DRIVERS: dict[str, type[ModbusDevice]] = {"foxess": FoxessDriver, "saj": SajDriver}

LABELS = {
    "auto": "Automatisch erkennen",
    "foxess": "FoxESS H3 / H3 Smart / H3 Pro",
    "saj": "SAJ H2 / HS2 (nur Anzeige)",
}

DETECT_TIMEOUT_S = 2.5


def create(key: str, host: str, port: int, unit: int, *, timeout: float, register_map: str = "auto",
           read_function: str = "auto") -> ModbusDevice:
    cls = DRIVERS[key]
    unit = unit or cls.DEFAULT_UNITS[0]
    if cls is FoxessDriver:
        return FoxessDriver(host, port, unit, register_map=register_map, read_function=read_function, timeout=timeout)
    return cls(host, port, unit, timeout=timeout)


async def detect_driver(host: str, port: int, unit: int = 0, *, timeout: float = DETECT_TIMEOUT_S,
                        only: str | None = None, register_map: str = "auto", read_function: str = "auto"):
    """Returns (info, connected driver) of the first driver that recognises the device; the caller owns the
    driver and must close it. Raises ConnectionError."""
    timeout = max(timeout, DETECT_TIMEOUT_S)  # never shorter than configured (slow proxies need more)
    keys = [only] if only else list(DRIVERS)
    for key in keys:
        cls = DRIVERS[key]
        for candidate_unit in ([unit] if unit else list(cls.DEFAULT_UNITS)):
            if cls is FoxessDriver:
                driver = FoxessDriver(host, port, candidate_unit, timeout=timeout, read_attempts=2,
                                      register_map=register_map, read_function=read_function)
            else:
                driver = cls(host, port, candidate_unit, timeout=timeout, read_attempts=2)
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
                await driver.close()
    raise ConnectionError("Kein unterstütztes Gerät erkannt. Ist die Adresse richtig und Modbus TCP aktiviert?")


async def detect(host: str, port: int, unit: int = 0, *, timeout: float = DETECT_TIMEOUT_S,
                 only: str | None = None) -> DeviceInfo:
    """Returns the info of the first driver that recognises the device. Raises ConnectionError."""
    info, driver = await detect_driver(host, port, unit, timeout=timeout, only=only)
    await driver.close()
    return info


class AutoDriver:
    """Detects the device on first connect and then behaves exactly like the matching driver."""

    def __init__(self, host: str, port: int, unit: int = 0, *, timeout: float = 3.0, register_map: str = "auto",
                 read_function: str = "auto") -> None:
        self._args = dict(host=host, port=port, unit=unit, timeout=timeout, register_map=register_map,
                          read_function=read_function)
        self._driver: ModbusDevice | None = None

    async def connect(self) -> DeviceInfo:
        if self._driver is None:
            a = self._args
            # keep the connection that detected the device instead of opening a second one
            _info, self._driver = await detect_driver(a["host"], a["port"], a["unit"], timeout=a["timeout"],
                                                      register_map=a["register_map"],
                                                      read_function=a["read_function"])
        return await self._driver.connect()

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
