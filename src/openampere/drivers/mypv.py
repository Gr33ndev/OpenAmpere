"""my-PV heating rods (AC ELWA-E, AC ELWA 2, AC THOR) over Modbus TCP: continuously adjustable power.

Only the power register is written. The manufacturer asks not to write any other register frequently
(non-volatile memory). In the device's web interface the control type has to be "Modbus TCP" and the
control timeout a little longer than OpenAmpere's interval: without a new value the rod switches off.

Register facts: power setpoint 1000 (W, read/write), water temperature 1001 and target temperature 1002
(0.1 °C), status 1003. Unit id 1, port 502.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from pymodbus.client import AsyncModbusTcpClient
from pymodbus.exceptions import ModbusException

REG_POWER = 1000
STATUS_TEXT = {
    1: "nicht angesteuert", 2: "heizt", 3: "Bereitschaft", 4: "Boost-Heizen", 5: "Wasser hat Zieltemperatur",
    9: "Einrichtung", 20: "Legionellenschutz", 21: "ausgeschaltet", 22: "gesperrt",
}


@dataclass
class HeatingRodState:
    setpoint_w: int
    temperature_c: float | None
    target_c: float | None
    status: int

    @property
    def status_text(self) -> str:
        if self.status >= 200:
            return f"Störung (Code {self.status})"
        return STATUS_TEXT.get(self.status, f"Status {self.status}")

    @property
    def heating(self) -> bool:
        """The rod takes power right now (water not yet at target temperature, no fault)."""
        return self.status in (2, 4) or (self.status == 3 and self.setpoint_w > 0)

    @property
    def power_w(self) -> int:
        """Best estimate of the actual consumption: the setpoint while heating, otherwise 0."""
        return self.setpoint_w if self.heating else 0


class MyPvHeatingRod:
    def __init__(self, host: str, port: int = 502, unit: int = 1, timeout: float = 3.0) -> None:
        self.host, self.port, self.unit, self.timeout = host, port, unit, timeout
        self._client: AsyncModbusTcpClient | None = None
        self._lock = asyncio.Lock()

    async def _connected(self) -> AsyncModbusTcpClient:
        if self._client is None:
            self._client = AsyncModbusTcpClient(self.host, port=self.port, timeout=self.timeout, retries=0,
                                                reconnect_delay=0)
        if not self._client.connected and not await self._client.connect():
            self._client.close()
            self._client = None
            raise ConnectionError(f"Heizstab unter {self.host}:{self.port} nicht erreichbar")
        return self._client

    async def read(self) -> HeatingRodState:
        async with self._lock:
            client = await self._connected()
            try:
                response = await client.read_holding_registers(REG_POWER, count=4, device_id=self.unit)
            except ModbusException as err:
                raise ConnectionError(f"Heizstab antwortet nicht: {err}") from err
        if response.isError():
            raise ConnectionError("Heizstab lehnt die Abfrage ab. Ist die Ansteuerung auf „Modbus TCP“ gestellt?")
        power, temp, target, status = response.registers
        signed = lambda v: v - 0x10000 if v & 0x8000 else v  # noqa: E731
        return HeatingRodState(setpoint_w=power, temperature_c=signed(temp) / 10 if temp else None,
                               target_c=signed(target) / 10 if target else None, status=status)

    async def set_power(self, watts: int) -> None:
        watts = max(0, min(int(watts), 65_000))
        async with self._lock:
            client = await self._connected()
            try:
                response = await client.write_register(REG_POWER, watts, device_id=self.unit)
            except ModbusException as err:
                raise ConnectionError(f"Heizstab antwortet nicht: {err}") from err
        if response.isError():
            raise ConnectionError("Heizstab lehnt die Leistungsvorgabe ab. Ist die Ansteuerung auf „Modbus TCP“ gestellt?")

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
