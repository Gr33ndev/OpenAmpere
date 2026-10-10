# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""State and register handling of my-PV heating rods (#238)."""

import asyncio
from types import SimpleNamespace

import pytest
from pymodbus.exceptions import ModbusException

from openampere.drivers.mypv import HeatingRodState, MyPvHeatingRod
from openampere.simulator import SimulatedHeatingRod


def state(status, setpoint=2000):
    return HeatingRodState(setpoint_w=setpoint, temperature_c=50.0, target_c=60.0, status=status)


def test_status_text():
    assert state(2).status_text == "heizt"
    assert state(200).status_text == "Störung (Code 200)"
    assert state(7).status_text == "Status 7"


@pytest.mark.parametrize("status, setpoint, heating", [
    (2, 2000, True), (4, 2000, True),  # heating, boost
    (3, 2000, True), (3, 0, False),  # standby: takes power only with a setpoint
    (5, 2000, False), (21, 2000, False), (200, 2000, False),  # at target temperature, switched off, fault
])
def test_heating_and_power(status, setpoint, heating):
    rod = state(status, setpoint)
    assert rod.heating is heating
    assert rod.power_w == (setpoint if heating else 0)


class FakeClient:
    """Stands in for the Modbus client: answers with fixed registers and records writes."""

    connected = True

    def __init__(self, registers=(0, 0, 0, 1), error=False, raises=False):
        self.registers, self.error, self.raises, self.writes = list(registers), error, raises, []

    def response(self):
        return SimpleNamespace(isError=lambda: self.error, registers=self.registers)

    async def read_holding_registers(self, address, count, device_id):
        if self.raises:
            raise ModbusException("timeout")
        return self.response()

    async def write_register(self, address, value, device_id):
        if self.raises:
            raise ModbusException("timeout")
        self.writes.append((address, value))
        return self.response()

    def close(self):
        pass


def rod_with(client):
    rod = MyPvHeatingRod("127.0.0.1")
    rod._client = client
    return rod


async def test_read_decodes_temperatures():
    rod = rod_with(FakeClient([1500, 0xFFF6, 0, 2]))  # -1.0 °C, no target temperature
    result = await rod.read()
    assert (result.setpoint_w, result.temperature_c, result.target_c, result.status) == (1500, -1.0, None, 2)
    rod = rod_with(FakeClient([0, 0, 600, 3]))  # no temperature sensor
    assert (await rod.read()).temperature_c is None


async def test_set_power_stays_in_range():
    client = FakeClient()
    rod = rod_with(client)
    await rod.set_power(-50)
    await rod.set_power(70_000)
    await rod.set_power(1234.7)
    assert client.writes == [(1000, 0), (1000, 65_000), (1000, 1234)]


async def test_errors_become_connection_errors():
    with pytest.raises(ConnectionError, match="lehnt die Abfrage ab"):
        await rod_with(FakeClient(error=True)).read()
    with pytest.raises(ConnectionError, match="lehnt die Leistungsvorgabe ab"):
        await rod_with(FakeClient(error=True)).set_power(1000)
    with pytest.raises(ConnectionError, match="antwortet nicht"):
        await rod_with(FakeClient(raises=True)).read()
    with pytest.raises(ConnectionError, match="antwortet nicht"):
        await rod_with(FakeClient(raises=True)).set_power(1000)


async def test_unreachable_rod():
    rod = MyPvHeatingRod("127.0.0.1", port=1, timeout=1)
    with pytest.raises(ConnectionError, match="nicht erreichbar"):
        await rod.read()
    assert rod._client is None


async def test_round_trip_with_simulator():
    sim = SimulatedHeatingRod()
    server = await asyncio.start_server(sim.serve_client, "127.0.0.1", 0)
    async with server:
        rod = MyPvHeatingRod("127.0.0.1", port=server.sockets[0].getsockname()[1])
        try:
            await rod.set_power(2500)
            result = await rod.read()
        finally:
            rod.close()
    assert sim.writes == [2500]
    assert (result.setpoint_w, result.temperature_c, result.target_c, result.status) == (2500, 45.0, 60.0, 2)
    assert result.heating and result.power_w == 2500
