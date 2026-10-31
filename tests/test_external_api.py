# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""API for other apps such as Home Assistant (#76): tokens, HTTPS only, fixed list of commands."""

import asyncio
import base64
import json
import os
import stat
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from openampere import control, external
from openampere.api import create_app
from openampere.apitokens import CODE_PREFIX, MAX_TOKENS, ApiTokens
from openampere.drivers.base import DeviceInfo, Snapshot
from openampere.runtime import Runtime
from openampere.storage import Storage
from openampere.tls import ensure_certificate, fingerprint

from conftest import login

V1 = external.PREFIX


@pytest.fixture
def runtime(tmp_path):
    return Runtime({}, Storage(tmp_path / "t.db"))


@pytest.fixture
def web(runtime):
    """The web app, logged in (creates the tokens)."""
    return login(TestClient(create_app(runtime), base_url="https://testserver"))


def decode(code: str) -> dict:
    assert code.startswith(CODE_PREFIX)
    raw = code[len(CODE_PREFIX):]
    return json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))


def new_token(web, scope="control", name="Home Assistant") -> str:
    response = web.post("/api/tokens", json={"name": name, "scope": scope})
    assert response.status_code == 200, response.text
    return decode(response.json()["code"])["token"]


def app_client(web, token=None, https=True) -> TestClient:
    """Another app on the same server as the web app: no cookie, no CSRF header, only the token."""
    client = TestClient(web.app, base_url="https://testserver" if https else "http://testserver")
    if token:
        client.headers["Authorization"] = f"Bearer {token}"
    return client


def test_creating_tokens_needs_a_login_and_shows_the_token_once(runtime, web):
    anonymous = TestClient(create_app(runtime), base_url="https://testserver")
    anonymous.headers["x-openampere"] = "1"
    assert anonymous.post("/api/tokens", json={"name": "x", "scope": "read"}).status_code == 401
    assert anonymous.get("/api/tokens").status_code == 401

    response = web.post("/api/tokens", json={"name": "  Home   Assistant ", "scope": "control"}).json()
    code = decode(response["code"])
    assert code["host"] == "testserver" and code["port"] == 8443
    assert code["fingerprint"] == runtime.tls.fingerprint and code["token"].startswith("oa_")
    listed = web.get("/api/tokens").json()
    assert listed["tls"] == {"port": 8443, "fingerprint": runtime.tls.fingerprint, "error": None}
    assert [(t["name"], t["scope"]) for t in listed["tokens"]] == [("Home Assistant", "control")]
    assert code["token"] not in json.dumps(listed) and "hash" not in listed["tokens"][0]
    assert web.post("/api/tokens", json={"name": "x", "scope": "admin"}).status_code == 422
    assert [e["action"] for e in runtime.storage.control_log()] == ["token_created"]


def test_external_api_only_over_https_and_with_a_valid_token(runtime, web):
    token = new_token(web, "read")
    assert app_client(web, token, https=False).get(f"{V1}/info").json()["code"] == "https_required"
    assert app_client(web).get(f"{V1}/info").json()["code"] == "token_invalid"
    assert app_client(web, "oa_guessed").get(f"{V1}/info").status_code == 401

    client = app_client(web, token)
    info = client.get(f"{V1}/info").json()
    assert info["api_version"] == 1 and info["token"] == {"name": "Home Assistant", "scope": "read"}
    assert info["installation_id"] and info["device"] is None  # no inverter set up in this test
    state = client.get(f"{V1}/state").json()
    assert state["status"]["connected"] is False and state["battery_settings"] is None
    assert state["grid_charging"] == {"enabled": False, "target_soc": 80, "active": False}

    # the token is not a login for the web app
    client.headers["x-openampere"] = "1"
    assert client.put("/api/settings", json={"control.enabled": True}).status_code == 401
    assert client.get("/api/backup").status_code == 401

    token_id = web.get("/api/tokens").json()["tokens"][0]["id"]
    assert web.delete(f"/api/tokens/{token_id}").status_code == 200
    assert client.get(f"{V1}/info").status_code == 401  # revoked


def test_commands_need_a_control_token_and_the_control_switch(runtime, web, monkeypatch):
    calls = []

    async def fake_write(self, changes, source=None):
        calls.append((changes, source))
        return {"dry_run": True, "written": changes, "settings": {}}

    monkeypatch.setattr(control.BatteryControl, "write", fake_write)
    reader = app_client(web, new_token(web, "read", "Nur lesen"))
    assert reader.put(f"{V1}/battery", json={"min_soc_on_grid": 30}).json()["code"] == "read_only"

    client = app_client(web, new_token(web, "control"))
    response = client.put(f"{V1}/battery", json={"min_soc_on_grid": 30})
    assert response.status_code == 403 and "ausgeschaltet" in response.json()["detail"]

    web.put("/api/settings", json={"control.enabled": True})
    assert client.put(f"{V1}/battery", json={"min_soc_on_grid": 30}).json()["written"] == {"min_soc_on_grid": 30}
    assert calls == [({"min_soc_on_grid": 30}, "Home Assistant (Zugang für Apps)")]
    # not released for other apps
    assert client.put(f"{V1}/battery", json={"export_limit_w": 0}).status_code == 400
    assert client.put(f"{V1}/grid-charging", json={"power_w": 9000}).status_code == 400
    assert client.put(f"{V1}/grid-charging", json={"enabled": True}).status_code == 400  # never set up in the app
    assert client.put(f"{V1}/devices/nope/mode", json={"mode": "boost"}).status_code == 400
    response = client.put(f"{V1}/grid-charging", json={"target_soc": 90})
    assert response.json() == {"enabled": False, "target_soc": 90, "active": False}
    assert runtime.storage.control_log()[0]["details"]["source"] == "Home Assistant (Zugang für Apps)"


def test_other_apps_cannot_set_a_reserve_below_10(runtime, web, monkeypatch):
    """The warning for a backup reserve below 10 % can only be confirmed in OpenAmpere itself (#313)."""
    async def fake_write(self, changes, source=None, confirm_low_reserve=False):
        assert not confirm_low_reserve
        raise control.ConfirmationRequired("Notstrom-Reserve unter 10 % bestätigen")

    monkeypatch.setattr(control.BatteryControl, "write", fake_write)
    web.put("/api/settings", json={"control.enabled": True})
    client = app_client(web, new_token(web))
    response = client.put(f"{V1}/battery", json={"min_soc_on_grid": 5, "confirm_low_reserve": True})
    assert response.status_code == 400  # not a setting other apps may send
    response = client.put(f"{V1}/battery", json={"min_soc_on_grid": 5})
    assert response.status_code == 428 and "nur in OpenAmpere selbst" in response.json()["detail"]


def test_battery_settings_are_written_at_most_six_times_an_hour(runtime, web, monkeypatch):
    async def fake_write(self, changes, source=None):
        return {"dry_run": True, "written": changes}

    monkeypatch.setattr(control.BatteryControl, "write", fake_write)
    web.put("/api/settings", json={"control.enabled": True})
    client = app_client(web, new_token(web))
    for value in range(20, 26):
        assert client.put(f"{V1}/battery", json={"min_soc_on_grid": value}).status_code == 200
    response = client.put(f"{V1}/battery", json={"min_soc_on_grid": 30})
    assert response.status_code == 429 and "6 pro Stunde" in response.json()["detail"]
    assert client.put(f"{V1}/battery", json={"work_mode": "backup"}).status_code == 200  # counted per setting


def test_rate_limit_window():
    limit = external.RateLimit()
    for i in range(3):
        limit.check("a", 3, now=1000 + i)
    with pytest.raises(Exception, match="in 60 min"):
        limit.check("a", 3, now=1003)
    limit.check("a", 3, now=1000 + 3600.5)  # the first write left the window


def test_state_does_not_wait_for_a_slow_battery_reading(runtime, web, monkeypatch):
    """#336: behind a slow Modbus proxy a reading took longer than Home Assistant waits for /state (20 s), and every
    entity became unavailable for a minute."""
    readings = {"started": 0, "running": 0, "most_at_once": 0}

    async def slow_read(self):
        readings["started"] += 1
        readings["running"] += 1
        readings["most_at_once"] = max(readings["most_at_once"], readings["running"])
        await asyncio.sleep(0.5)
        readings["running"] -= 1
        return {"work_mode": "self_use", "min_soc": 10, "max_soc": 100, "min_soc_on_grid": 20 + readings["started"]}

    monkeypatch.setattr(control.BatteryControl, "read", slow_read)
    monkeypatch.setattr(external, "SETTINGS_WAIT_S", 0.05)
    token = new_token(web, "read")
    with TestClient(web.app, base_url="https://testserver", headers={"Authorization": f"Bearer {token}"}) as client:
        started = time.monotonic()
        assert client.get(f"{V1}/state").json()["battery_settings"] is None  # nothing read yet
        assert client.get(f"{V1}/state").json()["battery_settings"] is None  # still the same reading
        assert time.monotonic() - started < 0.4
        time.sleep(0.6)
        # the finished reading, while the next one runs
        assert client.get(f"{V1}/state").json()["battery_settings"]["min_soc_on_grid"] == 21
    assert readings["started"] == 2 and readings["most_at_once"] == 1


def test_state_shows_a_change_right_away_also_while_a_reading_is_slow(runtime, web, monkeypatch):
    """#342: after a change, /state must not answer with the settings from before it, neither the remembered ones nor
    those of a reading that started before the change."""
    inverter = {"work_mode": "self_use", "min_soc": 10, "max_soc": 100, "min_soc_on_grid": 20}
    read_at_start = []

    async def slow_read(self):
        values = dict(inverter)
        read_at_start.append(values)
        await asyncio.sleep(0.5)
        return values

    async def fake_write(self, changes, source, confirm_low_reserve):
        inverter.update(changes)
        return {"dry_run": False, "written": changes, "settings": dict(inverter)}

    monkeypatch.setattr(control.BatteryControl, "read", slow_read)
    monkeypatch.setattr(control.BatteryControl, "_write", fake_write)
    monkeypatch.setattr(external, "SETTINGS_WAIT_S", 0.05)
    web.put("/api/settings", json={"control.enabled": True})
    token = new_token(web, "control")
    with TestClient(web.app, base_url="https://testserver", headers={"Authorization": f"Bearer {token}"}) as client:
        assert client.get(f"{V1}/state").json()["battery_settings"] is None  # a reading of 20 % runs
        assert client.put(f"{V1}/battery", json={"min_soc_on_grid": 30}).status_code == 200
        assert client.get(f"{V1}/state").json()["battery_settings"]["min_soc_on_grid"] == 30
        time.sleep(0.6)  # the reading from before the change is done
        assert read_at_start[0]["min_soc_on_grid"] == 20
        assert client.get(f"{V1}/state").json()["battery_settings"]["min_soc_on_grid"] == 30
        # a change in the OpenAmpere app as well
        assert web.put("/api/battery/settings", json={"work_mode": "backup"}).status_code == 200
        assert client.get(f"{V1}/state").json()["battery_settings"]["work_mode"] == "backup"


def test_other_apps_see_readings_as_old_only_after_a_minute(runtime, web):
    """#336: behind a slow Modbus proxy readings came 20 s and more apart; OpenAmpere's own control still treats them
    as old after 30 s, Home Assistant only after 60 s."""
    collector = runtime.collector
    collector.connected, collector.interval = True, 10
    client = app_client(web, new_token(web, "read"))
    collector.latest = Snapshot(timestamp=time.time() - 45)
    assert collector.stale and client.get(f"{V1}/state").json()["status"]["stale"] is False
    collector.latest = Snapshot(timestamp=time.time() - 65)
    assert client.get(f"{V1}/state").json()["status"]["stale"] is True
    collector.latest = Snapshot(timestamp=time.time())
    collector.connected = False  # no grace without a connection
    assert client.get(f"{V1}/state").json()["status"]["stale"] is True


def test_state_follows_the_firmware_of_the_inverter(runtime, web):
    """#372: in /info the firmware is only read when Home Assistant sets up; an update of the inverter must show."""
    client = app_client(web, new_token(web, "read"))
    assert client.get(f"{V1}/state").json()["status"]["firmware"] is None  # no inverter yet
    runtime.collector.device = DeviceInfo("FoxESS", "H3", firmware="1.40")
    assert client.get(f"{V1}/state").json()["status"]["firmware"] == "1.40"
    runtime.collector.device = DeviceInfo("FoxESS", "H3", firmware="1.50")  # read again on reconnect
    assert client.get(f"{V1}/state").json()["status"]["firmware"] == "1.50"


def test_live_websocket_needs_https_and_a_token(runtime, web):
    token = new_token(web, "read")
    app = create_app(runtime)
    with TestClient(app, base_url="https://testserver") as client:
        for url, headers in ((f"ws://testserver{V1}/ws", {"Authorization": f"Bearer {token}"}),
                             (f"wss://testserver{V1}/ws", {}),
                             (f"wss://testserver{V1}/ws", {"Authorization": "Bearer oa_wrong"})):
            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect(url, headers=headers) as ws:
                    ws.receive_json()
        with client.websocket_connect(f"wss://testserver{V1}/ws", headers={"Authorization": f"Bearer {token}"}) as ws:
            message = ws.receive_json()
            assert message["type"] == "live" and message["status"]["connected"] is False and message["devices"] == []


def test_tokens(tmp_path):
    tokens = ApiTokens(Storage(tmp_path / "t.db"))
    with pytest.raises(ValueError, match="Namen"):
        tokens.create(" ", "read")
    token, entry = tokens.create("HA", "read")
    assert tokens.check(token) == {"id": entry["id"], "name": "HA", "scope": "read"}
    assert tokens.check(token[:-1]) is None and tokens.check(None) is None and tokens.check("Bearer x") is None
    assert tokens.list()[0]["last_used"] is not None
    for i in range(MAX_TOKENS - 1):
        tokens.create(f"t{i}", "read")
    with pytest.raises(ValueError, match="Höchstens"):
        tokens.create("one more", "read")


def test_certificate_is_created_once_with_a_private_key(tmp_path):
    first = ensure_certificate(tmp_path)
    assert ensure_certificate(tmp_path).fingerprint == first.fingerprint == fingerprint(first.cert_path.read_bytes())
    assert len(first.fingerprint) == 64
    if os.name == "posix":
        assert stat.S_IMODE(first.key_path.stat().st_mode) == 0o600


def test_https_port_can_be_switched_off(tmp_path, web, runtime):
    runtime.config.server.tls_port = 0
    assert web.get("/api/tokens").json()["tls"] == {"port": None, "fingerprint": None, "error": None}
    assert web.post("/api/tokens", json={"name": "HA", "scope": "read"}).status_code == 409


def pair(client, name="Home Assistant"):
    """The app's side of pairing: commit, learn the server's number, reveal."""
    import hashlib
    import secrets as rnd

    nonce = rnd.token_bytes(32)
    started = client.post(f"{V1}/pair/start", json={"name": name, "commitment": hashlib.sha256(nonce).hexdigest()})
    assert started.status_code == 200, started.text
    data = started.json()
    assert client.post(f"{V1}/pair/{data['request_id']}/reveal",
                       json={"poll_key": data["poll_key"], "nonce": nonce.hex()}).status_code == 200
    return data, nonce


def test_pairing_by_comparing_a_code(runtime, web):
    from openampere.apitokens import pairing_code

    app = app_client(web)  # no token yet
    assert app_client(web, https=False).post(f"{V1}/pair/start", json={}).status_code == 403
    data, nonce = pair(app)
    assert data["api_version"] == 1 and data["installation_id"]
    status = lambda: app.post(f"{V1}/pair/{data['request_id']}/status", json={"poll_key": data["poll_key"]})  # noqa: E731
    assert status().json() == {"status": "pending"}
    assert app.post(f"{V1}/pair/{data['request_id']}/status", json={"poll_key": "wrong"}).status_code == 404

    # the web app shows the same code the app computes from the certificate it sees
    pending = web.get("/api/tokens").json()["pairing"]
    expected = pairing_code(runtime.tls.fingerprint, nonce, bytes.fromhex(data["nonce"]))
    assert [(p["name"], p["code"]) for p in pending] == [("Home Assistant", expected)]
    # someone in between shows the app a different certificate: the app's code differs
    assert pairing_code("ab" * 32, nonce, bytes.fromhex(data["nonce"])) != expected

    web.post(f"/api/tokens/pairing/{pending[0]['id']}", json={"approve": True, "scope": "control"})
    result = status().json()
    assert result["status"] == "approved" and result["scope"] == "control"
    assert app_client(web, result["token"]).get(f"{V1}/info").json()["token"]["scope"] == "control"
    assert status().status_code == 404  # the token is handed out once
    assert web.get("/api/tokens").json()["pairing"] == []
    assert runtime.storage.control_log()[0]["details"]["via"] == "Kopplung"


def test_pairing_can_be_rejected_and_is_limited(runtime, web):
    import hashlib

    app = app_client(web)
    data, _ = pair(app)
    request_id = web.get("/api/tokens").json()["pairing"][0]["id"]
    anonymous = TestClient(create_app(runtime), base_url="https://testserver")
    anonymous.headers["x-openampere"] = "1"
    assert anonymous.post(f"/api/tokens/pairing/{request_id}", json={"approve": True}).status_code == 401
    web.post(f"/api/tokens/pairing/{request_id}", json={"approve": False})
    assert app.post(f"{V1}/pair/{data['request_id']}/status", json={"poll_key": data["poll_key"]}).json() == {"status": "rejected"}
    assert web.get("/api/tokens").json()["tokens"] == []

    # revealing a different number than committed ends the request
    started = app.post(f"{V1}/pair/start", json={"name": "x", "commitment": hashlib.sha256(b"a" * 32).hexdigest()}).json()
    assert app.post(f"{V1}/pair/{started['request_id']}/reveal",
                    json={"poll_key": started["poll_key"], "nonce": (b"b" * 32).hex()}).status_code == 400
    assert app.post(f"{V1}/pair/{started['request_id']}/status", json={"poll_key": started["poll_key"]}).status_code == 404

    for _ in range(3):
        pair(app)
    response = app.post(f"{V1}/pair/start", json={"name": "x", "commitment": "00" * 32})
    assert response.status_code == 429 and "mehrere" in response.json()["detail"]


def test_pairing_code_vector():
    """Same fixed values as tests_ha/test_config_flow.py: the integration computes the code the same way."""
    from openampere.apitokens import pairing_code

    assert pairing_code("00" * 32, bytes(range(32)), bytes(range(32, 64))) == "001345"


def test_tokens_show_whether_the_app_is_connected_live(runtime, web):
    """The page "Verbundene Apps" shows whether Home Assistant is connected right now (#79)."""
    token = new_token(web, "read")
    assert web.get("/api/tokens").json()["tokens"][0]["live_since"] is None
    with TestClient(web.app, base_url="https://testserver") as app:
        with app.websocket_connect(f"wss://testserver{V1}/ws", headers={"Authorization": f"Bearer {token}"}) as ws:
            ws.receive_json()
            assert web.get("/api/tokens").json()["tokens"][0]["live_since"] is not None
    assert web.get("/api/tokens").json()["tokens"][0]["live_since"] is None


def test_one_device_cannot_block_pairing_for_the_others_and_the_owner_sees_the_address(runtime, web):
    guesser = TestClient(web.app, base_url="https://testserver", client=("192.0.2.66", 50000))
    for _ in range(3):
        pair(guesser, name="Home Assistant")
    assert guesser.post(f"{V1}/pair/start", json={"name": "x", "commitment": "00" * 32}).status_code == 429
    owner_app = TestClient(web.app, base_url="https://testserver", client=("192.0.2.10", 50000))
    pair(owner_app, name="Home Assistant")  # still possible for another device
    addresses = sorted({p["address"] for p in web.get("/api/tokens").json()["pairing"]})
    assert addresses == ["192.0.2.10", "192.0.2.66"]


def test_a_made_up_device_id_does_not_count_against_the_limits(runtime, web, monkeypatch):
    token = new_token(web, scope="control")
    app = app_client(web, token)
    monkeypatch.setattr(runtime.config.control, "enabled", True)
    for _ in range(61):  # more than the limit per hour: still "not found", no entries for the ids
        assert app.put(f"{V1}/devices/does-not-exist/mode", json={"mode": "boost"}).status_code == 400


def test_wallboxes_for_other_apps_without_address_or_key_and_their_mode_through_the_checks(runtime, monkeypatch):
    """Wallboxes OpenAmpere controls itself (#388): values for Home Assistant, the mode through the same checks."""
    from openampere import api as api_module
    from openampere.wallbox.de_tuya import Reading
    from openampere.wallbox.native import Live, NativeWallboxes

    key, sent, created = "ab12cd34ef56gh78", [], []

    class Wallboxes(NativeWallboxes):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            created.append(self)

        def refresh(self, now=None):  # no reads in the home network from a test
            pass

        def device(self, wb):
            class Device:
                async def set(self, values, read_back=True):
                    sent.append(values)
                    return values
            return Device()

    monkeypatch.setattr(api_module, "NativeWallboxes", Wallboxes)
    web = login(TestClient(create_app(runtime), base_url="https://testserver"))
    assert web.put("/api/wallboxes", json={"wallboxes": [{"name": "Carport", "host": "192.168.0.50",
                                                          "local_key": key, "mode": "pv"}]}).status_code == 200
    wallboxes = created[0]
    wb = wallboxes.wallboxes[0]
    wallboxes.live[wb.id] = Live(reading=Reading(state="charging", connected=True, charging=True, power_w=4140.0,
                                                 set_current_a=6, active_phases=3), updated=time.time(), online=True)
    client = app_client(web, new_token(web, "control"))

    info = client.get(f"{V1}/info").json()
    assert info["wallboxes"] == [{"id": wb.id, "name": "Carport", "model": "dé Mobile Wallbox 11 kW (Tuya)",
                                  "read_only": False}]
    state = client.get(f"{V1}/state").json()
    assert state["wallboxes"] == [{"id": wb.id, "name": "Carport", "enabled": True, "mode": "pv", "max_current_a": 16,
                                   "mode_current_a": {"pv": 16, "minpv": 16, "now": 16}, "online": True,
                                   "state": "charging", "connected": True, "charging": True, "power_w": 4140.0,
                                   "current_a": 6}]
    assert state["devices"] == []  # not among the heating rods and switches (an older integration would break)
    for text in (str(info), str(state)):
        assert "192.168.0.50" not in text and key not in text

    # the master switch first, then the mode through set_mode: logged with the token's name
    reader = app_client(web, new_token(web, "read", "Nur lesen"))
    assert reader.put(f"{V1}/wallboxes/{wb.id}/mode", json={"mode": "now"}).json()["code"] == "read_only"
    assert client.put(f"{V1}/wallboxes/{wb.id}/mode", json={"mode": "now"}).status_code == 403
    monkeypatch.setattr(runtime.config.control, "enabled", True)
    monkeypatch.setattr(type(runtime.collector), "stale", property(lambda self: False))
    runtime.collector.latest = Snapshot(timestamp=time.time(), grid_power=-6000.0, battery_power=0.0, battery_soc=90.0)
    assert client.put(f"{V1}/wallboxes/{wb.id}/mode", json={"mode": "turbo"}).status_code == 400
    assert client.put(f"{V1}/wallboxes/nope/mode", json={"mode": "now"}).status_code == 400
    response = client.put(f"{V1}/wallboxes/{wb.id}/mode", json={"mode": "now"})
    assert response.status_code == 200 and response.json()["wallboxes"][0]["mode"] == "now"
    assert wallboxes.wallboxes[0].mode == "now"
    log = runtime.storage.control_log()
    assert any(e["details"]["to"] == {"action": "mode", "value": "now"}
               and e["details"]["source"] == "Home Assistant (Zugang für Apps)" for e in log)
    # test mode (on by default) applies to what the surplus control does with the new mode: nothing sent
    assert sent == [] and any(e["dry_run"] and e["details"]["to"]["action"] == "current" for e in log)


def test_highest_current_of_a_mode_for_other_apps_through_the_same_checks(runtime, monkeypatch):
    """#409: the current of a mode with the mode, as in the web app: the owner's limit, only in a charging mode,
    the control switch and the limit per hour; logged with the token's name."""
    from openampere import api as api_module
    from openampere.wallbox.native import NativeWallboxes

    created = []

    class Wallboxes(NativeWallboxes):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            created.append(self)

        def refresh(self, now=None):  # no reads in the home network from a test
            pass

    monkeypatch.setattr(api_module, "NativeWallboxes", Wallboxes)
    web = login(TestClient(create_app(runtime), base_url="https://testserver"))
    assert web.put("/api/wallboxes", json={"wallboxes": [{"name": "Carport", "host": "192.0.2.50",
                                                          "local_key": "ab12cd34ef56gh78", "mode": "pv",
                                                          "max_current_a": 13}]}).status_code == 200
    wallboxes = created[0]
    wb_id = wallboxes.wallboxes[0].id
    client = app_client(web, new_token(web, "control"))
    url = f"{V1}/wallboxes/{wb_id}/mode"
    assert client.put(url, json={"mode": "now", "current_a": 10}).status_code == 403  # control switch off
    monkeypatch.setattr(runtime.config.control, "enabled", True)
    monkeypatch.setattr(type(runtime.collector), "stale", property(lambda self: False))
    runtime.collector.latest = Snapshot(timestamp=time.time(), grid_power=0.0, battery_power=0.0, battery_soc=50.0)

    for body in ({"mode": "now", "current_a": 16}, {"mode": "now", "current_a": 5}, {"mode": "off", "current_a": 10},
                 {"mode": "now", "current_a": "viel"}):
        assert client.put(url, json=body).status_code == 400, body  # above the owner's 13 A, below 6 A, ...
    assert wallboxes.wallboxes[0].mode == "pv" and wallboxes.wallboxes[0].mode_current_a == {}

    response = client.put(url, json={"mode": "now", "current_a": 10})
    assert response.status_code == 200
    assert response.json()["wallboxes"][0]["mode_current_a"] == {"pv": 13, "minpv": 13, "now": 10}
    assert wallboxes.wallboxes[0].mode == "now" and wallboxes.wallboxes[0].mode_current_a == {"now": 10}
    # without a current the mode changes and the chosen currents stay
    assert client.put(url, json={"mode": "pv"}).json()["wallboxes"][0]["mode_current_a"]["now"] == 10
    log = runtime.storage.control_log()
    assert any(e["details"]["to"] == {"action": "mode_current", "value": {"mode": "now", "current_a": 10}}
               and e["details"]["source"] == "Home Assistant (Zugang für Apps)" for e in log)


def test_a_made_up_wallbox_id_does_not_count_against_the_limits(runtime, web, monkeypatch):
    app = app_client(web, new_token(web, scope="control"))
    monkeypatch.setattr(runtime.config.control, "enabled", True)
    for _ in range(61):
        assert app.put(f"{V1}/wallboxes/0a1b2c3d/mode", json={"mode": "now"}).status_code == 400


def test_the_days_left_of_an_enphase_token_are_shown(runtime, web):
    """#538: the app and Home Assistant show how long the token of an Enphase gateway is valid, in days like the
    diagnostics; never the token."""
    from openampere.drivers.enphase.driver import EnphaseDriver
    client = app_client(web, new_token(web, "read"))
    assert client.get(f"{V1}/state").json()["status"]["token_days_left"] is None  # no device with a token
    runtime.collector.driver = EnphaseDriver("192.0.2.1")
    runtime.collector.driver.token_expires = time.time() + 20 * 86400 + 3600
    assert client.get(f"{V1}/state").json()["status"]["token_days_left"] == 20
    assert TestClient(web.app).get("/api/status").json()["token_days_left"] == 20  # the app, also without login
