# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""The cloud export tool never lets an installation id from the cloud (or the .env file) choose where it writes."""

import importlib.util
import json
import threading
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

TOOL = Path(__file__).resolve().parents[1] / "tools" / "cloud-export" / "cloud_export.py"


def load_tool():
    spec = importlib.util.spec_from_file_location("cloud_export_under_test", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def fake_cloud():
    """Loopback server: the installation list names `installation`, every other path answers with data."""
    state = {"installation": "abc-123", "paths": []}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 (http.server API)
            state["paths"].append(self.path)
            query = parse_qs(urlsplit(self.path).query)
            day = query.get("date", [""])[0]
            period = query.get("period", [""])[0]
            # totals only for this month, so the automatic start search ends a few days back
            month = date.today().isoformat()[:7]
            if self.path.startswith("/api/v1/customer/installation"):
                body = [{"uuid": state["installation"]}]
            elif period == "year" and not day.startswith(month[:4]) or period == "month" and day[:7] != month:
                body = {"timeline": []}
            else:
                body = {"timeline": [{"value": 1.5}]}
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        state["url"] = f"http://127.0.0.1:{server.server_address[1]}"
        yield state
    finally:
        server.shutdown()
        server.server_close()


def files_outside(root: Path, out: Path) -> list[Path]:
    out = out.resolve()
    return [p for p in root.resolve().rglob("*") if p.is_file() and not p.is_relative_to(out)]


def run_export(tool, cloud, out: Path, start: str | None):
    client = tool.Client(base_url=cloud["url"], api_key="dummy-test-key", min_interval=0, timeout=5)
    tool.cmd_export(client, out, start)


@pytest.mark.parametrize("start", [None, "yesterday"])
@pytest.mark.parametrize("uuid", ["../../escaped", "ABSOLUTE", "..", "a/b", "abc\\..\\x", "", 42, None])
def test_cloud_installation_id_cannot_leave_the_output_folder(tmp_path, monkeypatch, fake_cloud, uuid, start):
    monkeypatch.delenv("CLOUD_INSTALLATION_UUID", raising=False)
    if uuid == "ABSOLUTE":
        uuid = str(tmp_path / "absolute")
    fake_cloud["installation"] = uuid
    out = tmp_path / "case" / "export"
    if start == "yesterday":
        start = (date.today() - timedelta(days=1)).isoformat()
    tool = load_tool()

    with pytest.raises(SystemExit):
        run_export(tool, fake_cloud, out, start)

    assert files_outside(tmp_path, out) == []
    # nothing was asked for the bad installation either
    assert all(p.startswith("/api/v1/customer/installation") for p in fake_cloud["paths"])


@pytest.mark.parametrize("uuid", ["../../escaped", "ABSOLUTE", "a/b"])
def test_installation_id_from_the_env_file_is_checked_as_well(tmp_path, monkeypatch, fake_cloud, uuid):
    if uuid == "ABSOLUTE":
        uuid = str(tmp_path / "absolute")
    monkeypatch.setenv("CLOUD_INSTALLATION_UUID", uuid)
    out = tmp_path / "case" / "export"
    tool = load_tool()

    with pytest.raises(SystemExit):
        run_export(tool, fake_cloud, out, (date.today() - timedelta(days=1)).isoformat())

    assert files_outside(tmp_path, out) == []
    assert fake_cloud["paths"] == []


def test_check_command_does_not_send_a_bad_installation_id(tmp_path, monkeypatch, fake_cloud):
    monkeypatch.delenv("CLOUD_INSTALLATION_UUID", raising=False)
    fake_cloud["installation"] = "../../customer/installation"
    tool = load_tool()
    client = tool.Client(base_url=fake_cloud["url"], api_key="dummy-test-key", min_interval=0, timeout=5)

    with pytest.raises(SystemExit):
        tool.cmd_check(client, tmp_path / "export")

    assert fake_cloud["paths"] == ["/api/v1/customer/installation"]


def test_plain_installation_id_still_exports_into_the_output_folder(tmp_path, monkeypatch, fake_cloud):
    monkeypatch.delenv("CLOUD_INSTALLATION_UUID", raising=False)
    fake_cloud["installation"] = "0f8e2c1a-1234-4abc-9def-00aa11bb22cc"
    out = tmp_path / "case" / "export"
    tool = load_tool()
    yesterday = date.today() - timedelta(days=1)

    run_export(tool, fake_cloud, out, yesterday.isoformat())

    assert files_outside(tmp_path, out) == []
    inst_dir = out / fake_cloud["installation"]
    for name, _suffix, _extra in tool.DAILY_ENDPOINTS:
        assert (inst_dir / name / f"{yesterday}.json").is_file()
