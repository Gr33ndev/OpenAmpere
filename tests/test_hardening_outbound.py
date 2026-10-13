# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Outbound requests (#354): the EKD cloud key never follows a redirect, and the requests to fixed hosts (GitHub,
aWATTar, EKD cloud, Netze BW) have a size limit and a deadline for the whole request."""

import importlib.util
import json
import threading
import time
import urllib.request
from http.cookiejar import CookieJar
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from openampere import cloud_import, tariffs, updates
from openampere.cloud_import import CloudApiError, CloudImport
from openampere.gridmeter import netze_bw
from openampere.gridmeter.base import ProviderError
from openampere.outbound import fetch_within

KEY = "test-key-123456"
TOOL = Path(__file__).resolve().parents[1] / "tools" / "cloud-export" / "cloud_export.py"


class _Server(ThreadingHTTPServer):
    daemon_threads = True  # a handler still sending slowly must not hold up the end of the test


@pytest.fixture
def serve():
    """serve(answer) starts a loopback server on an ephemeral port; answer(handler) writes the answer. Every request
    is recorded as (path, headers in lower case) in server.seen."""
    servers = []

    def start(answer):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):  # noqa: N802 (http.server API)
                server.seen.append((self.path, {k.lower(): v for k, v in self.headers.items()}))
                try:
                    answer(self)
                except OSError:  # the client cut the connection
                    pass

        server = _Server(("127.0.0.1", 0), Handler)
        server.seen = []
        server.url = f"http://127.0.0.1:{server.server_address[1]}"
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return server

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


def send(handler, body: bytes, status: int = 200, headers: dict | None = None):
    handler.send_response(status)
    for name, value in {"Content-Type": "application/json", "Content-Length": str(len(body)), **(headers or {})}.items():
        handler.send_header(name, value)
    handler.end_headers()
    handler.wfile.write(body)


def redirect_to(target):
    return lambda h: send(h, b"", 302, {"Location": target.url + h.path})


def drip(seconds: float, body: bytes = b"[]"):
    """An answer that is sent byte by byte over `seconds`: each read gets data in time, the whole takes long."""
    def answer(handler):
        padded = b" " * int(seconds * 10) + body
        handler.send_response(200)
        handler.send_header("Content-Length", str(len(padded)))
        handler.end_headers()
        for byte in padded:
            handler.wfile.write(bytes([byte]))
            handler.wfile.flush()
            time.sleep(0.1)
    return answer


def cloud(url: str) -> CloudImport:
    class NoStorage:
        def get_meta(self, key):
            return None
    return CloudImport(NoStorage(), lambda: (KEY, url))


# ---- EKD cloud import (server) ----------------------------------------------------------------------------------------

def test_cloud_import_does_not_send_the_key_on_after_a_redirect(serve):
    elsewhere = serve(lambda h: send(h, b"[]"))
    cloud_server = serve(redirect_to(elsewhere))
    with pytest.raises(CloudApiError) as err:
        cloud(cloud_server.url)._request("/api/v1/customer/installation")
    assert not err.value.retry and "andere Adresse" in str(err.value)
    assert cloud_server.seen[0][1].get("x-client-api-key") == KEY
    assert elsewhere.seen == []  # the other address never got a request, let alone the key


def test_cloud_import_refuses_a_huge_answer(serve, monkeypatch):
    monkeypatch.setattr(cloud_import, "MAX_ANSWER_BYTES", 1000)
    server = serve(lambda h: send(h, json.dumps(["x" * 2000]).encode()))
    with pytest.raises(CloudApiError, match="zu groß"):
        cloud(server.url)._request("/api/v1/customer/installation")


def test_cloud_import_gives_up_on_a_slow_answer(serve, monkeypatch):
    monkeypatch.setattr(cloud_import, "REQUEST_DEADLINE_S", 1)
    server = serve(drip(4))
    begin = time.monotonic()
    with pytest.raises(CloudApiError) as err:
        cloud(server.url)._request("/api/v1/customer/installation")
    assert err.value.retry and time.monotonic() - begin < 3


def test_cloud_import_still_reads_a_normal_answer(serve):
    server = serve(lambda h: send(h, json.dumps([{"uuid": "abc"}]).encode()))
    assert cloud(server.url)._request("/api/v1/customer/installation") == (200, [{"uuid": "abc"}])


# ---- EKD cloud export (standalone tool) ------------------------------------------------------------------------------

def load_tool():
    spec = importlib.util.spec_from_file_location("cloud_export_redirect_test", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cloud_export_does_not_send_the_key_on_after_a_redirect(serve):
    tool = load_tool()
    elsewhere = serve(lambda h: send(h, b"[]"))
    cloud_server = serve(redirect_to(elsewhere))
    client = tool.Client(cloud_server.url, KEY, min_interval=0, timeout=5)
    with pytest.raises(tool.AuthError, match="redirects"):
        client.get("/api/v1/customer/installation")
    assert len(cloud_server.seen) == 1  # stops at once instead of retrying
    assert elsewhere.seen == []


def test_cloud_export_still_reads_a_normal_answer(serve):
    tool = load_tool()
    server = serve(lambda h: send(h, b'[{"uuid": "abc"}]'))
    assert tool.Client(server.url, KEY, 0, 5).get("/api/v1/customer/installation") == (200, [{"uuid": "abc"}])


# ---- update check and exchange prices ----------------------------------------------------------------------------------

RELEASE = {"tag_name": "v9.9.9", "html_url": "https://example.org/r", "body": "notes", "published_at": "2026-01-01"}


def test_update_check_reads_the_release(serve, monkeypatch):
    server = serve(lambda h: send(h, json.dumps(RELEASE).encode()))
    monkeypatch.setattr(updates, "RELEASES_URL", server.url + "/latest")
    assert updates.fetch_latest()["version"] == "9.9.9"


def test_update_check_refuses_a_huge_answer(serve, monkeypatch):
    server = serve(lambda h: send(h, json.dumps({**RELEASE, "body": "x" * 5000}).encode()))
    monkeypatch.setattr(updates, "RELEASES_URL", server.url + "/latest")
    monkeypatch.setattr(updates, "MAX_ANSWER_BYTES", 1000)
    with pytest.raises(ValueError):
        updates.fetch_latest()


def test_update_check_gives_up_on_a_slow_answer(serve, monkeypatch):
    server = serve(drip(4, json.dumps(RELEASE).encode()))
    monkeypatch.setattr(updates, "RELEASES_URL", server.url + "/latest")
    monkeypatch.setattr(updates, "DEADLINE_S", 1)
    begin = time.monotonic()
    with pytest.raises(TimeoutError):
        updates.fetch_latest()
    assert time.monotonic() - begin < 3


def test_exchange_prices_stay_within_the_requested_time(serve, monkeypatch):
    start, end = 1_767_225_600, 1_767_225_600 + 86400  # one day, from midnight UTC
    items = [{"start_timestamp": (start - 30 * 86400) * 1000, "end_timestamp": (end + 30 * 86400) * 1000,
              "marketprice": 80.0}]  # one entry over two months, e.g. a broken answer
    server = serve(lambda h: send(h, json.dumps({"data": items}).encode()))
    monkeypatch.setitem(tariffs.PRICE_SOURCES, "DE", server.url + "/marketdata")
    rows = tariffs.fetch_prices("DE", start, end)
    assert len(rows) == 96 and rows[0] == (start, 80.0) and rows[-1][0] == end - 900


def test_exchange_prices_refuse_a_huge_answer(serve, monkeypatch):
    server = serve(lambda h: send(h, json.dumps({"data": [], "pad": "x" * 5000}).encode()))
    monkeypatch.setitem(tariffs.PRICE_SOURCES, "DE", server.url + "/marketdata")
    monkeypatch.setattr(tariffs, "MAX_ANSWER_BYTES", 1000)
    with pytest.raises(ValueError):
        tariffs.fetch_prices("DE", 0, 86400)


# ---- Netze BW portal --------------------------------------------------------------------------------------------------

def portal(url: str) -> netze_bw.NetzeBw:
    return netze_bw.NetzeBw("user@example.org", "secret", base_url=url, auth_url=url, timeout=5, pause_s=0)


def test_netze_bw_refuses_a_huge_answer(serve, monkeypatch):
    monkeypatch.setattr(netze_bw, "MAX_ANSWER_BYTES", 1000)
    server = serve(lambda h: send(h, b"x" * 5000, headers={"Content-Type": "text/csv"}))
    with pytest.raises(ProviderError, match="große Antwort"):
        portal(server.url)._open(server.url + "/bff/values")


def test_netze_bw_gives_up_on_a_slow_answer(serve, monkeypatch):
    monkeypatch.setattr(netze_bw, "DEADLINE_S", 1)
    server = serve(drip(4))
    begin = time.monotonic()
    with pytest.raises(ProviderError, match="nicht erreichbar"):
        portal(server.url)._open(server.url + "/bff/values")
    assert time.monotonic() - begin < 3


def test_netze_bw_keeps_its_cookies_and_the_status_of_an_error(serve):
    def answer(handler):
        if handler.path == "/login":
            return send(handler, b"ok", headers={"Set-Cookie": "session=abc; Path=/"})
        if handler.headers.get("Cookie") == "session=abc":
            return send(handler, b"values")
        return send(handler, b"", 401)
    server = serve(answer)
    client = portal(server.url)
    assert client._open(server.url + "/values")[0] == 401
    assert client._open(server.url + "/login")[0] == 200
    assert client._open(server.url + "/values") == (200, server.url + "/values", b"values")


# ---- the helper itself --------------------------------------------------------------------------------------------------

def test_fetch_within_reports_where_the_answer_came_from(serve):
    target = serve(lambda h: send(h, b"done"))
    start = serve(redirect_to(target))
    request = urllib.request.Request(start.url + "/go")
    status, url, _, body = fetch_within(request, timeout=5, deadline=5, limit=100,
                                        handlers=(urllib.request.HTTPCookieProcessor(CookieJar()),))
    assert (status, url, body) == (200, target.url + "/go", b"done")
