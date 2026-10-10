# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Setup: pair with OpenAmpere by comparing a code, or paste a connection code.

Pairing: Home Assistant reads the certificate OpenAmpere shows, commits to a random number, learns OpenAmpere's
number and reveals its own. Both sides compute six digits from the certificate they see and both numbers; you
compare them and allow the connection in OpenAmpere (Mehr → Verbundene Apps). Then Home Assistant gets its token.
If someone sits in between, the codes differ. The connection code (made in OpenAmpere) holds address, port,
certificate fingerprint and token in one string.
"""

from __future__ import annotations

import asyncio
import hashlib
import secrets
import time
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import SOURCE_REAUTH, ConfigFlow, ConfigFlowResult, OptionsFlowWithReload
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (CannotConnect, CertificateMismatch, Connection, InvalidAuth, OpenAmpereClient, OpenAmpereError,
                  fetch_fingerprint, pairing_code, parse_code)
from .const import (API_VERSION, CONF_CODE, CONF_FINGERPRINT, CONF_MIN_INTERVAL, CONF_TOKEN, DEFAULT_MIN_INTERVAL_S,
                    DOMAIN)

DEFAULT_PORT = 8443
PAIRING_TIMEOUT_S = 600
POLL_S = 2


class PairingRejected(Exception):
    pass


class OpenAmpereConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._connection: Connection | None = None
        self._pairing: dict[str, Any] = {}
        self._code = ""
        self._task: asyncio.Task | None = None
        self._failure = "pairing_rejected"
        self._failure_detail = ""

    # ---- start -----------------------------------------------------------------------------------------------

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="user", menu_options=["pair", "code"])

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Access removed in OpenAmpere or a new certificate: pair again or paste a new code."""
        return self.async_show_menu(step_id="reauth_confirm", menu_options=["pair", "code"])

    def _default_host(self) -> str:
        return self._get_reauth_entry().data[CONF_HOST] if self.source == SOURCE_REAUTH else ""

    async def _finish(self, installation_id: str, data: dict[str, Any]) -> ConfigFlowResult:
        await self.async_set_unique_id(installation_id)
        if self.source == SOURCE_REAUTH:
            self._abort_if_unique_id_mismatch(reason="wrong_server")
            return self.async_update_reload_and_abort(self._get_reauth_entry(), data=data)
        self._abort_if_unique_id_configured(updates=data)
        return self.async_create_entry(title="OpenAmpere", data=data)

    @staticmethod
    def _data(connection: Connection) -> dict[str, Any]:
        return {CONF_HOST: connection.host, CONF_PORT: connection.port, CONF_FINGERPRINT: connection.fingerprint,
                CONF_TOKEN: connection.token}

    # ---- connection code -------------------------------------------------------------------------------------

    async def async_step_code(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                connection = parse_code(user_input[CONF_CODE])
            except ValueError:
                errors[CONF_CODE] = "invalid_code"
            else:
                if user_input.get(CONF_HOST, "").strip():
                    connection.host = user_input[CONF_HOST].strip()
                try:
                    info = await OpenAmpereClient(async_get_clientsession(self.hass), connection).info()
                except InvalidAuth:
                    errors["base"] = "invalid_auth"
                except CertificateMismatch:
                    errors["base"] = "certificate_mismatch"
                except CannotConnect:
                    errors["base"] = "cannot_connect"
                except OpenAmpereError:
                    errors["base"] = "unknown"
                else:
                    if info.get("api_version") != API_VERSION:
                        errors["base"] = "unsupported_version"
                    else:
                        return await self._finish(info["installation_id"], self._data(connection))
        schema = vol.Schema({vol.Required(CONF_CODE): str, vol.Optional(CONF_HOST, default=self._default_host()): str})
        return self.async_show_form(step_id="code", data_schema=schema, errors=errors)

    # ---- pairing ---------------------------------------------------------------------------------------------

    async def async_step_pair(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host, port = user_input[CONF_HOST].strip(), int(user_input[CONF_PORT])
            try:
                fingerprint = await fetch_fingerprint(host, port)
                connection = Connection(host, port, fingerprint, "")
                client = OpenAmpereClient(async_get_clientsession(self.hass), connection)
                nonce = secrets.token_bytes(32)
                started = await client.pair_start("Home Assistant", hashlib.sha256(nonce).hexdigest())
                if started.get("api_version") != API_VERSION:
                    errors["base"] = "unsupported_version"
                else:
                    await self.async_set_unique_id(started["installation_id"])
                    if self.source == SOURCE_REAUTH:
                        self._abort_if_unique_id_mismatch(reason="wrong_server")
                    else:
                        self._abort_if_unique_id_configured()
                    await client.pair_reveal(started["request_id"], started["poll_key"], nonce.hex())
                    self._connection, self._pairing = connection, started
                    self._code = pairing_code(fingerprint, nonce, bytes.fromhex(started["nonce"]))
                    return await self.async_step_wait()
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except OpenAmpereError as err:
                # e.g. too many open requests: OpenAmpere's own words
                errors["base"] = "pairing_failed"
                self._failure_detail = str(err)
        schema = vol.Schema({vol.Required(CONF_HOST, default=self._default_host()): str,
                             vol.Required(CONF_PORT, default=DEFAULT_PORT): vol.All(vol.Coerce(int), vol.Range(1, 65535))})
        return self.async_show_form(step_id="pair", data_schema=schema, errors=errors,
                                    description_placeholders={"detail": self._failure_detail})

    async def _wait_for_approval(self) -> str:
        assert self._connection is not None
        client = OpenAmpereClient(async_get_clientsession(self.hass), self._connection)
        deadline = time.monotonic() + PAIRING_TIMEOUT_S
        while time.monotonic() < deadline:
            await asyncio.sleep(POLL_S)
            try:
                status = await client.pair_status(self._pairing["request_id"], self._pairing["poll_key"])
            except CannotConnect:
                continue  # e.g. a short Wi-Fi hiccup
            except OpenAmpereError as err:  # gone: expired or OpenAmpere restarted
                self._failure = "pairing_expired"
                raise PairingRejected from err
            if status["status"] == "approved":
                return status["token"]
            if status["status"] == "rejected":
                raise PairingRejected
        self._failure = "pairing_expired"
        raise PairingRejected

    async def async_step_wait(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if self._task is None:
            self._task = self.hass.async_create_task(self._wait_for_approval())
        if not self._task.done():
            return self.async_show_progress(step_id="wait", progress_action="wait_for_approval",
                                            progress_task=self._task,
                                            description_placeholders={"code": f"{self._code[:3]} {self._code[3:]}"})
        try:
            self._connection.token = self._task.result()
        except PairingRejected:
            return self.async_show_progress_done(next_step_id="failed")
        return self.async_show_progress_done(next_step_id="paired")

    async def async_step_paired(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._finish(self._pairing["installation_id"], self._data(self._connection))

    async def async_step_failed(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_abort(reason=self._failure)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> OpenAmpereOptionsFlow:
        return OpenAmpereOptionsFlow()


class OpenAmpereOptionsFlow(OptionsFlowWithReload):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        current = self.config_entry.options.get(CONF_MIN_INTERVAL, DEFAULT_MIN_INTERVAL_S)
        return self.async_show_form(step_id="init", data_schema=vol.Schema({
            vol.Required(CONF_MIN_INTERVAL, default=current): vol.All(vol.Coerce(int), vol.Range(min=0, max=300)),
        }))
