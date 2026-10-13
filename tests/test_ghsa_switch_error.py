# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""GHSA-7whx-cq9r-q56g: the error of a failed switch command quoted the switch address with the device's password,
readable without a login (/api/consumers, /api/devices, /api/status) and with a read-only app token (/state)."""

import base64
import json
import socket
import urllib.error

import pytest
from fastapi.testclient import TestClient

from openampere import consumers as consumers_module
from openampere import external
from openampere.api import create_app
from openampere.apitokens import CODE_PREFIX
from openampere.consumers import SurplusControl
from openampere.runtime import Runtime
from openampere.storage import Storage

from conftest import login

SECRET_USERINFO = "DUMMYPW-USERINFO-1"
SECRET_SPACE = "DUMMYPW-SPACE-2"
# forms an older version accepted; urllib refuses both before any connection, with a message quoting the address
USERINFO = {"id": "old1", "name": "Heizstab", "kind": "http", "power_w": 1000, "min_on_min": 0, "min_off_min": 0,
            "url_on": f"http://admin:{SECRET_USERINFO}@heater.invalid/on",
            "url_off": f"http://admin:{SECRET_USERINFO}@heater.invalid/off"}
SPACE = {"id": "old2", "name": "Steckdose", "kind": "http", "power_w": 500, "min_on_min": 0, "min_off_min": 0,
         "url_on": f"http://plug.invalid/cm?user=admin&password={SECRET_SPACE}&cmnd=Power On",
         "url_off": f"http://plug.invalid/cm?user=admin&password={SECRET_SPACE}&cmnd=Power Off"}
SECRETS = (SECRET_USERINFO, SECRET_SPACE)
INVALID = "Nicht erreichbar: ungültige Adresse"


def runtime_with_control(tmp_path) -> Runtime:
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    runtime.config.control.enabled = True
    runtime.config.control.dry_run = False
    return runtime


def read_token(web) -> str:
    code = web.post("/api/tokens", json={"name": "Home Assistant", "scope": "read"}).json()["code"]
    raw = code[len(CODE_PREFIX):]
    return json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))["token"]


def test_a_failed_switch_shows_no_password_without_login_nor_to_a_read_token(tmp_path):
    runtime = runtime_with_control(tmp_path)
    runtime.storage.set_meta("consumers", [USERINFO, SPACE])  # stored by an older version
    web = login(TestClient(create_app(runtime), base_url="https://testserver"))
    for device in ("old1", "old2"):
        assert web.post(f"/api/consumers/{device}/switch?on=true").status_code == 200
    reader = TestClient(web.app, base_url="https://testserver")
    reader.headers["Authorization"] = f"Bearer {read_token(web)}"

    anonymous = TestClient(web.app, base_url="https://testserver")  # no cookie: any device in the home network
    listed = anonymous.get("/api/consumers").json()["consumers"]
    assert [c["id"] for c in listed] == ["old1", "old2"]  # stored devices keep loading
    assert [c["state"]["error"] for c in listed] == [INVALID, INVALID]
    state = reader.get(f"{external.PREFIX}/state")
    assert state.status_code == 200 and [d["error"] for d in state.json()["devices"]] == [INVALID, INVALID]
    log = json.dumps(runtime.storage.control_log(), ensure_ascii=False)
    assert "Fehler: ungültige Adresse" in log
    for body in (*(anonymous.get(path).text for path in ("/api/consumers", "/api/devices", "/api/status")),
                 state.text, log):
        for secret in SECRETS:
            assert secret not in body


def test_switch_addresses_urllib_cannot_send_are_refused_when_saved(tmp_path):
    runtime = runtime_with_control(tmp_path)
    web = login(TestClient(create_app(runtime)))
    for device in (USERINFO, SPACE):
        response = web.put("/api/consumers", json={"consumers": [{k: v for k, v in device.items() if k != "id"}]})
        assert response.status_code == 400 and response.json()["detail"].startswith(f"{device['name']}: ")
    tab = {**SPACE, "url_on": SPACE["url_on"].replace("\x20", "\t"), "url_off": "http://plug.invalid/off"}
    assert web.put("/api/consumers", json={"consumers": [tab]}).status_code == 400
    assert runtime.storage.get_meta("consumers") is None
    fine = {**SPACE, "url_on": SPACE["url_on"].replace(" ", "%20"), "url_off": SPACE["url_off"].replace(" ", "%20")}
    assert web.put("/api/consumers", json={"consumers": [fine]}).status_code == 200

    # a device stored by an older version still loads; the next save names it so the owner can correct it
    runtime.storage.set_meta("consumers", [USERINFO])
    assert [c.id for c in SurplusControl(runtime).consumers] == ["old1"]
    unchanged = {**USERINFO, "url_on": None, "url_off": None}  # the app sends the hidden addresses as null
    response = web.put("/api/consumers", json={"consumers": [unchanged]})
    assert response.status_code == 400 and response.json()["detail"].startswith("Heizstab: ")
    assert SECRET_USERINFO not in response.text


@pytest.mark.parametrize(("error", "reason"), [
    (urllib.error.HTTPError(SPACE["url_on"], 401, "Unauthorized", None, None), "das Gerät meldet HTTP-Status 401"),
    (urllib.error.URLError(ConnectionRefusedError(111, f"refused {SECRET_SPACE}")), "Verbindung abgelehnt"),
    (urllib.error.URLError(socket.gaierror(-2, f"unknown {SECRET_SPACE}")), "Adresse nicht gefunden"),
    (urllib.error.URLError(TimeoutError(f"timed out {SECRET_SPACE}")), "keine Antwort vom Gerät"),
    (TimeoutError(f"timed out {SECRET_SPACE}"), "keine Antwort vom Gerät"),
    (ValueError(f"unknown url type: {SECRET_SPACE}"), "ungültige Adresse"),
    (urllib.error.URLError(f"no host given {SECRET_SPACE}"), "keine Verbindung"),
    (OSError(f"network unreachable {SECRET_SPACE}"), "keine Verbindung"),
    (RuntimeError(f"anything else {SECRET_SPACE}"), "keine Verbindung"),
])
async def test_a_failed_switch_says_a_fixed_reason_not_the_text_of_the_error(tmp_path, monkeypatch, error, reason):
    def fail(url):
        raise error
    runtime = runtime_with_control(tmp_path)
    monkeypatch.setattr(consumers_module, "_call", fail)
    control = SurplusControl(runtime)
    fine = {**SPACE, "url_on": SPACE["url_on"].replace(" ", "%20"), "url_off": SPACE["url_off"].replace(" ", "%20")}
    device = control.save([fine])[0]
    await control.switch(device, True, "von Hand geschaltet", now=1000)
    assert control.states[device.id].error == f"Nicht erreichbar: {reason}"
    assert [e["result"] for e in runtime.storage.control_log()] == [f"Fehler: {reason}"]
