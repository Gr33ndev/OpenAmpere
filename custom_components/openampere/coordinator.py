"""Keeps the data of one OpenAmpere server: live values by push, the rest every minute."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import CannotConnect, CertificateMismatch, InvalidAuth, OpenAmpereClient, OpenAmpereError
from .const import CONF_MIN_INTERVAL, DEFAULT_MIN_INTERVAL_S, DOMAIN, STATE_INTERVAL_S

_LOGGER = logging.getLogger(__name__)


class OpenAmpereCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: OpenAmpereClient, info: dict) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN,
                         update_interval=timedelta(seconds=STATE_INTERVAL_S))
        self.client = client
        self.info = info
        self.live_connected = False
        self._last_push = 0.0
        self._listener: asyncio.Task | None = None
        self._reload_scheduled = False

    @property
    def can_control(self) -> bool:
        return self.info["token"]["scope"] == "control"

    def async_update_listeners(self) -> None:
        super().async_update_listeners()
        if not self._reload_scheduled and self.config_entry and needs_reload(self.info, (self.data or {}).get("live")):
            self._reload_scheduled = True
            self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            state = await self.client.state()
        except (InvalidAuth, CertificateMismatch) as err:
            # revoked token or a different server/certificate: ask for a new connection code
            raise ConfigEntryAuthFailed(str(err)) from err
        except (CannotConnect, OpenAmpereError) as err:
            raise UpdateFailed(str(err)) from err
        return {**(self.data or {}), **state}

    def start_listening(self) -> None:
        self._listener = self.config_entry.async_create_background_task(
            self.hass, self.client.listen(self._on_push, self._on_live_connection), f"{DOMAIN} live values")

    def _on_push(self, message: dict) -> None:
        if message.get("type") != "live" or self.data is None:
            return
        min_interval = self.config_entry.options.get(CONF_MIN_INTERVAL, DEFAULT_MIN_INTERVAL_S)
        now = time.monotonic()
        if min_interval and now - self._last_push < min_interval:
            return  # fewer values for small systems (e.g. a Raspberry Pi with an SD card)
        self._last_push = now
        # not async_set_updated_data: that would restart the minute timer on every push and the settings would
        # never be fetched again
        self.data = {**self.data, **{k: message[k] for k in ("live", "status", "devices") if k in message}}
        self.async_update_listeners()

    def _on_live_connection(self, connected: bool) -> None:
        if connected != self.live_connected:
            self.live_connected = connected
            if self.data is not None:
                self.async_update_listeners()


def needs_reload(info: dict, live: dict | None) -> bool:
    """Set up before OpenAmpere had its first reading (e.g. everything starting after a power cut): the device and
    the PV inputs were unknown, so set up again once they are there (#220)."""
    if not live:
        return False
    if info["device"] is None:
        return True
    count = info.get("pv_input_count")  # all inputs, also hidden ones; missing on older OpenAmpere versions
    return count is not None and len(live.get("pv_inputs") or []) != count
