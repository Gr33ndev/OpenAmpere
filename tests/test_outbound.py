# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Outgoing requests: credentials are not sent on by redirects, answers are limited, addresses are checked."""

import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from openampere import discovery, evcc, notify
from openampere.consumers import validate
from openampere.outbound import in_home_network
from openampere.runtime import Runtime
from openampere.storage import Storage


class Server:
    """A local HTTP server that redirects /first to /second (another path stands in for another host)."""

    def __init__(self, body: bytes = b"{}") -> None:
        self.paths: list[tuple[str, str | None]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _answer(self) -> None:
                outer.paths.append((self.path, self.headers.get("Authorization") or self.headers.get("Cookie")))
                if self.path.startswith("/first") or self.path == "/":
                    self.send_response(302)
                    self.send_header("Location", f"http://localhost:{outer.port}/second")
                    self.end_headers()
                else:
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(body)

            do_GET = do_POST = _answer

            def log_message(self, *args) -> None:
                pass

        self.httpd = HTTPServer(("localhost", 0), Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.httpd.shutdown()


def test_the_ntfy_token_is_not_sent_on_by_a_redirect():
    server = Server()
    try:
        with pytest.raises(urllib.error.HTTPError):
            notify.send(f"http://localhost:{server.port}/topic", "tk_secret", "Titel", "Text")
        assert [path for path, _ in server.paths] == ["/"]  # not followed to /second with the token
    finally:
        server.close()


def test_evcc_follows_no_redirect_and_refuses_huge_answers(tmp_path, monkeypatch):
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    server = Server(body=b'{"result": "' + b"x" * 2000 + b'"}')
    try:
        monkeypatch.setattr(runtime.config.evcc, "url", f"http://localhost:{server.port}")
        client = evcc.Evcc(runtime)
        client._cookie, client._cookie_url = "auth=abc", client.url
        with pytest.raises(evcc.EvccError):
            client._call("GET", "/first")
        assert [path for path, _ in server.paths] == ["/first"]
        monkeypatch.setattr(evcc, "MAX_ANSWER_BYTES", 1000)
        with pytest.raises(evcc.EvccError, match="zu groß"):
            client._call("GET", "/second")
    finally:
        server.close()
        runtime.storage.close()


@pytest.mark.parametrize(("host", "local"), [
    ("localhost", True), ("192.168.178.20", True), ("10.1.2.3", True), ("100.101.102.103", True), ("fd00::1", True),
    ("169.254.1.1", True), ("8.8.8.8", False), ("2001:4860:4860::8888", False), ("does-not-resolve.invalid", True)])
def test_home_network_addresses(host, local):
    assert in_home_network(host) is local


async def test_the_connection_test_probes_only_the_home_network(tmp_path):
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    result = await discovery.test_connection(runtime, "8.8.8.8", 502, 1)
    assert result == {"ok": False, "error": "Bitte eine Adresse im Heimnetz angeben."}
    runtime.storage.close()


@pytest.mark.parametrize(("host", "ok"), [("shelly.local", True), ("192.168.178.41", True), ("[fe80::1]", True),
                                          ("h/x#", False), ("host?turn=on", False), ("user@host", False)])
def test_a_switch_host_is_a_name_or_an_address(host, ok):
    config = [{"name": "Pumpe", "kind": "shelly1", "host": host}]
    if ok:
        assert validate(config)[0].host == host
    else:
        with pytest.raises(ValueError):
            validate(config)
