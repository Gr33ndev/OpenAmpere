# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Reading a wallbox over Modbus TCP for the wallboxes OpenAmpere shows itself (native.py, #439).

- One short connection per read, closed right afterwards: the connection is never held between two reads, as the
  number of connections such a wallbox allows is unknown (the FoxESS charger may allow only one).
- Holding registers are read with function code 3. Writing is possible only for the registers a ModbusWallbox was
  made with in `writable` (#464): reader() in native.py gives none, so a read can never turn into a write, and a
  wallbox that OpenAmpere only shows never gets a write request, whatever the settings say.
- Every failure becomes a WallboxUnreachable with a German text for the owner. On EKD systems the previous smartbox
  holds the charger's connection while it is powered (#431): the texts name it first, like the wallbox check.
"""

from __future__ import annotations

import asyncio

from pymodbus.client import AsyncModbusTcpClient
from pymodbus.exceptions import ModbusException

from .base import ReadBackFailed, WallboxUnreachable

TIMEOUT_S = 3.0
REQUEST_GAP_S = 0.5  # between the blocks of one read, as in the check that read a real charger (#431)


class ModbusWallbox:
    """One wallbox at host:port with its unit id. ranges: (start, count) that must answer; optional: blocks a model
    may not have (e.g. single-phase chargers), skipped when the wallbox refuses them as unknown addresses."""

    def __init__(self, host: str, port: int, unit: int, ranges: tuple[tuple[int, int], ...],
                 optional: tuple[tuple[int, int], ...] = (), writable: frozenset[int] = frozenset()) -> None:
        self.host, self.port, self.unit = host, port, unit
        self.ranges, self.optional, self.writable = ranges, optional, writable

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

    async def write(self, address: int, value: int) -> int:
        """Writes one read/write register and returns what it reads back (see set())."""
        return (await self.set({address: value}))[address]

    async def set(self, values: dict[int, int], write_only: frozenset[int] = frozenset()) -> dict[int, int]:
        """Writes over one short connection: first the read/write registers with function code 16, each read back,
        then the registers in write_only with function code 6 (start and stop of the FoxESS charger, which cannot be
        read back). The order matters: a current written after a stop would start the charger again (#465). Only
        registers in `writable` or write_only; the caller checks master switch, test mode and values (native.py).
        Returns the read-back values of the read/write registers."""
        if any(a not in self.writable and a not in write_only or not 0 <= v <= 0xFFFF for a, v in values.items()):
            raise ValueError(f"not writable here: {sorted(values)}")
        client = AsyncModbusTcpClient(self.host, port=self.port, timeout=TIMEOUT_S, retries=0, reconnect_delay=0)
        try:
            if not await client.connect():
                raise WallboxUnreachable(
                    f"Die Wallbox unter {self.host} nimmt gerade keine Modbus-Verbindung an. Bei Anlagen von EKD ist "
                    "meist die bisherige Smartbox der Grund: Nimm sie vom Strom und versuche es noch einmal.")
            back: dict[int, int] = {}
            ordered = [(a, v) for a, v in values.items() if a not in write_only] \
                + [(a, v) for a, v in values.items() if a in write_only]
            for i, (address, value) in enumerate(ordered):
                if i:
                    await asyncio.sleep(REQUEST_GAP_S)
                await self._write_one(client, address, value, single=address in write_only)
                if address not in write_only:
                    await asyncio.sleep(REQUEST_GAP_S)
                    try:
                        back.update(await self._block(client, address, 1, False))
                    except WallboxUnreachable:
                        raise ReadBackFailed(
                            "Die Wallbox hat die Einstellung angenommen, OpenAmpere konnte sie aber nicht zurücklesen. "
                            "Ob sie gilt, siehst du nach der nächsten Abfrage der Wallbox.") from None
            return back
        finally:
            client.close()

    async def _write_one(self, client: AsyncModbusTcpClient, address: int, value: int, *, single: bool) -> None:
        try:
            if single:
                response = await client.write_register(address, value, device_id=self.unit)
            else:
                response = await client.write_registers(address, [value], device_id=self.unit)
        except (ModbusException, OSError, asyncio.TimeoutError):
            # sent, but the answer is lost: the wallbox may well have taken it
            raise ReadBackFailed(
                "Die Wallbox hat auf die Einstellung nicht geantwortet. Vielleicht gilt sie trotzdem: Das siehst du "
                "nach der nächsten Abfrage der Wallbox.") from None
        if response is None:  # no exception, but no answer either: as lost
            raise ReadBackFailed(
                "Die Wallbox hat auf die Einstellung nicht geantwortet. Vielleicht gilt sie trotzdem: Das siehst du "
                "nach der nächsten Abfrage der Wallbox.")
        if response.isError():
            code = getattr(response, "exception_code", None)
            raise WallboxUnreachable(
                "Die Wallbox hat die Einstellung nicht angenommen. Vermutlich lässt sie sich gerade nur über ihre "
                "eigene App steuern: Stelle in der App „Fox Switch“ die Verbindungsart „Modbus TCP“ ein."
                if code in (1, 2, 3, 6) else
                f"Die Wallbox hat die Einstellung mit einer Fehlermeldung abgelehnt (Fehlernummer {code}). Versuche "
                "es in ein paar Minuten noch einmal. Klappt es dann nicht, erstelle unter Mehr → Diagnose einen "
                "Bericht.")

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
