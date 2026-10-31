# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Sensors: power, energy (for the energy dashboard), battery, temperatures, price, devices."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (SensorDeviceClass, SensorEntity, SensorEntityDescription,
                                             SensorStateClass)
from homeassistant.const import (PERCENTAGE, EntityCategory, UnitOfElectricCurrent, UnitOfElectricPotential,
                                 UnitOfEnergy, UnitOfPower, UnitOfTemperature, UnitOfTime)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import OpenAmpereConfigEntry
from .coordinator import OpenAmpereCoordinator, firmware
from .const import WALLBOX_STATES
from .entity import OpenAmpereEntity, WallboxReadingEntity


@dataclass(frozen=True, kw_only=True)
class OpenAmpereSensorDescription(SensorEntityDescription):
    value: Callable[[dict[str, Any]], Any]  # from the coordinator data
    # a reading of the inverter: unavailable while it is old, so automations do not act on frozen values (#220)
    from_inverter: bool = True


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
    # from the balance of the other counters, like the app (#346)
    OpenAmpereSensorDescription(key="consumption_energy", value=_total("consumption"), **ENERGY),
    # the inverter's own load counter, which can count much more than the house used: kept for existing history
    # (a sensor that drops to the balance would count as a meter reset and add the whole value once)
    OpenAmpereSensorDescription(key="load_energy", value=_total("load"), entity_registry_enabled_default=False,
                                entity_category=EntityCategory.DIAGNOSTIC, **ENERGY),
    OpenAmpereSensorDescription(key="grid_import_energy", value=_total("grid_import"), **ENERGY),
    OpenAmpereSensorDescription(key="grid_export_energy", value=_total("grid_export"), **ENERGY),
    OpenAmpereSensorDescription(key="battery_charge_energy", value=_total("battery_charge"), **ENERGY),
    OpenAmpereSensorDescription(key="battery_discharge_energy", value=_total("battery_discharge"), **ENERGY),
    OpenAmpereSensorDescription(key="price", value=lambda d: d.get("price_ct"), native_unit_of_measurement="ct/kWh",
                                suggested_display_precision=2, from_inverter=False),
    OpenAmpereSensorDescription(key="control", value=_control_state, device_class=SensorDeviceClass.ENUM,
                                options=["off", "test", "active"], entity_category=EntityCategory.DIAGNOSTIC,
                                from_inverter=False),
)


async def async_setup_entry(hass: HomeAssistant, entry: OpenAmpereConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [OpenAmpereSensor(coordinator, d) for d in SENSORS]
    entities += [PvInputSensor(coordinator, pv["index"], pv["name"]) for pv in coordinator.info["pv_inputs"]]
    if external := coordinator.info.get("pv_external"):  # a counted second PV inverter (#419)
        entities += [ExternalPvSensor(coordinator, d, external["name"]) for d in EXTERNAL_PV]
    for device in coordinator.info["devices"]:
        entities.append(DevicePowerSensor(coordinator, device["id"]))
        if device["kind"] == "heating_rod":
            entities.append(DeviceTemperatureSensor(coordinator, device["id"]))
    for wallbox in coordinator.info.get("wallboxes") or []:  # missing on older OpenAmpere versions
        entities += [WallboxPowerSensor(coordinator, wallbox["id"]), WallboxStateSensor(coordinator, wallbox["id"])]
        if not wallbox.get("read_only"):  # one OpenAmpere only shows does not tell the current set in it (#439)
            entities.append(WallboxCurrentSensor(coordinator, wallbox["id"]))
    entities.append(FirmwareSensor(coordinator))
    # Enphase (#538); remembered, so the integration sets up again when a token comes or goes (needs_reload)
    coordinator.info["token_sensor"] = ((coordinator.data or {}).get("status") or {}).get("token_days_left") is not None
    if coordinator.info["token_sensor"]:
        entities.append(TokenDaysSensor(coordinator))
    async_add_entities(entities)


class OpenAmpereSensor(OpenAmpereEntity, SensorEntity):
    entity_description: OpenAmpereSensorDescription

    def __init__(self, coordinator: OpenAmpereCoordinator, description: OpenAmpereSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        return super().available and not (self.entity_description.from_inverter and self.stale)

    @property
    def native_value(self) -> Any:
        return self.entity_description.value(self.data)


class FirmwareSensor(OpenAmpereEntity, SensorEntity):
    """Firmware of the inverter, e.g. for an automation when it changes with an update (#372)."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: OpenAmpereCoordinator) -> None:
        super().__init__(coordinator, "firmware")

    @property
    def native_value(self) -> str | None:
        return firmware(self.coordinator)


class TokenDaysSensor(OpenAmpereEntity, SensorEntity):
    """Days until the access token of an Enphase gateway runs out (#538; negative: since it ran out), e.g. for an
    automation that reminds the owner. Only days, never the date."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = UnitOfTime.DAYS

    def __init__(self, coordinator: OpenAmpereCoordinator) -> None:
        super().__init__(coordinator, "token_days_left")

    @property
    def native_value(self) -> int | None:
        return ((self.coordinator.data or {}).get("status") or {}).get("token_days_left")


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
    def available(self) -> bool:
        return super().available and not self.stale

    @property
    def native_value(self) -> float | None:
        inputs = self.live.get("pv_inputs") or []
        return inputs[self._index].get("power") if self._index < len(inputs) else None


# a second PV inverter OpenAmpere counts (#419): its power, and the energy OpenAmpere integrated from it, which is
# not in pv_energy (the inverter's counter); in the energy dashboard it is a second solar source
EXTERNAL_PV: tuple[OpenAmpereSensorDescription, ...] = (
    OpenAmpereSensorDescription(key="pv_external_power", value=_live("pv_external_power"), **POWER),
    OpenAmpereSensorDescription(key="pv_external_energy", value=_total("pv_external"), **ENERGY),
)


class ExternalPvSensor(OpenAmpereSensor):
    """Named like in OpenAmpere, e.g. "Garage"."""

    def __init__(self, coordinator: OpenAmpereCoordinator, description: OpenAmpereSensorDescription, name: str) -> None:
        super().__init__(coordinator, description)
        self._attr_translation_placeholders = {"name": name}


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


class WallboxPowerSensor(WallboxReadingEntity, SensorEntity):
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator: OpenAmpereCoordinator, wallbox_id: str) -> None:
        super().__init__(coordinator, "wallbox_power", wallbox_id)

    @property
    def native_value(self) -> float | None:
        return self.wallbox["power_w"] if self.wallbox else None


class WallboxStateSensor(WallboxReadingEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = WALLBOX_STATES

    def __init__(self, coordinator: OpenAmpereCoordinator, wallbox_id: str) -> None:
        super().__init__(coordinator, "wallbox_state", wallbox_id)

    @property
    def native_value(self) -> str | None:
        state = self.wallbox["state"] if self.wallbox else None
        return state if state in WALLBOX_STATES else "unknown" if state else None


class WallboxCurrentSensor(WallboxReadingEntity, SensorEntity):
    """The charging current set in the wallbox (what OpenAmpere or the owner set), not the one measured."""

    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator: OpenAmpereCoordinator, wallbox_id: str) -> None:
        super().__init__(coordinator, "wallbox_current", wallbox_id)

    @property
    def native_value(self) -> int | None:
        return self.wallbox["current_a"] if self.wallbox else None
