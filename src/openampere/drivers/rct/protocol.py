# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""The local protocol of RCT Power inverters (#646): frames over TCP, port 8899, the same as the RCT app uses.

Facts from RCT Power's "Serial Communication Protocol" (document version 1.14, 2022-02-11), in own words; nothing
copied. A frame is

    '+' (0x2B), command, length, object id (4 bytes), data, CRC-16

- length: one byte, two bytes for the long commands (LONG_WRITE, LONG_RESPONSE); it counts the object id and the data.
- all numbers big endian, floats IEEE 754 single precision.
- CRC-16/CCITT (polynomial 0x1021, start 0xFFFF) over command, length, id and data, padded with one zero byte when that
  is an odd number of bytes.
- '+' and '-' (0x2D) inside a frame are sent as '-' followed by the byte; the escape is not part of the CRC. A frame
  may be preceded by one zero byte.
- Plant frames (commands | 0x40) carry a 4-byte address of a further device of a plant; OpenAmpere only reads the
  device it is connected to and ignores them.
"""

from __future__ import annotations

import struct

START, ESCAPE = 0x2B, 0x2D
READ, WRITE, LONG_WRITE, RESPONSE, LONG_RESPONSE = 0x01, 0x02, 0x03, 0x05, 0x06
PLANT = 0x40
LONG = (LONG_WRITE, LONG_RESPONSE)


def crc16(data: bytes) -> int:
    if len(data) % 2:
        data += b"\x00"
    crc = 0xFFFF
    for byte in data:
        for bit in range(7, -1, -1):
            top = (crc >> 15) & 1
            crc = (crc << 1) & 0xFFFF
            if top ^ ((byte >> bit) & 1):
                crc ^= 0x1021
    return crc


def escape(data: bytes) -> bytes:
    out = bytearray()
    for byte in data:
        if byte in (START, ESCAPE):
            out.append(ESCAPE)
        out.append(byte)
    return bytes(out)


def frame(command: int, oid: int, data: bytes = b"") -> bytes:
    """A whole standard frame, escaped, ready to send."""
    length = 4 + len(data)
    size = struct.pack(">H", length) if command in LONG else struct.pack(">B", length)
    body = bytes([command]) + size + struct.pack(">I", oid) + data
    return bytes([START]) + escape(body + struct.pack(">H", crc16(body)))


def read_request(oid: int) -> bytes:
    return frame(READ, oid)


class FrameReader:
    """Collects bytes from the stream and yields the complete frames (command, object id, data) with a valid CRC.
    Bytes before a start byte, frames with a wrong CRC and plant frames are dropped."""

    def __init__(self) -> None:
        self._body = bytearray()  # unescaped bytes after the start byte
        self._in_frame = False
        self._escaped = False

    def feed(self, chunk: bytes) -> list[tuple[int, int, bytes]]:
        frames = []
        for byte in chunk:
            if not self._in_frame:
                if byte == START:
                    self._in_frame, self._body, self._escaped = True, bytearray(), False
                continue
            if self._escaped:
                self._escaped = False
            elif byte == ESCAPE:
                self._escaped = True
                continue
            elif byte == START:  # an unescaped start byte: the frame before was cut off, a new one begins
                self._body = bytearray()
                continue
            self._body.append(byte)
            found = self._complete()
            if found is not None:
                self._in_frame = False
                if found:
                    frames.append(found)
        return frames

    def _complete(self) -> tuple[int, int, bytes] | tuple[()] | None:
        """None while the frame is incomplete, () for a complete one that is dropped."""
        body = self._body
        if len(body) < 2:
            return None
        command = body[0]
        head = 3 if command & ~PLANT in LONG else 2
        if len(body) < head:
            return None
        length = int.from_bytes(body[1:head], "big")
        if len(body) < head + length + 2:
            return None
        if len(body) > head + length + 2 or length < 4:
            return ()
        payload = bytes(body[head:head + length])
        if crc16(bytes(body[:head + length])) != int.from_bytes(body[head + length:head + length + 2], "big"):
            return ()
        if command & PLANT:
            return ()
        return command, int.from_bytes(payload[:4], "big"), payload[4:]


# value types of the objects OpenAmpere reads
FLOAT, UINT8, INT32, UINT32, STRING = "float", "uint8", "int32", "uint32", "string"


def decode(kind: str, data: bytes):
    """The value of an answer, None if it does not fit its type (or is no finite number)."""
    try:
        if kind == FLOAT:
            value = struct.unpack(">f", data)[0]
            return value if value == value and abs(value) != float("inf") else None
        if kind == UINT8:
            return struct.unpack(">B", data)[0]
        if kind == INT32:
            return struct.unpack(">i", data)[0]
        if kind == UINT32:
            return struct.unpack(">I", data)[0]
        if kind == STRING:
            return data.split(b"\x00", 1)[0].decode("utf-8", "replace").strip() or None
    except struct.error:
        return None
    raise ValueError(f"unknown type {kind}")


def encode(kind: str, value) -> bytes:
    """For the simulator and tests: the data of an answer."""
    if kind == FLOAT:
        return struct.pack(">f", float(value))
    if kind == UINT8:
        return struct.pack(">B", int(value))
    if kind == INT32:
        return struct.pack(">i", int(value))
    if kind == UINT32:
        return struct.pack(">I", int(value))
    if kind == STRING:
        return str(value).encode() + b"\x00"
    raise ValueError(f"unknown type {kind}")
