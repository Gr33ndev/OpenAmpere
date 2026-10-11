# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""HTTP requests with credentials (evcc, ntfy): never sent on to another address by a redirect.

urllib follows redirects and keeps headers such as Authorization and Cookie, also to another host. The servers
OpenAmpere talks to with credentials answer directly, so a redirect is refused (urllib raises HTTPError for it).
"""

from __future__ import annotations

import ipaddress
import socket
import urllib.request


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001 - urllib's signature
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def open_without_redirect(request: urllib.request.Request, timeout: float):
    return _opener.open(request, timeout=timeout)


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
