# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Logging: the level from the settings and the last lines for the diagnostics report (#169)."""

from __future__ import annotations

import logging
import time
from collections import deque

LEVELS = {"debug": logging.DEBUG, "info": logging.INFO, "warning": logging.WARNING}
FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
KEEP = 300  # lines kept in memory for the diagnostics report


class RecentLogs(logging.Handler):
    """Keeps the last log records in memory, so a report can include them without access to the server's console."""

    def __init__(self, size: int = KEEP) -> None:
        super().__init__()
        self.records: deque[tuple[float, str, str, str]] = deque(maxlen=size)
        self._exceptions = logging.Formatter()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
            if record.exc_info:
                message += "\n" + self._exceptions.formatException(record.exc_info)
            self.records.append((record.created, record.levelname, record.name, message))
        except Exception:  # noqa: BLE001 - logging must never fail the caller
            self.handleError(record)

    def lines(self, now: float | None = None) -> list[str]:
        """The kept lines with the time before `now` instead of the clock time: when something happened relative to
        the report is what support needs; the times of the owner's day are not (#149)."""
        now = time.time() if now is None else now
        out = []
        for created, level, name, message in list(self.records):
            ago = max(0, round(now - created))
            out.append(f"-{ago // 3600:02d}:{ago // 60 % 60:02d}:{ago % 60:02d} {level} {name}: {message}")
        return out


RECENT = RecentLogs()
_configured = False


def setup(level: str = "info") -> None:
    """Console output plus the in-memory lines; called once when the server starts."""
    global _configured
    logging.basicConfig(level=logging.INFO, format=FORMAT)
    root = logging.getLogger()
    if RECENT not in root.handlers:
        root.addHandler(RECENT)
    _configured = True
    apply_level(level)


def apply_level(level: str) -> None:
    """Applies the level of the settings at runtime. "debug" only for OpenAmpere's own messages: libraries such as the
    Modbus client would fill the kept lines within seconds."""
    value = LEVELS.get(str(level).strip().lower(), logging.INFO)
    logging.getLogger("openampere").setLevel(value)
    if _configured:
        logging.getLogger().setLevel(max(value, logging.INFO))
