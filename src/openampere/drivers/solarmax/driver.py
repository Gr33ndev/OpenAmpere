# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Solarmax MAX.STORAGE driver (#519). Display only: it never writes."""

from __future__ import annotations

import logging
import time

from ..base import BatterySettings, DeviceInfo, EnergyCounters, ExportLimit, Snapshot, WorkMode
from ..modbus import DeviceRefused, ModbusDevice, ModbusIllegalError, ModbusTransientError
from ..regs import counter_step_wh, decode
from .registers import COUNTER_BLOCK, DEVICE_STATES, ERROR_STATE, READ_FUNCTION, STANDARD_BLOCK, VALUES

log = logging.getLogger(__name__)

# plausibility of the identification: nominal power of a MAX.STORAGE (6 to 15 kW, with room), firmware "x.y.z"
RATED_RANGE_W = range(1000, 30001)
FIRMWARE_RANGE = range(100, 1000)
EXTERNAL_FROM_W = 50  # the plant's DC power above the inverter's own: another inverter is connected
COUNTER_FAILURES = 3  # the counters stop being read after this many reads in a row without an answer


class ReadOnly(DeviceRefused):
    """OpenAmpere only shows the values of this device."""


def refuse() -> None:
    raise ReadOnly("Beim Solarmax-Speicher zeigt OpenAmpere nur die Werte an und ändert keine Einstellungen.")


class SolarmaxDriver(ModbusDevice):
    KEY = "solarmax"
    DEFAULT_UNITS = (1,)

    def __init__(self, host: str, port: int = 502, unit: int = 1, *, timeout: float = 3.0, read_attempts: int = 3,
                 **_ignored) -> None:
        super().__init__(host, port, unit, timeout=timeout, read_attempts=read_attempts)
        self.read_function = READ_FUNCTION
        self.has_counters = True  # False once the device rejects them (no "Connectivity+") or keeps not answering
        self._counter_failures = 0
        self.has_external = False  # another inverter connected to the MAX.STORAGE was seen (112 above 110)

    async def _detect(self) -> DeviceInfo:
        start, count = STANDARD_BLOCK
        hit = await self._probe(start, count, (READ_FUNCTION,))
        if not hit:
            raise ConnectionError("no Solarmax MAX.STORAGE registers")
        raw = values({start + i: word for i, word in enumerate(hit[1])})
        firmware, state, rated = int(raw["firmware"]), int(raw["device_state"]), int(raw["rated_power"])
        # plausibility: other devices answering FC04 at 100 would not fit all of these
        if (firmware not in FIRMWARE_RANGE or state not in DEVICE_STATES or rated not in RATED_RANGE_W
                or raw["battery_soc"] > 100 or raw["battery_capacity"] > 100):
            raise ConnectionError("implausible Solarmax values")
        serial = str(int(raw["serial"])) if raw["serial"] else None
        self.info = DeviceInfo(manufacturer="Solarmax", model="MAX.STORAGE", serial=serial,
                               firmware=".".join(str(firmware)), register_map="solarmax_max_storage",
                               driver=self.KEY, unit=self._unit, rated_power_w=rated, supports_control=False,
                               energy_step_wh=counter_step_wh(VALUES))
        log.info("connected: %s", self.info)
        return self.info

    async def read_raw(self) -> dict[str, float]:
        words = await self._read_block(*STANDARD_BLOCK)
        if self.has_counters:
            # optional: what they do without "Connectivity+" is not known, so the live values never wait for them
            try:
                words.update(await self._read_block(*COUNTER_BLOCK))
                self._counter_failures = 0
            except ModbusIllegalError:
                self.has_counters = False
                log.info("Solarmax energy counters not readable (no Connectivity+)")
            except ModbusTransientError as err:
                self._counter_failures += 1
                if self._counter_failures >= COUNTER_FAILURES:
                    self.has_counters = False
                    log.info("Solarmax energy counters do not answer, no longer read: %s", err)
        return values(words)

    async def read(self) -> Snapshot:
        raw = await self.read_raw()
        if not self.has_external and raw.get("pv_plant_power", 0) - raw.get("pv_power", 0) > EXTERNAL_FROM_W:
            self.has_external = True
        return to_snapshot(raw, time.time(), self.has_external)

    # ---- display only -------------------------------------------------------

    async def read_settings(self) -> BatterySettings:
        return BatterySettings()

    async def read_export_limit(self) -> ExportLimit:
        return ExportLimit(supported=False, rated_power_w=self.info.rated_power_w if self.info else None)

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


def values(words: dict[int, int]) -> dict[str, float]:
    """The decoded values whose registers were read."""
    result = {}
    for name, reg in VALUES.items():
        chunk = [words.get(reg.address + i) for i in range(reg.count)]
        if None not in chunk:
            result[name] = decode(reg, chunk)  # type: ignore[arg-type]
    return result


def to_snapshot(raw: dict[str, float], timestamp: float, external: bool = False) -> Snapshot:
    """external: another inverter was seen on the MAX.STORAGE; until then there is no second PV inverter to show (the
    app offers to count one only when the device reports it)."""
    g = raw.get
    negate = lambda key: None if g(key) is None else -g(key)  # noqa: E731
    pv, plant = g("pv_power"), g("pv_plant_power")
    state = int(raw["device_state"]) if "device_state" in raw else None
    return Snapshot(
        timestamp=timestamp,
        pv_power=pv,
        # other inverters connected to the MAX.STORAGE: the plant's DC power minus its own
        pv_external_power=None if not external or pv is None or plant is None else max(0.0, plant - pv),
        house_power=g("house_power"),
        grid_power=negate("grid_power"),  # Solarmax: + = export
        battery_power=negate("battery_power"),  # Solarmax: + = charging
        battery_soc=g("battery_soc"),
        inverter_state=state,
        alarms=[state] if state == ERROR_STATE else [],
        totals=counters(raw),
        today=EnergyCounters(),
    )


def counters(raw: dict[str, float]) -> EnergyCounters:
    """The lifetime counters of "Connectivity+". Without it a device may answer with zeros: all zero means not
    available, never counters that start at 0. There is no load counter; the consumption is the balance of the others."""
    keys = ("pv_total", "grid_import_total", "grid_export_total", "battery_charge_total", "battery_discharge_total")
    if any(key not in raw for key in keys) or not any(raw[key] for key in keys):
        return EnergyCounters()
    pv, imp, exp, charge, discharge = (raw[key] for key in keys)
    return EnergyCounters(pv=pv, grid_import=imp, grid_export=exp, battery_charge=charge, battery_discharge=discharge)
