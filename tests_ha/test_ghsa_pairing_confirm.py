# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""GHSA-8f28-gpm6-w56g: Home Assistant stores a pairing only after its user confirmed the code.

Only the owner compares the code, in OpenAmpere. Home Assistant cannot tell by itself whether the answer "allowed"
came from the real OpenAmpere or from another device at the same address, so nothing is stored before its user
confirms that OpenAmpere showed exactly this code.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.openampere.const import DOMAIN

from conftest import FINGERPRINT, INSTALLATION

API = "custom_components.openampere.api.OpenAmpereClient"
FLOW = "custom_components.openampere.config_flow"
OTHER = "cd" * 32  # the certificate of another device at the same address
STARTED = {"request_id": "r1", "poll_key": "pk", "nonce": "11" * 32, "expires_in": 600, "api_version": 1,
           "installation_id": INSTALLATION, "version": "0.7.0"}


async def until_approved(hass: HomeAssistant, result, fingerprint: str, port: int = 8443):
    """From the pairing form until the server answers "approved" by itself: what any device at the address can do."""
    with patch(f"{FLOW}.fetch_fingerprint", AsyncMock(return_value=fingerprint)), \
            patch(f"{API}.pair_start", AsyncMock(return_value=STARTED)), \
            patch(f"{API}.pair_reveal", AsyncMock(return_value={"status": "pending"})), \
            patch(f"{API}.pair_status", AsyncMock(return_value={"status": "approved", "token": "oa_other",
                                                                 "scope": "control"})), \
            patch(f"{FLOW}.POLL_S", 0):
        result = await hass.config_entries.flow.async_configure(result["flow_id"],
                                                                {"host": "192.168.178.20", "port": port})
        assert result["type"] is FlowResultType.SHOW_PROGRESS
        await hass.async_block_till_done()
        return await hass.config_entries.flow.async_configure(result["flow_id"])


async def test_a_pairing_is_stored_only_after_the_code_is_confirmed(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "pair"})
    result = await until_approved(hass, result, OTHER)

    assert result["type"] is FlowResultType.FORM and result["step_id"] == "confirm_code"
    assert result["description_placeholders"]["code"]  # the code to compare once more
    assert hass.config_entries.async_entries(DOMAIN) == []  # nothing stored, no token kept yet

    with patch("custom_components.openampere.async_setup_entry", AsyncMock(return_value=True)):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY and result["data"]["fingerprint"] == OTHER


async def test_reauth_with_another_certificate_warns_and_keeps_the_entry(hass: HomeAssistant, entry,
                                                                        client) -> None:
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "pair"})
    result = await until_approved(hass, result, OTHER)

    assert result["type"] is FlowResultType.FORM and result["step_id"] == "confirm_new_certificate"
    assert entry.data["fingerprint"] == FINGERPRINT and entry.data["token"] == "oa_secret"  # unchanged

    with patch("custom_components.openampere.async_setup_entry", AsyncMock(return_value=True)):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reauth_successful"
    assert entry.data["fingerprint"] == OTHER and entry.data["token"] == "oa_other"


async def test_reauth_with_the_same_certificate_asks_for_the_code(hass: HomeAssistant, entry, client) -> None:
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "pair"})
    result = await until_approved(hass, result, FINGERPRINT)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "confirm_code"
    assert entry.data["token"] == "oa_secret"


async def test_reauth_suggests_the_stored_port(hass: HomeAssistant, entry, client) -> None:
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, data={**entry.data, "port": 9443})
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "pair"})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "pair"
    defaults = {str(key): key.default() for key in result["data_schema"].schema}
    assert defaults == {"host": "192.168.178.20", "port": 9443}
