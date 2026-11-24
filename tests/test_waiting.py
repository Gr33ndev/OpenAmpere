# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""The helper tests use to wait for the collector (#668)."""

import pytest

from waiting import wait_for


async def test_returns_as_soon_as_the_condition_holds():
    calls = []
    await wait_for(lambda: calls.append(1) or len(calls) >= 3, "three calls", timeout=5)
    assert len(calls) == 3


async def test_fails_naming_what_did_not_happen_and_cleans_up_first():
    cleaned = []

    async def cleanup():
        cleaned.append(True)
    with pytest.raises(pytest.fail.Exception, match=r"first reading: not within 0\.2 s"):
        await wait_for(lambda: False, "first reading", timeout=0.2, cleanup=cleanup)
    assert cleaned == [True]
