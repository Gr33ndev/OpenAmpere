# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Listening to a wallbox over OCPP 1.6J (#328): Pending, only reading requests, own access rules, private report."""

import json

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from openampere import ocpp
from openampere.api import create_app
from openampere.diagnostics import report_markdown
from openampere.runtime import Runtime
from openampere.storage import Storage

BOOT = {"chargePointVendor": "FoxESS", "chargePointModel": "A022KP1", "firmwareVersion": "1.16",
        "chargePointSerialNumber": "SN0123456789ABCD", "iccid": "8949000000000000000", "imsi": "262010000000000"}
CONFIG = {"configurationKey": [
    {"key": "HeartbeatInterval", "readonly": False, "value": "60"},
    {"key": "AuthorizeRemoteTxRequests", "readonly": False, "value": "true"},
    {"key": "CentralSystemUrl", "readonly": False, "value": "ws://192.168.178.5:8080/ocpp"},
    {"key": "WifiSsid", "readonly": True, "value": "FRITZ!Box Muster"}], "unknownKey": []}


@pytest.fixture
def setup(tmp_path, authed):
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    client = TestClient(create_app(runtime))
    authed(client)
    yield runtime, client
    runtime.storage.close()


def connect(client, path="/ocpp/SN0123456789ABCD"):
    return client.websocket_connect(path, subprotocols=["ocpp1.6"])


def test_pending_and_only_reading_requests(setup):
    runtime, client = setup
    assert client.post("/api/wallbox/ocpp/start").json()["active"]
    sent_by_us = []
    with connect(client) as ws:
        ws.send_text(json.dumps([2, "1", "BootNotification", BOOT]))
        answer = json.loads(ws.receive_text())
        assert answer[0] == 3 and answer[1] == "1" and answer[2]["status"] == "Pending"
        for _ in range(2):  # GetConfiguration and GetCompositeSchedule
            sent_by_us.append(json.loads(ws.receive_text()))
        ids = {m[2]: m[1] for m in sent_by_us}
        ws.send_text(json.dumps([3, ids["GetConfiguration"], CONFIG]))
        ws.send_text(json.dumps([3, ids["GetCompositeSchedule"], {"status": "Accepted", "connectorId": 0,
                                 "scheduleStart": "2026-10-13T08:00:00Z"}]))
        ws.send_text(json.dumps([2, "2", "StatusNotification", {"connectorId": 1, "status": "Available",
                                                                "errorCode": "NoError", "timestamp": "2026-10-13T08:00:00Z"}]))
        assert json.loads(ws.receive_text()) == [3, "2", {}]
        ws.send_text(json.dumps([2, "3", "MeterValues", {"connectorId": 1, "meterValue": [{"timestamp": "2026-10-13T08:00:00Z",
            "sampledValue": [{"value": "123456", "measurand": "Energy.Active.Import.Register", "unit": "Wh"},
                             {"value": "0", "measurand": "Power.Active.Import", "unit": "W"}]}]}]))
        assert json.loads(ws.receive_text()) == [3, "3", {}]
        ws.send_text(json.dumps([2, "4", "Authorize", {"idTag": "04A1B2C3D4"}]))  # no decision from a listener
        refused = json.loads(ws.receive_text())
        assert refused[0] == 4 and refused[2] == "NotSupported"
        ws.send_text(json.dumps([2, "5", "Heartbeat", {}]))
        assert "currentTime" in json.loads(ws.receive_text())[2]
        view = client.get("/api/wallbox/ocpp").json()
        assert view["connected"] and (view["vendor"], view["model"], view["status"]) == ("FoxESS", "A022KP1", "Available")
    assert [m[2] for m in sent_by_us] == ["GetConfiguration", "GetCompositeSchedule"]
    assert all(m[2] in ocpp.READ_REQUESTS for m in sent_by_us)

    report = client.post("/api/diagnostics").json()
    text = json.dumps(report["report"], ensure_ascii=False) + report_markdown(report["report"])
    for private in ("SN0123456789ABCD", "8949000000000000000", "262010000000000", "FRITZ!Box Muster",
                    "192.168.178.5", "123456", "04A1B2C3D4", "2026-10-13T08:00"):
        assert private not in text, private
    settings = {c["key"]: c["value"] for c in report["report"]["ocpp"]["configuration"]}
    assert settings["HeartbeatInterval"] == "60" and settings["AuthorizeRemoteTxRequests"] == "true"
    assert "Wallbox über OCPP" in report["markdown"] and "Authorize" in report["markdown"]


def test_refused_without_listener_twice_or_from_outside(setup, monkeypatch):
    runtime, client = setup
    with pytest.raises(WebSocketDisconnect):  # not started
        with connect(client) as ws:
            ws.receive_text()
    client.post("/api/wallbox/ocpp/start")
    with pytest.raises(WebSocketDisconnect):  # no OCPP subprotocol
        with client.websocket_connect("/ocpp/X") as ws:
            ws.receive_text()
    monkeypatch.setattr(ocpp, "in_home_network", lambda host: False)
    with pytest.raises(WebSocketDisconnect):  # from the internet
        with connect(client) as ws:
            ws.receive_text()
    monkeypatch.setattr(ocpp, "in_home_network", lambda host: True)
    with connect(client):
        with pytest.raises(WebSocketDisconnect):  # one connection at a time
            with connect(client) as second:
                second.receive_text()


def test_never_sends_a_command():
    listener = ocpp.OcppListener(None)
    with pytest.raises(ValueError, match="only listens"):
        import asyncio
        asyncio.run(listener._request("RemoteStartTransaction", {"idTag": "x"}))


def test_the_listener_ends_by_itself(setup, monkeypatch):
    runtime, client = setup
    client.post("/api/wallbox/ocpp/start")
    with connect(client) as ws:
        runtime_listener = client.app  # noqa: F841 - keep the app alive
        monkeypatch.setattr(ocpp.time, "monotonic", lambda: 10**12)  # far past the end of the listening time
        ws.send_text(json.dumps([2, "1", "Heartbeat", {}]))
        with pytest.raises(WebSocketDisconnect):
            ws.receive_text()
            ws.receive_text()
    assert not client.get("/api/wallbox/ocpp").json()["active"]


def test_the_ocpp_view_needs_the_login(tmp_path):
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    client = TestClient(create_app(runtime))
    assert client.get("/api/wallbox/ocpp").status_code == 401  # it shows the charge point id (a serial number)
    client.headers.update({"x-openampere": "1"})
    assert client.post("/api/wallbox/ocpp/start").status_code == 401
    runtime.storage.close()
