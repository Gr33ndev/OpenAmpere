# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""OpenAmpere: data and control of a home battery system, through a running OpenAmpere server.

The integration never talks to the inverter itself. OpenAmpere stays its only Modbus client and checks every command
(control switch, test mode, limits) before it reaches the device. https://github.com/Gr33ndev/OpenAmpere
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import CannotConnect, CertificateMismatch, Connection, InvalidAuth, OpenAmpereClient, OpenAmpereError
from .const import API_VERSION, CONF_FINGERPRINT, CONF_TOKEN
from .coordinator import OpenAmpereCoordinator

PLATFORMS = [Platform.BINARY_SENSOR, Platform.NUMBER, Platform.SELECT, Platform.SENSOR, Platform.SWITCH]

type OpenAmpereConfigEntry = ConfigEntry[OpenAmpereCoordinator]


def client_for(hass: HomeAssistant, data: dict) -> OpenAmpereClient:
    connection = Connection(data[CONF_HOST], data[CONF_PORT], data[CONF_FINGERPRINT], data[CONF_TOKEN])
    return OpenAmpereClient(async_get_clientsession(hass), connection)


async def async_setup_entry(hass: HomeAssistant, entry: OpenAmpereConfigEntry) -> bool:
    client = client_for(hass, dict(entry.data))
    try:
        info = await client.info()
    except (InvalidAuth, CertificateMismatch) as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except (CannotConnect, OpenAmpereError) as err:
        raise ConfigEntryNotReady(str(err)) from err
    if info.get("api_version") != API_VERSION:
        raise ConfigEntryNotReady("OpenAmpere und die Integration passen nicht zusammen. Bitte beide aktualisieren.")
    coordinator = OpenAmpereCoordinator(hass, entry, client, info)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    coordinator.start_listening()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: OpenAmpereConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
