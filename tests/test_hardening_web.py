# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Headers and error pages of the web app (#358): script restrictions in the Content-Security-Policy, framing under
the Home Assistant sidebar, unknown API paths and broken addresses."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openampere import api as api_module
from openampere.api import CONTENT_POLICY, create_app
from openampere.runtime import Runtime
from openampere.storage import Storage

from test_ha_app import app_mode, clients  # noqa: F401 (fixture)

VITE_CONFIG = Path(__file__).resolve().parent.parent / "web" / "vite.config.ts"


def _directives(header: str) -> dict[str, str]:
    return {part.split()[0]: " ".join(part.split()[1:]) for part in header.split(";") if part.strip()}


def _check_policy(header: str, frame_ancestors: str) -> None:
    directives = _directives(header)
    assert directives["default-src"] == "'self'"
    assert directives["script-src"] == "'self'"  # no inline scripts, no eval
    assert directives["object-src"] == "'none'"
    assert directives["base-uri"] == "'none'"
    assert directives["frame-ancestors"] == frame_ancestors
    assert "unsafe" not in header


@pytest.fixture
def web_client(tmp_path, monkeypatch):
    web = tmp_path / "web"
    (web / "assets").mkdir(parents=True)
    (web / "index.html").write_text("<!doctype html><title>app</title>")
    monkeypatch.setattr(api_module, "WEB_DIST", web)
    return TestClient(create_app(Runtime({}, Storage(tmp_path / "t.db"))))


def test_direct_access_restricts_scripts_and_framing(web_client):
    for path in ("/", "/api/status"):
        response = web_client.get(path)
        _check_policy(response.headers["content-security-policy"], "'none'")
        assert response.headers["x-frame-options"] == "DENY"


def test_the_sidebar_restricts_scripts_and_allows_only_home_assistant_to_frame(tmp_path, app_mode):  # noqa: F811
    _, sidebar, _ = clients(tmp_path)
    response = sidebar.get("/api/auth/status")
    _check_policy(response.headers["content-security-policy"], "'self'")
    assert response.headers["x-frame-options"] == "SAMEORIGIN"


def test_ui_tests_run_with_the_policy_of_the_server():
    """`vite preview` (Playwright) sends the same policy, so a UI test fails when the app needs more than it allows."""
    assert f'"Content-Security-Policy": "{CONTENT_POLICY}"' in VITE_CONFIG.read_text()


def test_unknown_api_paths_get_a_json_404_not_the_app(web_client):
    for path in ("/api", "/api/", "/api/does-not-exist", "/api/status/extra"):
        response = web_client.get(path)
        assert response.status_code == 404, path
        assert response.headers["content-type"] == "application/json", path
        assert response.json() == {"detail": "Not Found"}, path
    assert web_client.get("/api/status").status_code == 200
    assert "<title>app</title>" in web_client.get("/apiary").text  # only the API folder, not every name like it


def test_a_nul_byte_in_the_address_gets_the_app_not_a_server_error(web_client):
    for path in ("/%00", "/devices%00", "/x%00/y"):
        response = web_client.get(path)
        assert response.status_code == 200, path
        assert "<title>app</title>" in response.text, path
