# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Reading a wallbox over Modbus TCP for the wallboxes OpenAmpere shows itself (native.py, #439).

- One short connection per read, closed right afterwards: the connection is never held between two reads, as the
  number of connections such a wallbox allows is unknown (the FoxESS charger may allow only one).
- Holding registers only (function code 3). There is deliberately no function here that writes: a wallbox that
  OpenAmpere only shows must never get a write request, whatever the settings say.
- Every failure becomes a WallboxUnreachable with a German text for the owner. On EKD systems the previous smartbox
  holds the charger's connection while it is powered (#431): the texts name it first, like the wallbox check.
"""

from __future__ import annotations

import asyncio

from pymodbus.client import AsyncModbusTcpClient
from pymodbus.exceptions import ModbusException

from .base import WallboxUnreachable

TIMEOUT_S = 3.0
REQUEST_GAP_S = 0.5  # between the blocks of one read, as in the check that read a real charger (#431)


class ModbusWallbox:
    """One wallbox at host:port with its unit id. ranges: (start, count) that must answer; optional: blocks a model
    may not have (e.g. single-phase chargers), skipped when the wallbox refuses them as unknown addresses."""

    def __init__(self, host: str, port: int, unit: int, ranges: tuple[tuple[int, int], ...],
                 optional: tuple[tuple[int, int], ...] = ()) -> None:
        self.host, self.port, self.unit = host, port, unit
        self.ranges, self.optional = ranges, optional

    async def status(self) -> dict[int, int]:
        """address -> word of every block that answered."""
        client = AsyncModbusTcpClient(self.host, port=self.port, timeout=TIMEOUT_S, retries=0, reconnect_delay=0)
        try:
            if not await client.connect():
                raise WallboxUnreachable(
                    f"Die Wallbox unter {self.host} antwortet nicht oder nimmt keine Modbus-Verbindung an. Bei Anlagen "
                    "von EKD ist meist die bisherige Smartbox der Grund: Solange sie Strom hat, hält sie die "
                    "Verbindung zur Wallbox. Nimm sie vom Strom, dann liest OpenAmpere die Wallbox von selbst wieder. "
                    "Hilft das nicht, prüfe die Adresse und ob evcc oder ein anderes Programm die Wallbox liest.")
            values: dict[int, int] = {}
            blocks = [(r, False) for r in self.ranges] + [(r, True) for r in self.optional]
            for i, ((start, count), optional) in enumerate(blocks):
                if i:
                    await asyncio.sleep(REQUEST_GAP_S)
                values.update(await self._block(client, start, count, optional))
            return values
        finally:
            client.close()

    async def _block(self, client: AsyncModbusTcpClient, start: int, count: int, optional: bool) -> dict[int, int]:
        try:
            response = await client.read_holding_registers(start, count=count, device_id=self.unit)
        except (ModbusException, OSError, asyncio.TimeoutError):
            response = None
        code = None
        if response is not None and not response.isError():
            if len(response.registers) == count:
                return {start + i: word for i, word in enumerate(response.registers)}
        elif response is not None:
            code = getattr(response, "exception_code", None)
            if optional and code in (1, 2, 3):
                return {}
        if code == 6:
            raise WallboxUnreachable(
                "Die Wallbox lehnt das Lesen ab („Gerät beschäftigt“). Vermutlich erlaubt sie Modbus in ihrer "
                "jetzigen Steuerungsart nicht. Du siehst sie in der App des Herstellers.")
        if code in (1, 2, 3):
            raise WallboxUnreachable("Die Wallbox antwortet, kennt diese Register aber nicht. Vielleicht ist es ein "
                                     "anderes Modell oder eine andere Firmware.")
        raise WallboxUnreachable(
            "Die Wallbox nimmt die Verbindung an, antwortet aber nicht richtig. Bei Anlagen von EKD ist meist die "
            "bisherige Smartbox der Grund: Solange sie Strom hat, hält sie die Verbindung zur Wallbox. Nimm sie vom "
            "Strom, dann liest OpenAmpere die Wallbox von selbst wieder. Hilft das nicht, liest vielleicht evcc die "
            "Wallbox, oder die Geräteadresse stimmt nicht.")
