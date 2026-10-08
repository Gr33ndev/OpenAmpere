"""SAJ H2 / HS2 driver. Read-only until verified on real hardware."""

from __future__ import annotations

import logging
import time

from ..base import BatterySettings, DeviceInfo, EnergyCounters, ExportLimit, PvInput, Snapshot, WorkMode
from ..modbus import ModbusDevice, ModbusIllegalError, ascii_text
from ..regs import counter_step_wh, decode
from .registers import (BLOCKS, DEVICE_TYPE_RANGE, DEVICE_TYPES, INFO_ADDRESS, INFO_LENGTH, OFF_GRID_MODE,
                        READ_FUNCTION, VALUES)

log = logging.getLogger(__name__)


class NotSupported(ModbusIllegalError):
    """Writing is not (yet) supported for this device."""


class SajDriver(ModbusDevice):
    KEY = "saj"
    DEFAULT_UNITS = (1, 2)

    def __init__(self, host: str, port: int = 502, unit: int = 1, *, timeout: float = 3.0, read_attempts: int = 3,
                 **_ignored) -> None:
        super().__init__(host, port, unit, timeout=timeout, read_attempts=read_attempts)
        self.read_function = READ_FUNCTION
        self.grid_counter_source: str | None = None  # "sum" or "l1", fixed after the first reading

    async def _detect(self) -> DeviceInfo:
        hit = await self._probe(INFO_ADDRESS, INFO_LENGTH, (READ_FUNCTION,))
        if not hit:
            raise ConnectionError("no SAJ identification block")
        words = hit[1]
        device_type, rated_w = words[0], words[1]
        serial = ascii_text(words[3:13]) or None
        product = ascii_text(words[13:23]) or None
        if device_type not in DEVICE_TYPES and device_type not in DEVICE_TYPE_RANGE:
            raise ConnectionError(f"unknown SAJ device type 0x{device_type:04X}")
        # plausibility: running mode and SoC must be in range, otherwise this is not a SAJ hybrid
        mode = (await self._read(0x4004, 1, READ_FUNCTION))[0]
        soc_raw = (await self._read(0x406F, 1, READ_FUNCTION))[0]
        if mode > 9 or soc_raw > 10000:
            raise ConnectionError("implausible SAJ values")
        if device_type not in DEVICE_TYPES:
            log.warning("unknown SAJ device type 0x%04X - treating it like an H2", device_type)
        model = DEVICE_TYPES.get(device_type, f"Typ 0x{device_type:04X}")  # manufacturer is shown separately
        versions = words[23:29]
        firmware = " / ".join(f"{v / 1000:.3f}" for v in versions[:3]) if any(versions) else None
        self.info = DeviceInfo(manufacturer="SAJ", model=model, serial=serial or product, firmware=firmware,
                               register_map="saj_h2", driver=self.KEY, unit=self._unit,
                               rated_power_w=rated_w or None, supports_control=False,
                               energy_step_wh=counter_step_wh(VALUES))
        log.info("connected: %s", self.info)
        return self.info

    async def read_raw(self) -> dict[str, float]:
        words: dict[int, int] = {}
        for start, count in BLOCKS:
            try:
                words.update(await self._read_block(start, count))
            except ModbusIllegalError:
                continue  # optional block (e.g. summed counters) not available on this firmware
        result = {}
        for name, reg in VALUES.items():
            chunk = [words.get(reg.address + i) for i in range(reg.count)]
            if None not in chunk:
                result[name] = decode(reg, chunk)  # type: ignore[arg-type]
        return result

    async def read(self) -> Snapshot:
        raw = await self.read_raw()
        if self.grid_counter_source is None and ("grid_import_sum_total" in raw or "grid_import_l1_total" in raw):
            self.grid_counter_source = "sum" if "grid_import_sum_total" in raw else "l1"
        return to_snapshot(raw, time.time(), self.grid_counter_source)

    # ---- settings: read-only for now --------------------------------------

    async def read_settings(self) -> BatterySettings:
        raw = await self.read_raw()
        modes = {0: WorkMode.SELF_USE, 2: WorkMode.BACKUP}
        as_int = lambda key: int(raw[key]) if key in raw else None  # noqa: E731
        return BatterySettings(work_mode=modes.get(as_int("app_mode")), min_soc=as_int("min_soc"),
                               max_soc=as_int("max_soc"), min_soc_on_grid=as_int("reserve_soc"))

    async def read_export_limit(self) -> ExportLimit:
        return ExportLimit(supported=False, rated_power_w=self.info.rated_power_w if self.info else None)

    async def _not_supported(self, *args, **kwargs):
        raise NotSupported("Steuerung ist für SAJ-Geräte noch nicht freigegeben")

    write_work_mode = write_soc_limits = set_remote_power = release_remote_power = write_export_limit = _not_supported

    async def remote_active(self) -> bool:
        return False


def to_snapshot(raw: dict[str, float], timestamp: float, grid_source: str | None = None) -> Snapshot:
    """grid_source pins the grid counters to the summed ("sum") or phase-1 ("l1") registers: switching
    between them from one reading to the next would look like a huge energy jump."""
    g = raw.get
    inputs = [PvInput(power=g(f"pv{i}_power"), voltage=g(f"pv{i}_voltage"), current=g(f"pv{i}_current"))
              for i in range(1, 5) if f"pv{i}_power" in raw]
    pv_power = g("pv_power", sum(i.power or 0 for i in inputs) if inputs else None)

    def first(*keys):
        return next((raw[k] for k in keys if k in raw), None)

    def grid(pattern: str):
        if grid_source:
            return g(pattern.format(grid_source))
        return first(pattern.format("sum"), pattern.format("l1"))

    def counters(suffix: str) -> EnergyCounters:
        return EnergyCounters(
            pv=g(f"pv_{suffix}"), load=g(f"load_{suffix}"),
            # three-phase: the summed counters cover all phases; fall back to L1 on older firmware
            grid_import=grid(f"grid_import_{{}}_{suffix}"),
            grid_export=grid(f"grid_export_{{}}_{suffix}"),
            battery_charge=g(f"battery_charge_{suffix}"), battery_discharge=g(f"battery_discharge_{suffix}"))

    temperatures = {k: v for k, v in (("inverter", g("inverter_temperature")), ("ambient", g("ambient_temperature")),
                                      ("battery", first("battery_temperature", "battery1_temperature"))) if v is not None}
    return Snapshot(
        timestamp=timestamp,
        pv_power=pv_power,
        house_power=g("house_power"),
        grid_power=g("grid_power"),
        battery_power=g("battery_power"),
        battery_soc=round(raw["battery_soc"], 1) if "battery_soc" in raw else None,
        pv_inputs=inputs,
        temperatures=temperatures,
        battery_voltage=g("battery_voltage"),
        battery_current=g("battery_current"),
        battery_temperature=temperatures.get("battery"),
        battery_soh=g("battery_soh"),
        inverter_state=int(raw["running_mode"]) if "running_mode" in raw else None,
        off_grid=int(raw["running_mode"]) == OFF_GRID_MODE if "running_mode" in raw else None,
        alarms=[int(raw[k]) for k in ("fault1", "fault2", "fault3") if k in raw],
        totals=counters("total"),
        today=counters("today"),
    )
