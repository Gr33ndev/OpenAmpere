# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Driver-independent data model and inverter driver interface."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
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
    # smallest step of the lifetime energy counters in Wh according to the register map (100 = 0.1 kWh, #194);
    # None if unknown. Firmware may count coarser than its unit, the recorded data can show that (Storage).
    energy_step_wh: int | None = None
    work_modes: tuple[str, ...] | None = None  # the work modes the device offers; None: all of WorkMode
    # grid charging through a time slot of the device instead of a remote control with a timeout (SAJ, #377): it
    # keeps charging this many minutes at most after OpenAmpere stops; None: remote control with a timeout
    charging_continues_min: int | None = None
    # the backup work mode keeps its own reserve (SAJ 0x3651, #380) instead of the reserve of normal operation
    backup_reserve: bool = False
    # the rated power the device reports when it does not fit its type (SAJ, #492): an export limit, converted with it,
    # is written only after the owner confirmed the rated power from the type plate (confirm_rated_power)
    rated_power_unchecked_w: int | None = None
    rated_power_confirmed: bool = False

    def confirm_rated_power(self, reported_w: int, confirmed_w: int) -> None:
        """The owner's rated power replaces an unchecked one while the device still reports the value it was
        confirmed for; otherwise the reported one counts again, unconfirmed."""
        if self.rated_power_unchecked_w is None:
            return
        self.rated_power_confirmed = bool(confirmed_w) and reported_w == self.rated_power_unchecked_w
        self.rated_power_w = confirmed_w if self.rated_power_confirmed else self.rated_power_unchecked_w


@dataclass
class EnergyCounters:
    """Monotonic lifetime counters in Wh. None if the device does not provide them."""

    pv: float | None = None
    load: float | None = None  # the inverter's own load counter: it can count much more than the house used (#346)
    grid_import: float | None = None
    grid_export: float | None = None
    battery_charge: float | None = None
    battery_discharge: float | None = None
    consumption: float | None = None  # from the balance of the other counters (Snapshot.sanitize), what the app shows
    # a counted second PV inverter (#419): not a counter of the device but OpenAmpere's integral of its power (only in
    # totals, set by the collector); not part of pv or consumption
    pv_external: float | None = None


# in and out of the house: what came from the roof, the grid and the battery minus what went to the grid and the battery
BALANCE = {"pv": 1, "grid_import": 1, "battery_discharge": 1, "grid_export": -1, "battery_charge": -1}


def energy_balance(flows: Mapping[str, float | None]) -> float | None:
    """What the house consumed, conversion losses included, from the other energy flows; None if one is missing.
    The inverter's load counter can count much more than that (#346), the balance cannot be wrong while the others
    are right."""
    values = [flows.get(f) for f in BALANCE]
    if any(v is None for v in values):
        return None
    return sum(sign * v for sign, v in zip(BALANCE.values(), values))


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
      pv_power       >= 0 (with an extra PV inverter, if the owner counts it: pv_external_power is part of it)
      pv_external_power >= 0 (an extra PV inverter the device measures with a meter, e.g. SAJ Meter B, #419)
      house_power    >= 0 (consumption)
      grid_power     + = import from grid, - = export to grid
      battery_power  + = discharging,      - = charging
    """

    timestamp: float
    pv_power: float | None = None
    pv_external_power: float | None = None
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
        for name in ("pv_power", "house_power", "pv_external_power"):
            setattr(self, name, ok(getattr(self, name), 0, MAX_PLAUSIBLE_W))
        for name in ("grid_power", "battery_power"):
            setattr(self, name, ok(getattr(self, name), -MAX_PLAUSIBLE_W, MAX_PLAUSIBLE_W))
        for pv in self.pv_inputs:
            pv.power = ok(pv.power, 0, MAX_PLAUSIBLE_W)
        for counters in (self.totals, self.today):
            balance = energy_balance(asdict(counters))
            counters.consumption = None if balance is None else max(0.0, balance)
        return self

    def count_external_pv(self, counted: bool) -> Snapshot:
        """Adds the extra PV inverter to pv_power when the owner switched it on (#419), otherwise drops it. It is not
        part of the inverter's own PV power: without it the solar power, and the house in the energy balance, are too
        low."""
        if not counted:
            self.pv_external_power = None
        elif self.pv_external_power is not None and self.pv_power is not None:
            self.pv_power += self.pv_external_power
        return self


SENTINELS = {32767, -32768, 65535, 3276.7, -3276.8, 6553.5, 327.67, -327.68}
MAX_PLAUSIBLE_W = 100_000


@dataclass
class ExportLimit:
    supported: bool
    limit_w: int | None = None  # current feed-in limit
    rated_power_w: int | None = None  # nominal inverter power (100 %)
    step_w: int = 1  # resolution: a limit is written rounded down to it (SAJ: per mille of the rated power)


@dataclass
class BatterySettings:
    work_mode: WorkMode | None = None
    min_soc: int | None = None
    max_soc: int | None = None
    min_soc_on_grid: int | None = None
    backup_soc: int | None = None  # reserve of the backup work mode, if the device has its own (SAJ 0x3651, #380)


class InverterDriver(Protocol):
    async def connect(self) -> DeviceInfo: ...

    async def close(self) -> None: ...

    async def read(self) -> Snapshot: ...

    async def read_settings(self) -> BatterySettings: ...

    async def write_work_mode(self, mode: WorkMode) -> None: ...

    async def write_soc_limits(self, *, min_soc: int | None = None, max_soc: int | None = None,
                               min_soc_on_grid: int | None = None) -> None: ...

    # only a device with DeviceInfo.backup_reserve: async def write_backup_soc(self, value: int) -> None

    async def set_remote_power(self, watts: int, timeout_s: int, restore: dict | None = None) -> dict | None:
        """Force battery power (+ discharge, - charge) until timeout or release. A driver that changes stored settings
        for it (SAJ) returns what it needs to put them back; the caller keeps it and passes it in again as restore,
        also after a restart."""
        ...

    async def release_remote_power(self, timeout_s: int | None = None, restore: dict | None = None) -> bool:
        """End remote control if OpenAmpere started it (or it still shows OpenAmpere's timeout_s); True if ended."""
        ...

    # only a driver whose set_remote_power returns something: def remote_restore(self) -> dict | None, what a release
    # needs now, also after a command that failed half way; async def adopt_remote_restore(self, restore: dict), takes
    # over what was kept after a restart while OpenAmpere still controls the device

    async def read_export_limit(self) -> ExportLimit: ...

    async def write_export_limit(self, watts: int) -> None: ...
