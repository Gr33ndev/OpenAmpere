# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""The OpenAmpere app of Home Assistant (#300): when it is uninstalled, a repair offers to remove the integration.

Home Assistant removes entries of uninstalled apps by itself only when their unique id is the id of the discovery;
ours is the installation id of OpenAmpere (kept when moving from Docker to the app). So the integration checks
whether its app is still installed: at the start and when the connection breaks.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.hassio import is_hassio

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

CONF_APP_SLUG = "app_slug"  # in the entry: connected to the Home Assistant app with this slug
ISSUE_PREFIX = "app_removed_"
APP_MARKER = ".installed-by-openampere-app"  # written by the app when it installed this integration


def integration_folder() -> Path:
    return Path(__file__).parent


def issue_id(entry_id: str) -> str:
    return f"{ISSUE_PREFIX}{entry_id}"


async def async_app_installed(hass: HomeAssistant, slug: str) -> bool | None:
    """Whether the app is installed; None if the Supervisor cannot tell."""
    from homeassistant.components.hassio import AddonError, AddonManager, AddonState

    try:
        info = await AddonManager(hass, _LOGGER, "OpenAmpere", slug).async_get_addon_info()
    except AddonError as err:
        _LOGGER.debug("could not ask the Supervisor for the OpenAmpere app: %s", err)
        return None
    return info.state is not AddonState.NOT_INSTALLED


async def async_check_app(hass: HomeAssistant, entry) -> None:
    """A repair while the app of the entry is uninstalled; gone again as soon as the app is back."""
    slug = entry.data.get(CONF_APP_SLUG)
    if not slug or not is_hassio(hass):
        return
    installed = await async_app_installed(hass, slug)
    if installed is None:
        return
    if installed:
        ir.async_delete_issue(hass, DOMAIN, issue_id(entry.entry_id))
        return
    ir.async_create_issue(hass, DOMAIN, issue_id(entry.entry_id), is_fixable=True, is_persistent=False,
                          severity=ir.IssueSeverity.WARNING, translation_key="app_removed",
                          translation_placeholders={"title": entry.title}, data={"entry_id": entry.entry_id})


async def async_remove(hass: HomeAssistant, entry_id: str) -> None:
    """The fix of the repair: remove the entry (with its devices and entities) and, if the app installed this
    integration and no other OpenAmpere is set up, its files. One from HACS stays."""
    if hass.config_entries.async_get_entry(entry_id) is not None:
        await hass.config_entries.async_remove(entry_id)
    folder = integration_folder()
    if hass.config_entries.async_entries(DOMAIN) or not (folder / APP_MARKER).is_file():
        return
    await hass.async_add_executor_job(shutil.rmtree, folder)
    _LOGGER.info("removed the OpenAmpere integration, the OpenAmpere app is no longer installed")
