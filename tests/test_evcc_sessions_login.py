# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""The charging sessions of evcc need a login (GHSA-33vp-mf38-3mj3)."""

from conftest import login
from test_security import app_client


def test_charging_sessions_need_login_and_refresh_only_after_login(tmp_path, monkeypatch):
    """GHSA-33vp-mf38-3mj3: the sessions show when and how far the car was driven."""
    import openampere.evcc
    runtime, client = app_client(tmp_path)
    calls = []

    def http(self, method, path, body=None):
        calls.append(path)
        return []
    monkeypatch.setattr(openampere.evcc.Evcc, "_http", http)
    monkeypatch.setattr(runtime.config.evcc, "url", "http://evcc.local:7070")
    assert client.get("/api/evcc/sessions").status_code == 401
    client.get("/api/evcc?refresh=true")
    assert calls == []  # no request to evcc for someone who is not logged in
    login(client)
    assert client.get("/api/evcc/sessions").json() == {"sessions": []}
    client.get("/api/evcc?refresh=true")
    assert "/api/sessions" in calls and len(calls) > 1
    runtime.storage.close()
