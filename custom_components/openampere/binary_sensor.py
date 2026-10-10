"""Yes/no states: connection to the inverter, power cut (off-grid), grid charging, alarm, devices on."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (BinarySensorDeviceClass, BinarySensorEntity,
                                                    BinarySensorEntityDescription)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import OpenAmpereConfigEntry
from .coordinator import OpenAmpereCoordinator
from .entity import OpenAmpereEntity


@dataclass(frozen=True, kw_only=True)
class OpenAmpereBinaryDescription(BinarySensorEntityDescription):
    value: Callable[[dict[str, Any]], bool | None]
    from_inverter: bool = False  # a reading of the inverter: unavailable while it is old (#220)


def _status(key: str) -> Callable[[dict], bool | None]:
    return lambda data: (data.get("status") or {}).get(key)


BINARY_SENSORS = (
    OpenAmpereBinaryDescription(key="inverter_connected", value=lambda d: bool((d.get("status") or {}).get("connected"))
                                and not (d.get("status") or {}).get("stale"),
                                device_class=BinarySensorDeviceClass.CONNECTIVITY, entity_category=EntityCategory.DIAGNOSTIC),
    OpenAmpereBinaryDescription(key="off_grid", value=_status("off_grid")),
    OpenAmpereBinaryDescription(key="grid_charging_active", value=_status("grid_charging"),
                                device_class=BinarySensorDeviceClass.RUNNING),
    OpenAmpereBinaryDescription(key="alarm", value=lambda d: bool((d.get("live") or {}).get("alarms")) if d.get("live") else None,
                                device_class=BinarySensorDeviceClass.PROBLEM, from_inverter=True),
)


async def async_setup_entry(hass: HomeAssistant, entry: OpenAmpereConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    entities: list[BinarySensorEntity] = [OpenAmpereBinarySensor(coordinator, d) for d in BINARY_SENSORS]
    entities.append(LiveConnectionSensor(coordinator))
    entities += [DeviceOnSensor(coordinator, d["id"]) for d in coordinator.info["devices"]]
    async_add_entities(entities)


class OpenAmpereBinarySensor(OpenAmpereEntity, BinarySensorEntity):
    entity_description: OpenAmpereBinaryDescription

    def __init__(self, coordinator: OpenAmpereCoordinator, description: OpenAmpereBinaryDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        return super().available and not (self.entity_description.from_inverter and self.stale)

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value(self.data)

    @property
    def extra_state_attributes(self) -> dict | None:
        if self.entity_description.key == "alarm":
            return {"codes": self.live.get("alarms") or []}
        return None


class LiveConnectionSensor(OpenAmpereEntity, BinarySensorEntity):
    """Whether live values arrive by push (otherwise only the minute updates)."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: OpenAmpereCoordinator) -> None:
        super().__init__(coordinator, "live_connection")

    @property
    def is_on(self) -> bool:
        return self.coordinator.live_connected


class DeviceOnSensor(OpenAmpereEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(self, coordinator: OpenAmpereCoordinator, device_id: str) -> None:
        super().__init__(coordinator, "device_on", device_id)

    @property
    def is_on(self) -> bool | None:
        return bool(self.device["on"]) if self.device else None
