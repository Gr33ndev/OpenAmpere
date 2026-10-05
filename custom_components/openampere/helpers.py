"""Sending a command: OpenAmpere's German message becomes the error shown in Home Assistant."""

from __future__ import annotations

from collections.abc import Awaitable

from homeassistant.exceptions import HomeAssistantError

from .api import OpenAmpereError
from .coordinator import OpenAmpereCoordinator


async def send(coordinator: OpenAmpereCoordinator, command: Awaitable[dict]) -> dict:
    try:
        result = await command
    except OpenAmpereError as err:
        raise HomeAssistantError(f"OpenAmpere: {err}") from err
    await coordinator.async_request_refresh()  # read back what the inverter holds now
    return result
