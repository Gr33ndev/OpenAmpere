# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""A user name and password in the ntfy address never end up in the log or the shareable diagnostics report.

urllib took them for part of the host name, so a failed push logged "nonnumeric port: '<part of the password>@host'"
and the masking of the report could not know that fragment."""

import logging
import urllib.parse

import pytest

from openampere import logs
from openampere import notify as notify_module
from openampere import privacy
from openampere.diagnostics import Diagnostics, report_markdown
from openampere.notify import Notifier, send
from openampere.runtime import Runtime
from openampere.storage import Storage

USER = "ntfyowner"
PASSWORD = "pa$$:w!rd!"  # ends with a special character: the e-mail masking does not take it
ADDRESSES = [f"https://{USER}:{PASSWORD}@ntfy.example/openampere-test",
             f"https://{USER}:pa%24%24%3Aw%21rd%21@ntfy.example/openampere-test"]
FRAGMENTS = (USER, "pa$$", "w!rd", "pa%24%24", "w%21rd")


@pytest.fixture
def recent():
    logger = logging.getLogger("openampere")
    logs.RECENT.records.clear()
    logger.addHandler(logs.RECENT)
    try:
        yield logs.RECENT
    finally:
        logger.removeHandler(logs.RECENT)
        logs.RECENT.records.clear()


def no_request(monkeypatch):
    requests = []
    monkeypatch.setattr(notify_module, "open_without_redirect", lambda request, timeout: requests.append(request))
    return requests


@pytest.mark.parametrize("address", ADDRESSES)
def test_send_refuses_credentials_in_the_address_without_quoting_them(address, monkeypatch):
    requests = no_request(monkeypatch)
    with pytest.raises(ValueError) as err:
        send(address, "", "Test", "Test")
    assert requests == []  # nothing is sent with the credentials in the host name
    assert "Zugangs-Token" in str(err.value)
    for fragment in FRAGMENTS:
        assert fragment not in str(err.value), fragment


@pytest.mark.parametrize("address", ADDRESSES)
async def test_a_failed_push_does_not_put_the_password_into_the_report(address, tmp_path, recent):
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    try:
        runtime.config.notify.ntfy_url = address  # e.g. from config.yaml, which the app does not check
        assert not await Notifier(runtime).push("Test", "Test")
        assert any("notification failed" in line for line in recent.lines())
        report = await Diagnostics(runtime).run()
        text = report_markdown(report)
        for fragment in FRAGMENTS:
            assert fragment not in text, fragment
    finally:
        runtime.storage.close()


@pytest.mark.parametrize("address", ADDRESSES)
async def test_the_app_does_not_save_credentials_in_the_ntfy_address(address, tmp_path):
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    try:
        with pytest.raises(ValueError) as err:
            await runtime.update_settings({"notify.ntfy_url": address})
        assert "Zugangs-Token" in str(err.value)
        for fragment in FRAGMENTS:
            assert fragment not in str(err.value), fragment
        assert runtime.config.notify.ntfy_url == ""
        await runtime.update_settings({"notify.ntfy_url": "https://ntfy.example/openampere-test"})
        assert runtime.config.notify.ntfy_url == "https://ntfy.example/openampere-test"
    finally:
        runtime.storage.close()


@pytest.mark.parametrize("address", ADDRESSES)
def test_the_report_masks_the_credentials_of_the_ntfy_address(address, tmp_path):
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    try:
        runtime.config.notify.ntfy_url = address
        written = urllib.parse.urlsplit(address).password  # as written in the address, maybe percent-encoded
        text = privacy.for_report(runtime).text(f"login {USER} failed, password {PASSWORD} or {written}, ntfy at {address}")
        for fragment in FRAGMENTS:
            assert fragment not in text, fragment
        assert "<Zugangsdaten>" in text and "<ntfy-Adresse>" in text
    finally:
        runtime.storage.close()
