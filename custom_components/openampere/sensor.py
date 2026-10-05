"""Sensors: power, energy (for the energy dashboard), battery, temperatures, price, devices."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (SensorDeviceClass, SensorEntity, SensorEntityDescription,
                                             SensorStateClass)
from homeassistant.const import (PERCENTAGE, EntityCategory, UnitOfElectricCurrent, UnitOfElectricPotential,
                                 UnitOfEnergy, UnitOfPower, UnitOfTemperature)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import OpenAmpereConfigEntry
from .coordinator import OpenAmpereCoordinator
from .entity import OpenAmpereEntity


@dataclass(frozen=True, kw_only=True)
class OpenAmpereSensorDescription(SensorEntityDescription):
    value: Callable[[dict[str, Any]], Any]  # from the coordinator data


def _live(key: str) -> Callable[[dict], Any]:
    return lambda data: (data.get("live") or {}).get(key)


def _total(key: str) -> Callable[[dict], Any]:
    return lambda data: ((data.get("live") or {}).get("totals") or {}).get(key)


def _temperature(key: str) -> Callable[[dict], Any]:
    return lambda data: ((data.get("live") or {}).get("temperatures") or {}).get(key)


def _control_state(data: dict) -> str | None:
    control = (data.get("status") or {}).get("control")
    if not control:
        return None
    return "off" if not control["enabled"] else "test" if control["dry_run"] else "active"


POWER = dict(native_unit_of_measurement=UnitOfPower.WATT, device_class=SensorDeviceClass.POWER,
             state_class=SensorStateClass.MEASUREMENT, suggested_display_precision=0)
# lifetime counters of the inverter in Wh: what the energy dashboard needs
ENERGY = dict(native_unit_of_measurement=UnitOfEnergy.WATT_HOUR, suggested_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
              device_class=SensorDeviceClass.ENERGY, state_class=SensorStateClass.TOTAL_INCREASING,
              suggested_display_precision=1)
TEMPERATURE = dict(native_unit_of_measurement=UnitOfTemperature.CELSIUS, device_class=SensorDeviceClass.TEMPERATURE,
                   state_class=SensorStateClass.MEASUREMENT, suggested_display_precision=1)

SENSORS: tuple[OpenAmpereSensorDescription, ...] = (
    OpenAmpereSensorDescription(key="pv_power", value=_live("pv_power"), **POWER),
    OpenAmpereSensorDescription(key="house_power", value=_live("house_power"), **POWER),
    OpenAmpereSensorDescription(key="grid_power", value=_live("grid_power"), **POWER),  # + import, - export
    OpenAmpereSensorDescription(key="battery_power", value=_live("battery_power"), **POWER),  # + discharge, - charge
    OpenAmpereSensorDescription(key="battery_soc", value=_live("battery_soc"), native_unit_of_measurement=PERCENTAGE,
                                device_class=SensorDeviceClass.BATTERY, state_class=SensorStateClass.MEASUREMENT,
                                suggested_display_precision=0),
    OpenAmpereSensorDescription(key="battery_soh", value=_live("battery_soh"), native_unit_of_measurement=PERCENTAGE,
                                state_class=SensorStateClass.MEASUREMENT, entity_category=EntityCategory.DIAGNOSTIC,
                                suggested_display_precision=0),
    OpenAmpereSensorDescription(key="battery_voltage", value=_live("battery_voltage"),
                                native_unit_of_measurement=UnitOfElectricPotential.VOLT,
                                device_class=SensorDeviceClass.VOLTAGE, state_class=SensorStateClass.MEASUREMENT,
                                entity_category=EntityCategory.DIAGNOSTIC, suggested_display_precision=1),
    OpenAmpereSensorDescription(key="battery_current", value=_live("battery_current"),
                                native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
                                device_class=SensorDeviceClass.CURRENT, state_class=SensorStateClass.MEASUREMENT,
                                entity_category=EntityCategory.DIAGNOSTIC, suggested_display_precision=1),
    OpenAmpereSensorDescription(key="battery_temperature", value=_live("battery_temperature"), **TEMPERATURE),
    OpenAmpereSensorDescription(key="inverter_temperature", value=_temperature("inverter"),
                                entity_category=EntityCategory.DIAGNOSTIC, **TEMPERATURE),
    OpenAmpereSensorDescription(key="pv_energy", value=_total("pv"), **ENERGY),
    OpenAmpereSensorDescription(key="load_energy", value=_total("load"), **ENERGY),
    OpenAmpereSensorDescription(key="grid_import_energy", value=_total("grid_import"), **ENERGY),
    OpenAmpereSensorDescription(key="grid_export_energy", value=_total("grid_export"), **ENERGY),
    OpenAmpereSensorDescription(key="battery_charge_energy", value=_total("battery_charge"), **ENERGY),
    OpenAmpereSensorDescription(key="battery_discharge_energy", value=_total("battery_discharge"), **ENERGY),
    OpenAmpereSensorDescription(key="price", value=lambda d: d.get("price_ct"), native_unit_of_measurement="ct/kWh",
                                suggested_display_precision=2),
    OpenAmpereSensorDescription(key="control", value=_control_state, device_class=SensorDeviceClass.ENUM,
                                options=["off", "test", "active"], entity_category=EntityCategory.DIAGNOSTIC),
)


async def async_setup_entry(hass: HomeAssistant, entry: OpenAmpereConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [OpenAmpereSensor(coordinator, d) for d in SENSORS]
    entities += [PvInputSensor(coordinator, pv["index"], pv["name"]) for pv in coordinator.info["pv_inputs"]]
    for device in coordinator.info["devices"]:
        entities.append(DevicePowerSensor(coordinator, device["id"]))
        if device["kind"] == "heating_rod":
            entities.append(DeviceTemperatureSensor(coordinator, device["id"]))
    async_add_entities(entities)


class OpenAmpereSensor(OpenAmpereEntity, SensorEntity):
    entity_description: OpenAmpereSensorDescription

    def __init__(self, coordinator: OpenAmpereCoordinator, description: OpenAmpereSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value(self.data)


class PvInputSensor(OpenAmpereEntity, SensorEntity):
    """Power of one PV input (module array), named like in OpenAmpere."""

    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0
    _attr_translation_key = "pv_input_power"

    def __init__(self, coordinator: OpenAmpereCoordinator, index: int, name: str) -> None:
        super().__init__(coordinator, f"pv_input_{index}_power")
        self._index = index
        self._attr_translation_key = "pv_input_power"
        self._attr_translation_placeholders = {"name": name}

    @property
    def native_value(self) -> float | None:
        inputs = self.live.get("pv_inputs") or []
        return inputs[self._index].get("power") if self._index < len(inputs) else None


class DevicePowerSensor(OpenAmpereEntity, SensorEntity):
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator: OpenAmpereCoordinator, device_id: str) -> None:
        super().__init__(coordinator, "device_power", device_id)

    @property
    def native_value(self) -> float | None:
        return self.device["power_w"] if self.device else None


class DeviceTemperatureSensor(OpenAmpereEntity, SensorEntity):
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator: OpenAmpereCoordinator, device_id: str) -> None:
        super().__init__(coordinator, "device_temperature", device_id)

    @property
    def native_value(self) -> float | None:
        return self.device["temperature_c"] if self.device else None
