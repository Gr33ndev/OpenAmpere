# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Meter 2 of a FoxESS H3 in the diagnostics report (#635)."""

from openampere.diagnostics import meter2_check


def words(active: int, power_raw: int) -> list[int]:
    w = [0] * 15
    w[0] = active
    value = power_raw & 0xFFFFFFFF
    w[13], w[14] = value >> 16, value & 0xFFFF
    return w


def test_meter2_not_set_up():
    c = meter2_check(words(0, 0))
    assert c.id == "meter2" and c.summary == "nicht eingerichtet" and not c.hint


def test_meter2_active_shows_rounded_power():
    c = meter2_check(words(1, 12_345))  # 1234.5 W
    assert c.summary == "aktiv, etwa 1230 W"
    assert c.details == {"active": 1, "power_w": 1230}
    assert "zweiter Zähler" in c.hint


def test_meter2_negative_power_and_unknown_flag():
    c = meter2_check(words(0, -5_000))
    assert c.summary == "unklar: Kennung 0, etwa -500 W" and c.hint
    assert meter2_check(words(7, 0)).summary.startswith("unklar: Kennung 7")


def test_meter2_not_readable():
    assert meter2_check(None, "Illegal address").summary == "nicht lesbar (Illegal address)"
    assert meter2_check(None).summary == "nicht lesbar"
    assert meter2_check([1, 2]).summary == "nicht lesbar"
