# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""What a wallbox module provides (#318). So far only a read-only check (#325): which blocks to read and what the
values mean. Control comes later and only after reports from real devices confirm the registers."""

from __future__ import annotations

from ..drivers.modbus import ascii_text


class Wallbox:
    key: str = ""
    label: str = ""
    ports: tuple[int, ...] = (502,)  # tried in this order
    unit: int = 1
    function: int = 3
    ranges: tuple[tuple[int, int], ...] = ()  # (start, count), read over one connection
    # values that never go into the report, only what they mean (e.g. an RFID card number, exact energy counters)
    private: frozenset[int] = frozenset()

    @classmethod
    def interpret(cls, values: dict[int, int]) -> dict:
        """What the readable values mean; values that could not be read are missing in `values`."""
        raise NotImplementedError


def words(values: dict[int, int], start: int, count: int) -> list[int] | None:
    if all(a in values for a in range(start, start + count)):
        return [values[a] for a in range(start, start + count)]
    return None


def u32(values: dict[int, int], address: int) -> int | None:
    """Two registers, the high word first."""
    found = words(values, address, 2)
    return (found[0] << 16) | found[1] if found else None


def text(values: dict[int, int], start: int, count: int) -> str | None:
    found = words(values, start, count)
    return (ascii_text(found) or None) if found else None


def scaled(values: dict[int, int], address: int, factor: float) -> float | None:
    return round(values[address] * factor, 3) if address in values else None
