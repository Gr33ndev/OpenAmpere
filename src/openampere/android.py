# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Running in the Android app on a tablet (#567).

The app starts the unchanged server inside its own process with OPENAMPERE_ANDROID set. There are no helper
containers (updates, Tailscale), no Docker and no install.sh, and the tablet is not a server: it runs on its own
battery, is switched off, put to sleep or taken out of the house. Docker installations and the Home Assistant app
never have the variable, so nothing here changes them."""
from __future__ import annotations

import os
import socket

ENV = "OPENAMPERE_ANDROID"


def active() -> bool:
    return os.environ.get(ENV, "") not in ("", "0")


def check_port(port: int) -> None:
    """Before the server starts on the tablet: another app may already use its port. uvicorn would then only exit with
    status 1, and the app could not tell the owner why OpenAmpere does not start."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # as uvicorn: a port in TIME_WAIT is free
        try:
            probe.bind(("0.0.0.0", port))
        except OSError:
            raise SystemExit(f"Port {port} wird schon von einer anderen App auf dem Tablet benutzt. Bitte beende oder "
                             f"deinstalliere diese App und starte das Tablet neu.") from None


def stop_reason(stop: SystemExit) -> str | None:
    """Why the server stopped, for the owner on the tablet: None if it stopped as asked (e.g. to start again after a
    restored backup). The messages of SystemExit in the server are German sentences that say what to do."""
    if stop.code in (None, 0):
        return None
    if isinstance(stop.code, str):
        return stop.code
    return f"OpenAmpere wurde mit dem Fehlercode {stop.code} beendet. Bitte schick einen Diagnosebericht."
