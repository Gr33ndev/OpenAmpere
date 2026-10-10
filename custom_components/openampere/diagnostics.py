# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Diagnostics download: without token, address and certificate."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from . import OpenAmpereConfigEntry
from .const import CONF_FINGERPRINT, CONF_TOKEN

REDACT = {CONF_HOST, CONF_TOKEN, CONF_FINGERPRINT, "installation_id"}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: OpenAmpereConfigEntry) -> dict[str, Any]:
    coordinator = entry.runtime_data
    return {
        "entry": async_redact_data(dict(entry.data), REDACT),
        "options": dict(entry.options),
        "info": async_redact_data(coordinator.info, REDACT),
        "live_connected": coordinator.live_connected,
        "data": coordinator.data,
    }
