"""Generic Modbus register description and value decoding (shared by all drivers).

32-bit values: high word at the lower address.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Kind(str, Enum):
    U16 = "u16"
    I16 = "i16"
    U32 = "u32"
    I32 = "i32"


@dataclass(frozen=True)
class Reg:
    address: int
    kind: Kind = Kind.U16
    scale: float = 1.0

    @property
    def count(self) -> int:
        return 2 if self.kind in (Kind.U32, Kind.I32) else 1


def decode(reg: Reg, words: list[int]) -> float:
    if reg.kind is Kind.U16:
        raw = words[0]
    elif reg.kind is Kind.I16:
        raw = words[0] - 0x10000 if words[0] & 0x8000 else words[0]
    else:
        raw = (words[0] << 16) | words[1]
        if reg.kind is Kind.I32 and raw & 0x80000000:
            raw -= 0x100000000
    return round(raw * reg.scale, 3)


def encode(reg: Reg, value: int) -> list[int]:
    if reg.count == 1:
        return [value & 0xFFFF]
    value &= 0xFFFFFFFF
    return [value >> 16, value & 0xFFFF]
