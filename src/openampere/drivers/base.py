"""Driver-independent data model and inverter driver interface."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Protocol


def raise_if_cancelled() -> None:
    """pymodbus turns a cancelled request into an ordinary Modbus error. Call this in error handlers
    that retry or continue, so a pending task cancellation (shutdown, reconfiguration) is not lost."""
    task = asyncio.current_task()
    if task is not None and task.cancelling():
        raise asyncio.CancelledError


class WorkMode(str, Enum):
    SELF_USE = "self_use"
    FEED_IN_FIRST = "feed_in_first"
    BACKUP = "backup"
    PEAK_SHAVING = "peak_shaving"


@dataclass
class DeviceInfo:
    manufacturer: str
    model: str
    serial: str | None = None
    firmware: str | None = None
    register_map: str | None = None
    driver: str | None = None  # driver key, e.g. "foxess", "saj"
    unit: int | None = None  # Modbus unit id the device answered on
    rated_power_w: int | None = None
    supports_control: bool = False  # writing settings is implemented and tested for this device


@dataclass
class EnergyCounters:
    """Monotonic lifetime counters in Wh. None if the device does not provide them."""

    pv: float | None = None
    load: float | None = None
    grid_import: float | None = None
    grid_export: float | None = None
    battery_charge: float | None = None
    battery_discharge: float | None = None


@dataclass
class PvInput:
    """One PV input of the inverter (MPPT tracker), usually one module array (roof side, garage, carport ...)."""

    power: float | None = None  # W
    voltage: float | None = None  # V
    current: float | None = None  # A


# Keys of Snapshot.temperatures (all °C); drivers fill what their device provides.
TEMPERATURE_KEYS = ("inverter", "ambient", "battery", "battery_cell_max", "battery_cell_min",
                    "battery2", "battery2_cell_max", "battery2_cell_min")


@dataclass
class Snapshot:
    """One reading of the energy system.

    Sign conventions (all W):
      pv_power       >= 0
      house_power    >= 0 (consumption)
      grid_power     + = import from grid, - = export to grid
      battery_power  + = discharging,      - = charging
    """

    timestamp: float
    pv_power: float | None = None
    house_power: float | None = None
    grid_power: float | None = None
    battery_power: float | None = None
    battery_soc: float | None = None
    pv_inputs: list[PvInput] = field(default_factory=list)
    temperatures: dict[str, float] = field(default_factory=dict)
    battery_voltage: float | None = None
    battery_current: float | None = None
    battery_temperature: float | None = None
    battery_soh: float | None = None
    inverter_state: int | None = None
    off_grid: bool | None = None
    alarms: list[int] = field(default_factory=list)
    totals: EnergyCounters = field(default_factory=EnergyCounters)
    today: EnergyCounters = field(default_factory=EnergyCounters)

    def to_dict(self) -> dict:
        return asdict(self)

    def sanitize(self) -> Snapshot:
        """Drops values that cannot be real: sentinels for "not available" (0x7FFF, 0x8000, 0xFFFF) and
        readings outside the physical range. A wrong value is worse than a missing one."""
        def ok(value, lo, hi):
            if value is None or value in SENTINELS or not lo <= value <= hi:
                return None
            return value
        self.battery_soc = ok(self.battery_soc, 0, 100)
        self.battery_soh = ok(self.battery_soh, 0, 100)
        self.battery_temperature = ok(self.battery_temperature, -40, 90)
        self.temperatures = {k: v for k, v in self.temperatures.items() if ok(v, -40, 120) is not None}
        for name in ("pv_power", "house_power"):
            setattr(self, name, ok(getattr(self, name), 0, MAX_PLAUSIBLE_W))
        for name in ("grid_power", "battery_power"):
            setattr(self, name, ok(getattr(self, name), -MAX_PLAUSIBLE_W, MAX_PLAUSIBLE_W))
        for pv in self.pv_inputs:
            pv.power = ok(pv.power, 0, MAX_PLAUSIBLE_W)
        return self


SENTINELS = {32767, -32768, 65535, 3276.7, -3276.8, 6553.5, 327.67, -327.68}
MAX_PLAUSIBLE_W = 100_000


@dataclass
class ExportLimit:
    supported: bool
    limit_w: int | None = None  # current feed-in limit
    rated_power_w: int | None = None  # nominal inverter power (100 %)


@dataclass
class BatterySettings:
    work_mode: WorkMode | None = None
    min_soc: int | None = None
    max_soc: int | None = None
    min_soc_on_grid: int | None = None


class InverterDriver(Protocol):
    async def connect(self) -> DeviceInfo: ...

    async def close(self) -> None: ...

    async def read(self) -> Snapshot: ...

    async def read_settings(self) -> BatterySettings: ...

    async def write_work_mode(self, mode: WorkMode) -> None: ...

    async def write_soc_limits(self, *, min_soc: int | None = None, max_soc: int | None = None,
                               min_soc_on_grid: int | None = None) -> None: ...

    async def set_remote_power(self, watts: int, timeout_s: int) -> None:
        """Force battery power (+ discharge, - charge) until timeout or release."""
        ...

    async def release_remote_power(self) -> None: ...

    async def read_export_limit(self) -> ExportLimit: ...

    async def write_export_limit(self, watts: int) -> None: ...
