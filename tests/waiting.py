# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Waiting in tests for something the collector does in the background (#668).

A fixed number of short sleeps that then went on silently made tests fail later, with an error that looked like a
bug (e.g. NotConnected), whenever the CI runner was slower than usual. wait_for allows plenty of time and fails right
there, naming what did not happen.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest

TIMEOUT_S = 60  # the backend suite runs several times slower on CI than locally


async def wait_for(condition: Callable[[], object], what: str, *, timeout: float = TIMEOUT_S,
                   cleanup: Callable[[], Awaitable[None]] | None = None,
                   sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
    """Waits until condition() is true; fails the test after `timeout` seconds. cleanup runs before failing, for a
    helper outside the test's try/finally (a running collector keeps the test run from ending). sleep: for a test
    that patches asyncio.sleep."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not condition():
        if loop.time() > deadline:
            if cleanup is not None:
                await cleanup()
            pytest.fail(f"{what}: not within {timeout:g} s")
        await sleep(0.1)
