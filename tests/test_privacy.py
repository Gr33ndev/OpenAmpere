# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""privacy.py: the one place for what is shortened, replaced or left out when something goes into a report."""

from openampere.privacy import ago, masked_block, shorten, text_runs


def test_one_set_of_masking_rules_for_every_block():
    """The device query and the wallbox check share the rules (#324, #325): private addresses always go, readable
    text is shortened unless included or known to be harmless (a model code)."""
    model, serial = [0x4130, 0x3232, 0x4B50, 0x3100], [0x534E, 0x3031, 0x3233, 0x3435, 0x3637]  # "A022KP1", "SN01234567"
    block = {"start": 100, "count": 12, "ok": True, "words": [*model, 0x1234, 0x0000, *serial, 7]}
    public = frozenset(range(100, 104))
    report = masked_block(block, include_text=False, private=frozenset({104}), public_text=public)
    assert report["words"][:4] == model and report["words"][4] is None  # model kept, card number gone
    assert report["words"][6:11] == [None] * 5 and report["words"][11] == 7  # serial gone, number kept
    assert [t["text"] for t in report["text"]] == ["A022KP1", "SN01…67"]
    full = masked_block(block, include_text=True, private=frozenset({104}), public_text=public)
    assert full["words"][4] is None and [t["text"] for t in full["text"]] == ["A022KP1", "SN01234567"]


def test_readable_text_in_registers():
    words = [0x4142, 0x4344, 0x4546, 0x4700, 0x0001, 0x3132]  # "ABCDEFG", then numbers
    assert text_runs(words) == [(0, 4, "ABCDEFG")]
    assert text_runs([0x4142, 0x0000, 0x4344]) == []  # too short to be a name or a serial number


def test_shorten_and_durations():
    assert shorten("SN0123456789") == "SN01…89" and shorten("ABC") == "…" and shorten(None) is None
    assert ago(30) == "vor weniger als einer Minute" and ago(90 * 60) == "vor 90 Minuten"
    assert ago(5 * 3600) == "vor 5 Stunden" and ago(3 * 86400) == "vor 3 Tagen"
