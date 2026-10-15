# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Numbers: backup reserve, charge limit, outage floor of the battery, the reserve of the backup work mode (only where
the device keeps its own, SAJ, #380) and the target of grid charging (all %); the highest charging current of the
current mode of each wallbox OpenAmpere controls (A, #409)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.number import NumberEntity, NumberEntityDescription, NumberMode
from homeassistant.const import PERCENTAGE, UnitOfElectricCurrent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import OpenAmpereConfigEntry
from .coordinator import OpenAmpereCoordinator
from .const import WALLBOX_CHARGING_MODES
from .entity import ControlEntity, WallboxEntity
from .helpers import send


@dataclass(frozen=True, kw_only=True)
class SocDescription(NumberEntityDescription):
    # limits that depend on the other values (the server checks them as well): data -> (min, max)
    bounds: Callable[[dict], tuple[int, int]]


def _settings(data: dict) -> dict:
    return data.get("battery_settings") or {}


def _value(data: dict, key: str, default: int) -> int:
    """A limit of the battery, the default only when it is unknown (0 % is a real value, #69, #313)."""
    value = _settings(data).get(key)
    return default if value is None else value


SOC_NUMBERS = (
    # backup reserve: between the outage floor and below the charge limit, at least 10 %; below 10 % only in
    # OpenAmpere itself, after a warning (#313)
    SocDescription(key="min_soc_on_grid", bounds=lambda d: (max(10, _value(d, "min_soc", 10)),
                                                           min(99, _value(d, "max_soc", 100) - 1))),
    SocDescription(key="max_soc", bounds=lambda d: (max(20, _value(d, "min_soc_on_grid", 10) + 1), 100)),
    # outage floor: down to 0 % (#69), at most the backup reserve
    SocDescription(key="min_soc", bounds=lambda d: (0, _value(d, "min_soc_on_grid", 100))),
)
# the reserve the backup work mode keeps: at least 10 %, at most the charge limit (#380)
BACKUP_SOC = SocDescription(key="backup_soc", bounds=lambda d: (10, _value(d, "max_soc", 100)))


async def async_setup_entry(hass: HomeAssistant, entry: OpenAmpereConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    if not coordinator.can_control:
        return
    numbers = [*SOC_NUMBERS, *([BACKUP_SOC] if (coordinator.info.get("device") or {}).get("backup_reserve") else [])]
    async_add_entities([*(SocNumber(coordinator, d) for d in numbers), GridChargingTarget(coordinator),
                        *(WallboxModeCurrent(coordinator, w["id"]) for w in coordinator.info.get("wallboxes") or [])])


class SocNumber(ControlEntity, NumberEntity):
    entity_description: SocDescription
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator: OpenAmpereCoordinator, description: SocDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_min_value(self) -> float:
        return self.entity_description.bounds(self.data)[0]

    @property
    def native_max_value(self) -> float:
        low, high = self.entity_description.bounds(self.data)
        return max(low, high)

    @property
    def native_value(self) -> float | None:
        return _settings(self.data).get(self.entity_description.key)

    async def async_set_native_value(self, value: float) -> None:
        await send(self.coordinator, self.coordinator.client.set_battery({self.entity_description.key: int(value)}))


class GridChargingTarget(ControlEntity, NumberEntity):
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_native_min_value = 20
    _attr_native_max_value = 100
    _attr_native_step = 5
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator: OpenAmpereCoordinator) -> None:
        super().__init__(coordinator, "grid_charging_target")

    @property
    def native_value(self) -> float | None:
        return (self.data.get("grid_charging") or {}).get("target_soc")

    async def async_set_native_value(self, value: float) -> None:
        await send(self.coordinator, self.coordinator.client.set_grid_charging({"target_soc": int(value)}))


class WallboxModeCurrent(WallboxEntity, ControlEntity, NumberEntity):
    """The highest charging current of the wallbox's current mode (#402): sent together with that mode, checked by
    OpenAmpere against the owner's limit. Only in a charging mode; an older OpenAmpere does not send it."""

    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_native_min_value = 6
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator: OpenAmpereCoordinator, wallbox_id: str) -> None:
        super().__init__(coordinator, "wallbox_mode_current", wallbox_id)

    @property
    def available(self) -> bool:
        return (super().available and self.wallbox["mode"] in WALLBOX_CHARGING_MODES
                and self.wallbox.get("mode_current_a") is not None)

    @property
    def native_max_value(self) -> float:
        return max(6, (self.wallbox or {}).get("max_current_a") or 6)

    @property
    def native_value(self) -> float | None:
        wallbox = self.wallbox or {}
        return (wallbox.get("mode_current_a") or {}).get(wallbox.get("mode"))

    async def async_set_native_value(self, value: float) -> None:
        mode = self.wallbox["mode"]
        await send(self.coordinator, self.coordinator.client.set_wallbox_mode(self._wallbox_id, mode, int(value)))
