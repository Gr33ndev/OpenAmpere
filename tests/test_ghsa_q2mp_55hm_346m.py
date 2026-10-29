# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""The OCPP listener accepts wallboxes only, not web pages; one connection also when two arrive together; the texts
a wallbox sends cannot add Markdown or HTML to the diagnostics report (GHSA-q2mp-55hm-346m)."""

import asyncio
import json
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from openampere import ocpp
from openampere.api import create_app
from openampere.runtime import Runtime
from openampere.storage import Storage


@pytest.fixture
def setup(tmp_path, authed):
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    client = TestClient(create_app(runtime))
    authed(client)
    yield runtime, client
    runtime.storage.close()


def connect(client, headers=None):
    return client.websocket_connect("/ocpp/PAGE", subprotocols=["ocpp1.6"], headers=dict(headers or {}))


@pytest.mark.parametrize("origin", ["http://attacker.example", "http://testserver", "null"])
def test_a_web_page_is_refused(setup, origin):
    runtime, client = setup
    client.post("/api/wallbox/ocpp/start")
    with pytest.raises(WebSocketDisconnect):
        with connect(client, {"origin": origin}) as ws:
            ws.send_text(json.dumps([2, "1", "BootNotification", {"chargePointVendor": "Page"}]))
            ws.receive_text()
    assert client.get("/api/wallbox/ocpp").json()["vendor"] is None
    with connect(client) as ws:  # the slot is still free for the wallbox, which sends no Origin
        ws.send_text(json.dumps([2, "1", "Heartbeat", {}]))
        assert "currentTime" in json.loads(ws.receive_text())[2]


def test_a_foreign_host_is_refused(setup):
    runtime, client = setup
    client.post("/api/wallbox/ocpp/start")
    with pytest.raises(WebSocketDisconnect):  # DNS rebinding: a name that is not allowed
        with connect(client, {"host": "rebind.attacker.example"}) as ws:
            ws.send_text(json.dumps([2, "1", "Heartbeat", {}]))  # answered if it got in: no hanging test
            ws.receive_text()


def test_two_connections_at_once_only_one_gets_in(tmp_path, monkeypatch):
    def slow_home_network(host):
        time.sleep(0.05)  # both connections wait here at the same time
        return True

    monkeypatch.setattr(ocpp, "in_home_network", slow_home_network)
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    try:
        listener = ocpp.OcppListener(runtime)
        listener.start()

        def socket():
            return SimpleNamespace(headers={"host": "192.0.2.1:8089"}, client=SimpleNamespace(host="192.0.2.10"),
                                   scope={"subprotocols": ["ocpp1.6"]})

        async def both():
            return await asyncio.gather(listener.refuse_reason(socket()), listener.refuse_reason(socket()))

        reasons = asyncio.run(both())
        assert sorted(reasons, key=str) == [None, "already connected"]
    finally:
        runtime.storage.close()


def test_texts_of_the_wallbox_cannot_add_markdown():
    report = {"charge_point": "X</details>", "messages": 3,
              "boot": {"chargePointVendor": "Vendor```\n\n**injected** [link](http://attacker.example)",
                       "chargePointModel": "M</details><img src=x>", "firmwareVersion": "1`<b>`"},
              "status": [{"status": "Faulted\n# Title"}], "refused": ["Authorize<script>"], "errors": ["x\n</details>"]}
    lines = ocpp.ocpp_markdown(report)
    summary = lines[:lines.index("")]
    for line in summary:
        assert line.startswith("- ") and "\n" not in line and "```" not in line
        outside_code = "".join(line.split("`")[0::2])  # what is not inline code
        assert "<" not in outside_code and "[" not in outside_code and "*" not in outside_code, line
    assert "`M</details><img src=x>`" in summary[0] and "`Vendor''' **injected** [link](http://attacker.example)`" \
        in summary[0]
    assert lines.count("</details>") == 1
