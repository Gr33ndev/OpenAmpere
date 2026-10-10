# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Setup: pairing by comparing a code, the connection code, reauthentication."""

from __future__ import annotations

import base64
import json
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.openampere.api import Connection, OpenAmpereClient, pairing_code, parse_code
from custom_components.openampere.const import DOMAIN

from conftest import FINGERPRINT, INSTALLATION, info

API = "custom_components.openampere.api.OpenAmpereClient"
FLOW = "custom_components.openampere.config_flow"


def make_code(host="openampere.local", port=8443, fingerprint=FINGERPRINT, token="oa_secret") -> str:
    data = json.dumps({"host": host, "port": port, "fingerprint": fingerprint, "token": token}).encode()
    return "openampere1:" + base64.urlsafe_b64encode(data).decode().rstrip("=")


def test_pairing_code_matches_the_server():
    """Same fixed values as tests/test_external_api.py::test_pairing_code_vector on the server side."""
    assert pairing_code("00" * 32, bytes(range(32)), bytes(range(32, 64))) == "001345"


def test_parse_code():
    connection = parse_code("  " + make_code()[:20] + "\n" + make_code()[20:])  # line breaks from copying
    assert connection == Connection("openampere.local", 8443, FINGERPRINT, "oa_secret")
    for bad in ("", "openampere1:xyz", make_code(fingerprint="abc"), "https://openampere.local"):
        with pytest.raises(ValueError):
            parse_code(bad)


async def start(hass: HomeAssistant, choice: str):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.MENU
    return await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": choice})


async def test_connection_code(hass: HomeAssistant) -> None:
    result = await start(hass, "code")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": "nonsense"})
    assert result["errors"] == {"code": "invalid_code"}

    with patch(f"{API}.info", AsyncMock(return_value=info())), \
            patch("custom_components.openampere.async_setup_entry", AsyncMock(return_value=True)):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"code": make_code(), "host": "192.168.178.20"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == INSTALLATION
    assert result["data"] == {"host": "192.168.178.20", "port": 8443, "fingerprint": FINGERPRINT, "token": "oa_secret"}


@pytest.mark.parametrize(("error", "key"), [
    ("InvalidAuth", "invalid_auth"), ("CertificateMismatch", "certificate_mismatch"), ("CannotConnect", "cannot_connect"),
])
async def test_connection_code_errors(hass: HomeAssistant, error: str, key: str) -> None:
    from custom_components.openampere import api

    result = await start(hass, "code")
    with patch(f"{API}.info", AsyncMock(side_effect=getattr(api, error)("x"))):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": make_code()})
    assert result["errors"] == {"base": key}


async def pair_until_progress(hass: HomeAssistant, statuses: list[dict]):
    """Pairing up to the waiting step; returns (flow result, code shown, mocks)."""
    started = {"request_id": "r1", "poll_key": "pk", "nonce": "11" * 32, "expires_in": 600, "api_version": 1,
               "installation_id": INSTALLATION, "version": "0.7.0"}
    reveal = AsyncMock(return_value={"status": "pending"})
    with patch(f"{FLOW}.fetch_fingerprint", AsyncMock(return_value=FINGERPRINT)), \
            patch(f"{API}.pair_start", AsyncMock(return_value=started)) as start_mock, \
            patch(f"{API}.pair_reveal", reveal), \
            patch(f"{API}.pair_status", AsyncMock(side_effect=statuses)), \
            patch(f"{FLOW}.POLL_S", 0), \
            patch("custom_components.openampere.async_setup_entry", AsyncMock(return_value=True)):
        result = await start(hass, "pair")
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"host": "192.168.178.20", "port": 8443})
        assert result["type"] is FlowResultType.SHOW_PROGRESS
        commitment = start_mock.call_args.args[1]
        nonce = bytes.fromhex(reveal.call_args.args[2])
        import hashlib
        assert hashlib.sha256(nonce).hexdigest() == commitment  # committed before learning the server's number
        expected = pairing_code(FINGERPRINT, nonce, bytes.fromhex(started["nonce"]))
        assert result["description_placeholders"]["code"] == f"{expected[:3]} {expected[3:]}"
        await hass.async_block_till_done()
        result = await hass.config_entries.flow.async_configure(result["flow_id"])
        return result


async def test_pairing(hass: HomeAssistant) -> None:
    result = await pair_until_progress(hass, [{"status": "pending"}, {"status": "approved", "token": "oa_paired",
                                                                    "scope": "control"}])
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["token"] == "oa_paired" and result["data"]["fingerprint"] == FINGERPRINT


async def test_pairing_rejected(hass: HomeAssistant) -> None:
    result = await pair_until_progress(hass, [{"status": "rejected"}])
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "pairing_rejected"


async def test_reauth_with_a_new_code(hass: HomeAssistant, entry, client) -> None:
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "code"})
    with patch("custom_components.openampere.async_setup_entry", AsyncMock(return_value=True)):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": make_code(token="oa_new")})
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reauth_successful"
    assert entry.data["token"] == "oa_new"


async def test_client_pins_the_certificate() -> None:
    """Every request carries the fingerprint as the only accepted certificate."""
    import aiohttp

    client = OpenAmpereClient(None, Connection("fe80::1", 8443, FINGERPRINT, "oa_x"))  # type: ignore[arg-type]
    assert isinstance(client._ssl, aiohttp.Fingerprint) and client._ssl.fingerprint == bytes.fromhex(FINGERPRINT)
    assert client._base == "https://[fe80::1]:8443/api/external/v1"
    assert client._headers == {"Authorization": "Bearer oa_x"}
