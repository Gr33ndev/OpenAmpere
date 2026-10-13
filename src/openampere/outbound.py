# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""HTTP requests with credentials (evcc, ntfy, EKD cloud): never sent on to another address by a redirect.

urllib follows redirects and keeps headers such as Authorization and Cookie, also to another host. The servers
OpenAmpere talks to with credentials answer directly, so a redirect is refused (urllib raises HTTPError for it).

The socket timeout only limits the wait for each piece of an answer: a device that sends its answer byte by byte would
hold a request open as long as it likes. `read_within` also limits the whole request.
"""

from __future__ import annotations

import contextlib
import http.client
import ipaddress
import socket
import ssl
import threading
import urllib.request
from email.message import Message


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001 - urllib's signature
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def open_without_redirect(request: urllib.request.Request, timeout: float, context: ssl.SSLContext | None = None):
    """`context`: for HTTPS with other certificate checks, e.g. a device's self-signed certificate (#324)."""
    if context is None:
        return _opener.open(request, timeout=timeout)
    opener = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=context))
    return opener.open(request, timeout=timeout)


class _Deadline:
    """Cuts the connections of one request once it has taken `seconds` in total."""

    def __init__(self, seconds: float) -> None:
        self.expired = False
        self._closed = False
        self._socks: list[socket.socket] = []
        self._lock = threading.Lock()
        self._timer = threading.Timer(seconds, self._cut)
        self._timer.daemon = True
        self._timer.start()

    def connect(self, *args, **kwargs) -> socket.socket:
        """Instead of socket.create_connection. A duplicate of the socket is kept to cut it, also once TLS wraps it."""
        sock = socket.create_connection(*args, **kwargs)
        with self._lock:
            if self.expired:
                sock.close()
                raise TimeoutError("timed out")
            self._socks.append(sock.dup())
        return sock

    def _cut(self) -> None:
        with self._lock:
            if self._closed:
                return
            self.expired = True
            for sock in self._socks:
                with contextlib.suppress(OSError):
                    sock.shutdown(socket.SHUT_RDWR)  # a read waiting in the request's thread ends at once

    def close(self) -> None:
        self._timer.cancel()
        with self._lock:
            self._closed = True
            for sock in self._socks:
                sock.close()
            self._socks.clear()


def _connection(cls: type[http.client.HTTPConnection], deadline: _Deadline):
    def connection(*args, **kwargs) -> http.client.HTTPConnection:
        conn = cls(*args, **kwargs)
        conn._create_connection = deadline.connect
        return conn
    return connection


class _DeadlineHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, deadline: _Deadline) -> None:
        super().__init__()
        self._deadline = deadline

    def http_open(self, req):  # noqa: ANN001 - urllib's signature
        return self.do_open(_connection(http.client.HTTPConnection, self._deadline), req)


class _DeadlineHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, deadline: _Deadline, context: ssl.SSLContext | None) -> None:
        super().__init__(context=context)
        self._deadline = deadline

    def https_open(self, req):  # noqa: ANN001 - urllib's signature
        return self.do_open(_connection(http.client.HTTPSConnection, self._deadline), req, context=self._context)


def read_within(request: urllib.request.Request, *, timeout: float, deadline: float, limit: int,
                redirect: bool = True, context: ssl.SSLContext | None = None) -> tuple[Message, bytes]:
    """Sends the request and reads at most `limit` bytes of the answer, all within `deadline` seconds; `timeout` is
    the socket timeout. Afterwards the connection is cut and TimeoutError raised. Returns the headers and the answer.
    `redirect`: False refuses a redirect like open_without_redirect."""
    _, _, headers, body = fetch_within(request, timeout=timeout, deadline=deadline, limit=limit, redirect=redirect,
                                       context=context)
    return headers, body


def fetch_within(request: urllib.request.Request, *, timeout: float, deadline: float, limit: int,
                 redirect: bool = True, context: ssl.SSLContext | None = None,
                 handlers: tuple[urllib.request.BaseHandler, ...] = ()) -> tuple[int, str, Message, bytes]:
    """Like read_within, also returns the status and the address the answer came from (after redirects).
    `handlers`: further urllib handlers, e.g. one that keeps the cookies of a login."""
    cut = _Deadline(deadline)
    handlers = (_DeadlineHTTPHandler(cut), _DeadlineHTTPSHandler(cut, context), *handlers,
                *(() if redirect else (_NoRedirect,)))
    try:
        with urllib.request.build_opener(*handlers).open(request, timeout=timeout) as response:
            status, url, headers, body = response.status, response.geturl(), response.headers, response.read(limit)
    except Exception as err:
        cut.close()
        if cut.expired:
            raise TimeoutError("timed out") from err
        raise
    cut.close()
    if cut.expired:  # the answer may have been cut short
        raise TimeoutError("timed out")
    return status, url, headers, body


def in_home_network(host: str) -> bool:
    """Whether every address of the host is a local one (private, loopback, link-local, Tailscale's range), not one
    on the internet. A name that cannot be resolved counts as local: the connection fails anyway."""
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError):
        return True
    for info in infos:
        address = ipaddress.ip_address(info[4][0].split("%", 1)[0])
        if address.is_global:
            return False
    return True
