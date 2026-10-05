"""Numbers: backup reserve, charge limit, outage floor of the battery and the target of grid charging (all %)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.number import NumberEntity, NumberEntityDescription, NumberMode
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import OpenAmpereConfigEntry
from .coordinator import OpenAmpereCoordinator
from .entity import ControlEntity
from .helpers import send


@dataclass(frozen=True, kw_only=True)
class SocDescription(NumberEntityDescription):
    # limits that depend on the other values (the server checks them as well): data -> (min, max)
    bounds: Callable[[dict], tuple[int, int]]


def _settings(data: dict) -> dict:
    return data.get("battery_settings") or {}


SOC_NUMBERS = (
    # backup reserve: between the outage floor and below the charge limit, at least 10 %
    SocDescription(key="min_soc_on_grid", bounds=lambda d: (max(10, _settings(d).get("min_soc") or 10),
                                                           min(99, (_settings(d).get("max_soc") or 100) - 1))),
    SocDescription(key="max_soc", bounds=lambda d: (max(20, (_settings(d).get("min_soc_on_grid") or 10) + 1), 100)),
    # outage floor: down to 0 % (#69), at most the backup reserve
    SocDescription(key="min_soc", bounds=lambda d: (0, _settings(d).get("min_soc_on_grid") or 100)),
)


async def async_setup_entry(hass: HomeAssistant, entry: OpenAmpereConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    if not coordinator.can_control:
        return
    async_add_entities([*(SocNumber(coordinator, d) for d in SOC_NUMBERS), GridChargingTarget(coordinator)])


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
