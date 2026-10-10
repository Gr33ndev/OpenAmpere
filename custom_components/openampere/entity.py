# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Base entity: one device for the system, one for each heating rod or switch OpenAmpere controls."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import OpenAmpereCoordinator


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


class ControlEntity(OpenAmpereEntity):
    """Changes something: only while the control switch in OpenAmpere is on."""

    @property
    def available(self) -> bool:
        status = self.data.get("status") or {}
        return super().available and bool((status.get("control") or {}).get("enabled")) and bool(status.get("connected"))
