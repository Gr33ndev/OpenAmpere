# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Base entity: one device for the system, one for each heating rod or switch and each wallbox OpenAmpere
controls."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import OpenAmpereCoordinator


def controllable_wallboxes(info: dict[str, Any]) -> list[dict[str, Any]]:
    """Wallboxes with a charging mode to choose: not the ones OpenAmpere only shows (#439, "read_only"; missing on
    older OpenAmpere versions, which only had wallboxes it controls)."""
    return [w for w in info.get("wallboxes") or [] if not w.get("read_only")]


class OpenAmpereEntity(CoordinatorEntity[OpenAmpereCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: OpenAmpereCoordinator, key: str, device_id: str | None = None) -> None:
        super().__init__(coordinator)
        info = coordinator.info
        installation = info["installation_id"]
        self._device_id = device_id
        self._attr_unique_id = f"{installation}_{device_id}_{key}" if device_id else f"{installation}_{key}"
        self._attr_translation_key = key
        if device_id is None:
            device = info.get("device") or {}
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, installation)}, name="OpenAmpere",
                manufacturer=device.get("manufacturer") or "OpenAmpere", model=device.get("model"),
                hw_version=device.get("firmware"), sw_version=info.get("version"),
                configuration_url=f"http://{coordinator.client.connection.host}:{info['web_port']}"
                if info.get("web_port") else None,
            )
        else:
            name = next((d["name"] for d in info["devices"] if d["id"] == device_id), device_id)
            self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, f"{installation}_{device_id}")}, name=name,
                                                manufacturer="OpenAmpere", via_device=(DOMAIN, installation))

    @property
    def data(self) -> dict[str, Any]:
        return self.coordinator.data or {}

    @property
    def live(self) -> dict[str, Any]:
        return self.data.get("live") or {}

    @property
    def stale(self) -> bool:
        """OpenAmpere has no current reading from the inverter (connection lost): the last values are old."""
        return bool((self.data.get("status") or {}).get("stale"))

    @property
    def device(self) -> dict[str, Any] | None:
        return next((d for d in self.data.get("devices") or [] if d["id"] == self._device_id), None)

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        return self._device_id is None or self.device is not None


class WallboxEntity(OpenAmpereEntity):
    """A wallbox OpenAmpere reads and controls itself (#388): its own device, ids apart from the other devices."""

    def __init__(self, coordinator: OpenAmpereCoordinator, key: str, wallbox_id: str) -> None:
        super().__init__(coordinator, key)
        installation = coordinator.info["installation_id"]
        self._wallbox_id = wallbox_id
        self._attr_unique_id = f"{installation}_wallbox_{wallbox_id}_{key}"
        wallbox = next((w for w in coordinator.info.get("wallboxes") or [] if w["id"] == wallbox_id), {})
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, f"{installation}_wallbox_{wallbox_id}")},
                                            name=wallbox.get("name") or wallbox_id, manufacturer="OpenAmpere",
                                            model=wallbox.get("model"), via_device=(DOMAIN, installation))

    @property
    def wallbox(self) -> dict[str, Any] | None:
        return next((w for w in self.data.get("wallboxes") or [] if w["id"] == self._wallbox_id), None)

    @property
    def available(self) -> bool:
        return super().available and self.wallbox is not None


class WallboxReadingEntity(WallboxEntity):
    """A value read from the wallbox: unavailable while it does not answer (a portable wallbox is often unplugged),
    never an old value."""

    @property
    def available(self) -> bool:
        return super().available and bool(self.wallbox["online"]) and self.wallbox.get("state") is not None


def inverter_list(live: dict | None) -> list[dict[str, str]]:
    """The inverters of an installation with more than one (#619): id and name, in their order; empty with one
    inverter and on older OpenAmpere versions."""
    return [{"id": i["id"], "name": i.get("name") or ""} for i in (live or {}).get("inverters") or []]


class InverterEntity(OpenAmpereEntity):
    """One inverter of an installation with more than one (#619): its own device, named like in OpenAmpere. A value
    of it is unavailable while OpenAmpere has no current reading of it."""

    def __init__(self, coordinator: OpenAmpereCoordinator, key: str, inverter_id: str) -> None:
        super().__init__(coordinator, key)
        installation = coordinator.info["installation_id"]
        self._inverter_id = inverter_id
        self._attr_unique_id = f"{installation}_inverter_{inverter_id}_{key}"
        inverters = coordinator.info.get("inverters") or []
        index = next((n for n, i in enumerate(inverters) if i["id"] == inverter_id), 0)
        name = inverters[index]["name"] if index < len(inverters) else ""
        named = {"name": name} if name else {"translation_key": "inverter",
                                             "translation_placeholders": {"number": str(index + 1)}}
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, f"{installation}_inverter_{inverter_id}")},
                                            manufacturer="OpenAmpere", via_device=(DOMAIN, installation), **named)

    @property
    def reading(self) -> dict[str, Any] | None:
        return next((i for i in self.live.get("inverters") or [] if i.get("id") == self._inverter_id), None)

    @property
    def available(self) -> bool:
        reading = self.reading
        return super().available and not self.stale and reading is not None and bool(reading.get("connected"))


class ControlEntity(OpenAmpereEntity):
    """Changes something: only while the control switch in OpenAmpere is on."""

    @property
    def available(self) -> bool:
        status = self.data.get("status") or {}
        return super().available and bool((status.get("control") or {}).get("enabled")) and bool(status.get("connected"))
