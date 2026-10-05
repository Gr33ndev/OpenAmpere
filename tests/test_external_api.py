"""API for other apps such as Home Assistant (#76): tokens, HTTPS only, fixed list of commands."""

import base64
import json
import os
import stat

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from openampere import control, external
from openampere.api import create_app
from openampere.apitokens import CODE_PREFIX, MAX_TOKENS, ApiTokens
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
