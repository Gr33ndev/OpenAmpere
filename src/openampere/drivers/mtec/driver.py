# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""M-TEC Energy-Butler GEN3 driver (#520). Display only: it never writes."""

from __future__ import annotations

import logging
import time

from pymodbus import FramerType

from ..base import BatterySettings, DeviceInfo, EnergyCounters, ExportLimit, PvInput, Snapshot, WorkMode
from ..modbus import DeviceRefused, ModbusDevice, ModbusIllegalError, ModbusTransientError, ascii_text
from ..regs import counter_step_wh, decode
from .registers import BLOCKS, FAULT_STATE, OFF_GRID_STATE, READ_FUNCTION, RUNNING_STATES, VALUES

log = logging.getLogger(__name__)

FRAMERS = (FramerType.SOCKET, FramerType.RTU)  # plain Modbus TCP first, then RTU frames over TCP (port 5743)


class ReadOnly(DeviceRefused):
    """OpenAmpere only shows the values of this device."""


def refuse() -> None:
    raise ReadOnly("Beim M-TEC Energy-Butler zeigt OpenAmpere nur die Werte an und ändert keine Einstellungen.")


class MtecDriver(ModbusDevice):
    KEY = "mtec"
    DEFAULT_UNITS = (252, 255, 247)

    def __init__(self, host: str, port: int = 502, unit: int = 252, *, timeout: float = 3.0, read_attempts: int = 3,
                 **_ignored) -> None:
        super().__init__(host, port, unit, timeout=timeout, read_attempts=read_attempts)
        self.read_function = READ_FUNCTION

    async def _detect(self) -> DeviceInfo:
        last: Exception | None = None
        for framer in FRAMERS:
            if framer is not self.framer:
                await self.close()  # a new connection with the other framing
                self.framer = framer
            try:
                return await self._identify()
            except ModbusTransientError as err:  # no answer in this framing (or not from this unit)
                last = err
                log.debug("M-TEC unit %s, %s framing: %s", self._unit, framer.value, err)
        raise ConnectionError(f"no M-TEC answer on unit {self._unit}: {last}")

    async def _identify(self) -> DeviceInfo:
        serial_words = await self._read(10000, 8, READ_FUNCTION)
        state = (await self._read(10105, 1, READ_FUNCTION))[0]
        soc_raw = (await self._read(33000, 1, READ_FUNCTION))[0]
        serial = ascii_text(serial_words)
        # plausibility: a readable serial number, a known running state and a state of charge within 0–100 %
        if len(serial) < 6 or state not in RUNNING_STATES or soc_raw > 10000:
            raise ConnectionError("implausible M-TEC values")
        firmware = None
        try:
            words = await self._read(10011, 4, READ_FUNCTION)
            parts = [".".join(str(b) for w in half for b in (w >> 8, w & 0xFF)) for half in (words[:2], words[2:])]
            firmware = " / ".join(parts)  # two versions of four bytes (as in the app: "V27.53.05.00")
        except ModbusIllegalError:
            pass
        self.info = DeviceInfo(manufacturer="M-TEC", model="Energy-Butler GEN3", serial=serial, firmware=firmware,
                               register_map=f"mtec_gen3 ({self.framer.value})", driver=self.KEY, unit=self._unit,
                               supports_control=False, energy_step_wh=counter_step_wh(VALUES))
        log.info("connected: %s", self.info)
        return self.info

    async def read_raw(self) -> dict[str, float]:
        words: dict[int, int] = {}
        for start, count in BLOCKS:
            try:
                words.update(await self._read_block(start, count))
            except ModbusIllegalError:
                continue  # a block this firmware does not have
        result = {}
        for name, reg in VALUES.items():
            chunk = [words.get(reg.address + i) for i in range(reg.count)]
            if None not in chunk:
                result[name] = decode(reg, chunk)  # type: ignore[arg-type]
        return result

    async def read(self) -> Snapshot:
        return to_snapshot(await self.read_raw(), time.time())

    # ---- display only -------------------------------------------------------

    async def read_settings(self) -> BatterySettings:
        return BatterySettings()

    async def read_export_limit(self) -> ExportLimit:
        return ExportLimit(supported=False)

    async def write_work_mode(self, mode: WorkMode) -> None:
        refuse()

    async def write_soc_limits(self, *, min_soc: int | None = None, max_soc: int | None = None,
                               min_soc_on_grid: int | None = None) -> None:
        refuse()

    async def set_remote_power(self, watts: int, timeout_s: int, restore: dict | None = None) -> None:
        refuse()

    async def release_remote_power(self, timeout_s: int | None = None, restore: dict | None = None) -> bool:
        return False  # OpenAmpere never started anything here

    async def remote_active(self) -> bool:
        return False

    async def write_export_limit(self, watts: int) -> None:
        refuse()


def to_snapshot(raw: dict[str, float], timestamp: float) -> Snapshot:
    g = raw.get
    grid = None if g("grid_power") is None else -g("grid_power")  # M-TEC: + = export
    # the house: what the inverter puts out on its AC side minus what goes to the grid (MTECmqtt)
    house = None if g("inverter_power") is None or g("grid_power") is None else g("inverter_power") - g("grid_power")
    inputs = [PvInput(power=g(f"pv{i}_power"), voltage=g(f"pv{i}_voltage"), current=g(f"pv{i}_current"))
              for i in (1, 2) if f"pv{i}_power" in raw]
    state = int(raw["running_state"]) if "running_state" in raw else None

    def counters(suffix: str) -> EnergyCounters:
        return EnergyCounters(pv=g(f"pv_{suffix}"), grid_import=g(f"grid_import_{suffix}"),
                              grid_export=g(f"grid_export_{suffix}"), battery_charge=g(f"battery_charge_{suffix}"),
                              battery_discharge=g(f"battery_discharge_{suffix}"))

    return Snapshot(
        timestamp=timestamp,
        pv_power=g("pv_power"),
        house_power=house,
        grid_power=grid,
        battery_power=g("battery_power"),
        battery_soc=g("battery_soc"),
        battery_soh=g("battery_soh"),
        battery_voltage=g("battery_voltage"),
        battery_current=g("battery_current"),
        pv_inputs=inputs,
        inverter_state=state,
        off_grid=None if state is None else state == OFF_GRID_STATE,
        alarms=[int(raw[k]) for k in ("fault1", "fault2") if k in raw] if state == FAULT_STATE else [],
        totals=counters("total"),
        today=counters("today"),
    )
