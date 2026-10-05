"""Switch: charge the battery from the grid when it is cheap (set up once in OpenAmpere)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import OpenAmpereConfigEntry
from .coordinator import OpenAmpereCoordinator
from .entity import ControlEntity
from .helpers import send


async def async_setup_entry(hass: HomeAssistant, entry: OpenAmpereConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    if coordinator.can_control:
        async_add_entities([GridChargingSwitch(coordinator)])


class GridChargingSwitch(ControlEntity, SwitchEntity):
    def __init__(self, coordinator: OpenAmpereCoordinator) -> None:
        super().__init__(coordinator, "grid_charging")

    @property
    def is_on(self) -> bool | None:
        return (self.data.get("grid_charging") or {}).get("enabled")

    async def async_turn_on(self, **kwargs: Any) -> None:
        await send(self.coordinator, self.coordinator.client.set_grid_charging({"enabled": True}))

    async def async_turn_off(self, **kwargs: Any) -> None:
        await send(self.coordinator, self.coordinator.client.set_grid_charging({"enabled": False}))
