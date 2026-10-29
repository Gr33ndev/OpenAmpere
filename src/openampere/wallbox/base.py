# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""What a wallbox module provides (#318): for the read-only check (#325) which blocks to read and what the values
mean; for a wallbox OpenAmpere reads itself (native.py) the Reading it is shown with. Control comes only after
reports from real devices confirm the registers."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..drivers.modbus import ascii_text

DEFAULT_STEPS = (6, 8, 10, 13, 16)


class WallboxUnreachable(Exception):
    """A wallbox that gave no usable answer; message: German, for the owner (with a key in locales/de.json)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotPrepared(WallboxUnreachable):
    """A wallbox that answers but did not take the fallback current OpenAmpere must set before it controls it (#465):
    not unreachable, so it is not shown as away."""


class ReadBackFailed(WallboxUnreachable):
    """A write that may have reached the wallbox: its answer was lost, or reading it back failed (#464)."""


@dataclass
class Reading:
    """What OpenAmpere knows about a wallbox after one read, the same for every model (native.py)."""
    state: str = "unknown"  # idle, plugged_in, charging, waiting, paused, finished, fault, unknown
    connected: bool = False
    charging: bool = False
    power_w: float = 0.0
    voltage_v: list[float | None] = field(default_factory=lambda: [None, None, None])
    current_a: list[float | None] = field(default_factory=lambda: [None, None, None])
    installed_phases: int = 3  # phases with voltage
    active_phases: int = 0  # phases that charge now
    session_kwh: float | None = None
    session_s: int | None = None
    temperature_c: float | None = None
    set_current_a: int | None = None  # None: the wallbox does not tell (or OpenAmpere only shows it)
    max_current_a: int | None = None
    steps_a: list[int] = field(default_factory=lambda: list(DEFAULT_STEPS))
    firmware: str | None = None
    cp_v: float | None = None
    lifetime_kwh: float | None = None  # private: not in reports
    # how the wallbox starts a charge by itself (FoxESS: controlled, plug_and_charge, locked), None: not told
    work_mode: str | None = None
    phase_switching: bool | None = None  # automatic 1/3-phase switching, None: not told
    phase_switch_min: int | None = None  # the shortest time between two phase switches, None: not told
    max_power_w: float | None = None  # its highest charging power, None: not told

    def phases(self) -> int:
        """Phases to calculate with: those that charge now, otherwise all that are connected."""
        return self.active_phases or self.installed_phases or 1


class Wallbox:
    key: str = ""
    label: str = ""
    sold_by: tuple[str, ...] = ()  # other sellers owners know the wallbox by, so they find it in the list (#412)
    ports: tuple[int, ...] = (502,)  # tried in this order
    unit: int = 1
    function: int = 3
    ranges: tuple[tuple[int, int], ...] = ()  # (start, count), read over one connection
    # values that never go into the report, only what they mean (e.g. an RFID card number, exact energy counters)
    private: frozenset[int] = frozenset()
    # addresses with harmless readable text (e.g. the model code) that is not shortened in the report
    public_text: frozenset[int] = frozenset()

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
