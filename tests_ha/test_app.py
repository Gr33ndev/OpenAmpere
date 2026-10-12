# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""The OpenAmpere app (#300): after uninstalling it, a repair removes the integration with one click."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.openampere import app
from custom_components.openampere.api import CannotConnect
from custom_components.openampere.const import CONF_FINGERPRINT, CONF_TOKEN, DOMAIN

from conftest import FINGERPRINT, INSTALLATION

APP = "abcd1234_openampere"


def app_entry(unique_id: str = INSTALLATION, slug: str | None = APP) -> MockConfigEntry:
    data = {"host": "abcd1234-openampere", "port": 8443, CONF_FINGERPRINT: FINGERPRINT, CONF_TOKEN: "oa_secret"}
    if slug:
        data[app.CONF_APP_SLUG] = slug
    return MockConfigEntry(domain=DOMAIN, unique_id=unique_id, title="OpenAmpere", data=data)


@pytest.fixture
def supervisor():
    """Home Assistant OS with a Supervisor that says whether the app is installed (True, False or None)."""
    installed = AsyncMock(return_value=False)
    with patch.object(app, "is_hassio", return_value=True), patch.object(app, "async_app_installed", installed):
        yield installed


def issues(hass: HomeAssistant) -> list[str]:
    return [issue_id for (domain, issue_id) in ir.async_get(hass).issues if domain == DOMAIN]


async def test_a_repair_appears_when_the_app_is_uninstalled_and_goes_when_it_is_back(hass, supervisor) -> None:
    entry = app_entry()
    entry.add_to_hass(hass)
    await app.async_check_app(hass, entry)
    assert issues(hass) == [app.issue_id(entry.entry_id)]
    assert supervisor.call_args.args[1] == APP
    supervisor.return_value = True  # installed again
    await app.async_check_app(hass, entry)
    assert issues(hass) == []


async def test_nothing_without_an_app_or_without_an_answer(hass, supervisor) -> None:
    docker = app_entry(slug=None)  # standalone with Docker (way 1): no app to ask for
    docker.add_to_hass(hass)
    await app.async_check_app(hass, docker)
    supervisor.assert_not_called()
    entry = app_entry(unique_id="other")
    entry.add_to_hass(hass)
    supervisor.return_value = None  # the Supervisor cannot tell
    await app.async_check_app(hass, entry)
    assert issues(hass) == []


async def test_without_home_assistant_os_nothing_is_asked(hass) -> None:
    entry = app_entry()
    entry.add_to_hass(hass)
    with patch.object(app, "is_hassio", return_value=False), patch.object(app, "async_app_installed") as installed:
        await app.async_check_app(hass, entry)
    installed.assert_not_called()


async def test_the_setup_notices_the_uninstalled_app(hass, supervisor) -> None:
    entry = app_entry()
    entry.add_to_hass(hass)
    with patch("custom_components.openampere.api.OpenAmpereClient.info", AsyncMock(side_effect=CannotConnect("gone"))):
        await hass.config_entries.async_setup(entry.entry_id)
    assert issues(hass) == [app.issue_id(entry.entry_id)]


async def set_up_without_server(hass: HomeAssistant, *entries: MockConfigEntry) -> None:
    """As in Home Assistant after the app is gone: the entries keep trying to connect, the integration is loaded."""
    with patch("custom_components.openampere.api.OpenAmpereClient.info", AsyncMock(side_effect=CannotConnect("gone"))):
        for entry in entries:
            entry.add_to_hass(hass)
            await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def fix(hass: HomeAssistant, entry_id: str) -> dict:
    """Click "Submit" in the repair, as in the dialog of Home Assistant."""
    from homeassistant.components.repairs import repairs_flow_manager
    assert await async_setup_component(hass, "repairs", {})
    manager = repairs_flow_manager(hass)
    result = await manager.async_init(DOMAIN, data={"issue_id": app.issue_id(entry_id)})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "confirm"
    return await manager.async_configure(result["flow_id"], {})


async def test_the_repair_removes_the_entry_and_the_files_the_app_installed(hass, supervisor, tmp_path) -> None:
    folder = tmp_path / "custom_components" / "openampere"
    folder.mkdir(parents=True)
    (folder / app.APP_MARKER).write_text("0.22.0\n")
    entry = app_entry()
    await set_up_without_server(hass, entry)
    with patch.object(app, "integration_folder", return_value=folder):
        result = await fix(hass, entry.entry_id)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert hass.config_entries.async_entries(DOMAIN) == [] and issues(hass) == []
    assert not folder.exists()


async def test_files_from_hacs_or_of_another_openampere_stay(hass, supervisor, tmp_path) -> None:
    folder = tmp_path / "openampere"
    folder.mkdir()
    entry = app_entry()
    await set_up_without_server(hass, entry)
    with patch.object(app, "integration_folder", return_value=folder):  # no marker: from HACS or by hand
        await fix(hass, entry.entry_id)
    assert folder.exists() and hass.config_entries.async_entries(DOMAIN) == []

    (folder / app.APP_MARKER).write_text("0.22.0\n")
    first, docker = app_entry(unique_id="a"), app_entry(unique_id="b", slug=None)  # another OpenAmpere with Docker
    await set_up_without_server(hass, first, docker)
    with patch.object(app, "integration_folder", return_value=folder):
        await fix(hass, first.entry_id)
    assert folder.exists() and hass.config_entries.async_entries(DOMAIN) == [docker]


async def test_removing_the_entry_by_hand_also_removes_the_repair(hass, supervisor) -> None:
    entry = app_entry()
    entry.add_to_hass(hass)
    await app.async_check_app(hass, entry)
    await hass.config_entries.async_remove(entry.entry_id)
    assert issues(hass) == []
