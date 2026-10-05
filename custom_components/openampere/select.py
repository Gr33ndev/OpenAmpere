"""Choices: work mode of the battery, mode of each heating rod or switch (automatic, off, boost)."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import OpenAmpereConfigEntry
from .const import DEVICE_MODES, WORK_MODES
from .coordinator import OpenAmpereCoordinator
from .entity import ControlEntity
from .helpers import send


async def async_setup_entry(hass: HomeAssistant, entry: OpenAmpereConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    if not coordinator.can_control:
        return  # read-only access
    entities: list[SelectEntity] = [WorkModeSelect(coordinator)]
    entities += [DeviceModeSelect(coordinator, d["id"]) for d in coordinator.info["devices"]]
    async_add_entities(entities)


class WorkModeSelect(ControlEntity, SelectEntity):
    _attr_options = WORK_MODES

    def __init__(self, coordinator: OpenAmpereCoordinator) -> None:
        super().__init__(coordinator, "work_mode")

    @property
    def current_option(self) -> str | None:
        return (self.data.get("battery_settings") or {}).get("work_mode")

    async def async_select_option(self, option: str) -> None:
        await send(self.coordinator, self.coordinator.client.set_battery({"work_mode": option}))


class DeviceModeSelect(ControlEntity, SelectEntity):
    _attr_options = DEVICE_MODES

    def __init__(self, coordinator: OpenAmpereCoordinator, device_id: str) -> None:
        super().__init__(coordinator, "device_mode", device_id)

    @property
    def current_option(self) -> str | None:
        return self.device["mode"] if self.device else None

    async def async_select_option(self, option: str) -> None:
        await send(self.coordinator, self.coordinator.client.set_device_mode(self._device_id, option))
