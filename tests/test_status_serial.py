# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""The serial number in the status is shortened without login (GHSA-vcc7-57gx-gcgr)."""

import json

from conftest import login
from test_security import app_client


def test_the_serial_number_is_shortened_without_login(tmp_path):
    """GHSA-vcc7-57gx-gcgr: it identifies the installation; the diagnostics report shortens it, too."""
    from openampere.drivers.base import DeviceInfo
    runtime, client = app_client(tmp_path)
    runtime.collector.device = DeviceInfo(manufacturer="FoxESS", model="H3-10.0-E", serial="60BH1234567890",
                                          firmware="1.0")
    runtime.storage.note_firmware("60BH1234567890", "1.0")
    status = client.get("/api/status").json()
    assert "60BH1234567890" not in json.dumps(status)
    assert status["device"]["serial"] == "60BH…90" and status["device"]["model"] == "H3-10.0-E"
    login(client)
    status = client.get("/api/status").json()
    assert status["device"]["serial"] == status["firmware"]["serial"] == "60BH1234567890"
    runtime.storage.close()
