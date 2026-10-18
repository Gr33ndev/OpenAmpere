# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Hardening of login, sessions, app tokens and pairing (#352)."""

import base64
import hashlib
import json
import secrets
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.routing import WebSocketRoute
from starlette.websockets import WebSocketDisconnect

from openampere import apitokens, external
from openampere.api import PROTECTED_READS, create_app
from openampere.apitokens import MAX_PENDING, REVEAL_TTL_S, ApiTokens, Pairing
from openampere.auth import MAX_FAILED_TOTAL, MAX_SESSIONS, Auth, host_allowed
from openampere.control import WriteFailed
from openampere.drivers.base import Snapshot
from openampere.runtime import Runtime
from openampere.storage import Storage

from conftest import PASSWORD, login
from test_security import app_client

V1 = external.PREFIX

# GET routes anybody in the home network may read without logging in, like the overview of the web app. A new GET
# route goes into PROTECTED_READS in api.py, or here if it shows nothing private (#164, #352).
PUBLIC_READS = {
    "/api/health",  # the health check of Docker and Home Assistant: a constant, no data
    "/api/auth/status", "/api/status", "/api/settings",  # settings: private values hidden without login
    "/api/setup/networks", "/api/setup/drivers", "/api/battery/health", "/api/battery/settings",
    "/api/grid/export-limit", "/api/import/cloud", "/api/outages", "/api/storage", "/api/live", "/api/energy/summary",
    "/api/tariffs", "/api/update", "/api/billing", "/api/charging", "/api/devices", "/api/devices/energy",
    "/api/devices/power", "/api/surplus-order", "/api/consumers",  # consumers: switch addresses hidden without login
    "/api/wallbox/models", "/api/wallboxes",  # wallboxes: local key never sent, device ID hidden without login
    "/api/cars",  # cars (#401): password never sent; login, VIN end and odometer only with login
    "/api/evcc", "/api/evcc/site", "/api/prices", "/api/export/csv", "/api/energy/timeline",
    "/api/power/timeline", "/api/pv/inputs", "/api/temperatures/timeline",
    "/api/v1/customer/installation", "/api/v1/installation/{installation}/now/all/power",  # cloud-compatible API
}
# the live values of the web app (public like /api/live) and of other apps (the token is checked in the route)
SOCKETS = {"/api/live/ws", f"{V1}/ws"}
# outside /api/ the security check does not ask for a login, also not for writes
OUTSIDE_API = {"/ocpp", "/ocpp/{charge_point}", "/assets", "/{path:path}"}


def new_token(web, scope: str) -> tuple[str, str]:
    """A new access token for another app: (token, id)."""
    created = web.post("/api/tokens", json={"name": "Home Assistant", "scope": scope}).json()
    raw = created["code"].split(":", 1)[1]
    return json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))["token"], created["token"]["id"]


def routes(app) -> tuple[set, set, set]:
    reads, sockets, outside = set(), set(), set()
    for route in app.routes:
        if not route.path.startswith("/api/"):
            outside.add(route.path)
        elif isinstance(route, WebSocketRoute):
            sockets.add(route.path)
        elif "GET" in getattr(route, "methods", ()):
            reads.add(route.path)
    return reads, sockets, outside


def test_every_read_needs_a_login_or_is_listed_as_public(tmp_path):
    _, client = app_client(tmp_path)
    reads, sockets, outside = routes(client.app)
    external_reads = {p for p in reads if p.startswith(f"{V1}/")}
    unlisted = reads - set(PROTECTED_READS) - PUBLIC_READS - external_reads
    assert not unlisted, "add these to PROTECTED_READS in api.py (or to PUBLIC_READS here if anybody may read them)"
    assert not (set(PROTECTED_READS) | PUBLIC_READS) - reads, "listed, but no such route"
    assert not set(PROTECTED_READS) & PUBLIC_READS
    assert not any("{" in p for p in PROTECTED_READS)  # compared as they are: a path with a parameter never matches
    assert sockets == SOCKETS
    assert outside <= OUTSIDE_API, "a route outside /api/ is not protected by the login"

    login(client)
    stranger = TestClient(client.app)
    for path in PROTECTED_READS:
        response = stranger.get(path)
        assert response.status_code == 401 and response.json()["code"] == "login_required", path
    other_app = TestClient(client.app, base_url="https://testserver")
    for path in external_reads:
        assert other_app.get(path).status_code == 401, path


def test_logging_out_everywhere_keeps_the_own_device_known(tmp_path):
    """Others trying passwords from many addresses must not lock out the owner who just logged out everywhere."""
    _, owner = app_client(tmp_path)
    login(owner)
    before = owner.cookies.get("openampere_device", path="/api/auth")
    response = owner.post("/api/auth/logout?everywhere=true")
    assert response.status_code == 200
    after = response.cookies.get("openampere_device")
    assert after and after != before
    for i in range(MAX_FAILED_TOTAL):
        guesser = TestClient(owner.app, client=(f"192.0.2.{i + 1}", 50000), headers={"x-openampere": "1"})
        assert guesser.post("/api/auth/login", json={"password": f"x{i}"}).status_code == 401
    assert owner.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200


def test_logins_keep_only_the_newest_sessions(tmp_path):
    storage = Storage(tmp_path / "t.db")
    auth = Auth(storage)
    salt = auth.set_password(PASSWORD)
    first = auth.create_session(salt)
    for _ in range(MAX_SESSIONS):
        last = auth.create_session(salt)
    assert len(storage.get_meta("sessions")) == MAX_SESSIONS
    assert auth.valid(last) and not auth.valid(first)


def test_a_revoked_token_gets_no_more_live_values(tmp_path):
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    web = login(TestClient(create_app(runtime), base_url="https://testserver"))
    token, token_id = new_token(web, "read")
    with TestClient(web.app, base_url="https://testserver") as app:
        with app.websocket_connect(f"wss://testserver{V1}/ws", headers={"Authorization": f"Bearer {token}"}) as ws:
            assert ws.receive_json()["type"] == "live"
            assert web.delete(f"/api/tokens/{token_id}").status_code == 200
            app.portal.call(runtime.collector._publish, Snapshot(timestamp=time.time()))
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_json()
            assert closed.value.code == 1008
    assert web.get("/api/tokens").json()["tokens"] == []


@pytest.mark.parametrize("host", ["192.168.178.50:8080", "openampere.local", "proxmox", "pi.fritz.box", "[::1]:8080",
                                  "[::1]", "::1", "fe80::1", "localhost:8089", "OpenAmpere.LOCAL", "my_pi:80"])
def test_plain_hosts_are_allowed(host):
    assert host_allowed(host, [])


@pytest.mark.parametrize("host", ["proxmox#", "proxmox?x", "proxmox/api", "user@proxmox", "[::1", "[::1]x",
                                  "[evil.example]:80", "a b", "192.168.1.5:80/x", "proxmox:80:80", "proxmox:port", ""])
def test_hosts_with_url_delimiters_are_refused(host):
    assert not host_allowed(host, [])
    assert not host_allowed(host, ["*"])  # "*" allows any name, not any text


def test_a_host_with_url_delimiters_is_refused_by_the_app(tmp_path):
    _, client = app_client(tmp_path)
    for host in ("proxmox#", "proxmox?", "proxmox/x"):
        assert client.get("/api/status", headers={"host": host}).status_code == 421, host
    assert client.get("/api/status", headers={"host": "proxmox:8089"}).status_code == 200


def pairing(tmp_path) -> Pairing:
    return Pairing(ApiTokens(Storage(tmp_path / "t.db")), lambda: "00" * 32)


def start(pair: Pairing, client: str, reveal: bool, now: float | None = None) -> dict:
    nonce = secrets.token_bytes(32)
    started = pair.start("App", hashlib.sha256(nonce).hexdigest(), now=now, client=client)
    if reveal:
        pair.reveal(started["request_id"], started["poll_key"], nonce.hex())
    return started


def test_requests_the_owner_cannot_see_do_not_block_pairing(tmp_path):
    pair = pairing(tmp_path)
    for i in range(MAX_PENDING):
        start(pair, f"192.0.2.{i + 1}", reveal=False)
    assert pair.pending() == []
    start(pair, "192.0.2.100", reveal=True)  # still possible
    assert len(pair.pending()) == 1


def test_requests_the_owner_sees_are_still_limited(tmp_path):
    pair = pairing(tmp_path)
    for i in range(MAX_PENDING):
        start(pair, f"192.0.2.{i + 1}", reveal=True)
    with pytest.raises(PermissionError, match="mehrere"):
        start(pair, "192.0.2.100", reveal=False)


def test_a_request_without_the_apps_number_ends_soon(tmp_path, monkeypatch):
    pair = pairing(tmp_path)
    hidden, revealed = start(pair, "192.0.2.1", reveal=False), start(pair, "192.0.2.2", reveal=True)
    later = time.time() + REVEAL_TTL_S + 1
    monkeypatch.setattr(apitokens, "time", SimpleNamespace(time=lambda: later))
    with pytest.raises(KeyError):
        pair.status(hidden["request_id"], hidden["poll_key"])
    assert pair.status(revealed["request_id"], revealed["poll_key"]) == {"status": "pending"}  # 10 minutes


def test_a_refused_write_does_not_use_up_the_hour(tmp_path):
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    web = login(TestClient(create_app(runtime), base_url="https://testserver"))
    web.put("/api/settings", json={"control.enabled": True})
    token, _ = new_token(web, "control")
    app = TestClient(web.app, base_url="https://testserver", headers={"Authorization": f"Bearer {token}"})
    for _ in range(external.BATTERY_WRITES_PER_HOUR + 1):
        assert app.put(f"{V1}/battery", json={"min_soc_on_grid": "viel"}).status_code == 400
    assert app.put(f"{V1}/battery", json={"min_soc_on_grid": 20}).status_code != 429  # 503: no inverter here
    for _ in range(external.OTHER_WRITES_PER_HOUR + 1):
        assert app.put(f"{V1}/grid-charging", json={"target_soc": 500}).status_code == 400
    assert app.put(f"{V1}/grid-charging", json={"target_soc": 80}).status_code == 200


def test_writes_are_counted_for_all_settings_or_none():
    limit = external.RateLimit()
    limit.check("battery:min_soc", 1)
    with pytest.raises(Exception, match="pro Stunde"):
        limit.take(["battery:max_soc", "battery:min_soc"], 1)
    limit.check("battery:max_soc", 1)  # was not counted with the refused request


def test_a_write_the_inverter_was_asked_for_stays_counted():
    limit = external.RateLimit()
    with pytest.raises(ValueError), limit.counted(["a"], 1):
        raise ValueError("refused before writing")
    with pytest.raises(WriteFailed), limit.counted(["a"], 1):
        raise WriteFailed("no answer")
    with pytest.raises(Exception, match="pro Stunde"):
        limit.check("a", 1)
