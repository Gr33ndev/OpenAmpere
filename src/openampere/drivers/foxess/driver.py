"""FoxESS H3 driver over Modbus TCP."""

from __future__ import annotations

import asyncio
import logging
import re
import time

from ..base import BatterySettings, DeviceInfo, EnergyCounters, ExportLimit, PvInput, Snapshot, WorkMode
from ..modbus import (ModbusDevice, ModbusIllegalError, ModbusReadError,  # noqa: F401  (re-exported)
                      ModbusTransientError, _ascii)
from .registers import (H3_LEGACY, H3_NEW, MAPS, MODEL_ADDRESS, MODEL_LENGTH, SERIAL_ADDRESS,
                        Reg, RegisterMap, decode, encode)

log = logging.getLogger(__name__)

_NEW_MODEL = re.compile(r"^(H3-[\d.]+-(Smart|M)|[HP]3-Pro-[\d.]+)", re.I)
_LEGACY_MODEL = re.compile(r"^H3-[\d.]+", re.I)
_RATED_KW = re.compile(r"^[HP]3-(?:Pro-)?(\d+(?:\.\d+)?)", re.I)  # "H3-10.0-Smart" -> 10.0 kW


def rated_power_w(model: str | None) -> int | None:
    match = _RATED_KW.match(model or "")
    return int(float(match.group(1)) * 1000) if match else None


class FoxessDriver(ModbusDevice):
    """FoxESS H3 family (H3, H3 Smart, H3 Pro); register map and function code auto-detected."""

    KEY = "foxess"
    DEFAULT_UNITS = (247,)

    def __init__(self, host: str, port: int = 502, unit: int = 247, *,
                 register_map: str = "auto", read_function: str = "auto", timeout: float = 3.0,
                 read_attempts: int = 3) -> None:
        super().__init__(host, port, unit, timeout=timeout, read_attempts=read_attempts)
        self._map_choice = register_map
        self._fc_choice = read_function
        self.map: RegisterMap | None = None
        self.read_function = 4

    # ---- detection -------------------------------------------------------

    async def _detect(self) -> DeviceInfo:
        functions = {"input": (4,), "holding": (3,)}.get(self._fc_choice, (4, 3))
        model = ""
        found = await self._probe(MODEL_ADDRESS, MODEL_LENGTH, functions)
        if found:
            model = _ascii(found[1])

        new_readable = legacy_readable = None  # probed lazily

        async def has_new() -> bool:
            nonlocal new_readable
            if new_readable is None:
                new_readable = bool(await self._probe(39601, 2, functions))
            return new_readable

        async def has_legacy() -> bool:
            nonlocal legacy_readable
            if legacy_readable is None:
                legacy_readable = bool(await self._probe(32000, 2, functions))
            return legacy_readable

        if self._map_choice in MAPS:
            self.map = MAPS[self._map_choice]
        # the model string is only a hint: always confirm with the counter registers, prefer the newer map
        elif await has_new():
            self.map = H3_NEW
        elif await has_legacy():
            self.map = H3_LEGACY
        else:
            raise ConnectionError(f"unsupported device (model string {model!r})")
        if self._map_choice not in MAPS and _NEW_MODEL.match(model) and self.map is not H3_NEW:
            log.warning("model %r suggests the new register map, but only the legacy map answers", model)
        elif self._map_choice not in MAPS and _LEGACY_MODEL.match(model) and not _NEW_MODEL.match(model) \
                and self.map is H3_NEW:
            log.info("model %r answers on the new register map", model)

        # Which function code serves the measurement registers?
        if self._fc_choice in ("input", "holding"):
            self.read_function = 4 if self._fc_choice == "input" else 3
        else:
            probe_address = self.map.blocks[-1][0]
            preferred = (self.map.read_function, 7 - self.map.read_function)
            hit = await self._probe(probe_address, 2, preferred)
            if not hit:
                raise ConnectionError("measurement registers not readable with FC03 or FC04")
            self.read_function = hit[0]

        serial = firmware = None
        if self.map is H3_NEW:
            hit = await self._probe(SERIAL_ADDRESS, MODEL_LENGTH, (self.read_function, 3, 4))
            serial = _ascii(hit[1]) if hit else None
        hit = await self._probe(self.map.firmware[0], 3, (self.read_function, 3, 4))
        if hit:
            master, slave, manager = hit[1]
            firmware = f"{master / 100:.2f} / {slave / 100:.2f} / {manager:x}"

        self.info = DeviceInfo(manufacturer="FoxESS", model=model or "unknown", serial=serial,
                               firmware=firmware, register_map=self.map.name, driver=self.KEY, unit=self._unit,
                               rated_power_w=rated_power_w(model), supports_control=True)
        log.info("connected: %s (map %s, fc%02d)", self.info, self.map.name, self.read_function)
        return self.info

    # ---- reading ---------------------------------------------------------

    async def read_raw(self) -> dict[str, float]:
        assert self.map is not None, "connect() first"
        words: dict[int, int] = {}
        for start, count in self.map.blocks:
            try:
                words.update(await self._read_block(start, count))
            except ModbusIllegalError:
                if start not in self.map.optional_blocks:
                    raise
                # e.g. no second battery module or no MPPT detail registers on this model
        result = {}
        for name, reg in self.map.values.items():
            chunk = [words.get(reg.address + i) for i in range(reg.count)]
            if None not in chunk:
                result[name] = decode(reg, chunk)  # type: ignore[arg-type]
        return result

    async def read(self) -> Snapshot:
        raw = await self.read_raw()
        return to_snapshot(self.map, raw, time.time())

    # ---- settings --------------------------------------------------------

    async def read_settings(self) -> BatterySettings:
        assert self.map is not None
        s = self.map.settings
        values = {}
        for name in ("work_mode", "min_soc", "max_soc", "min_soc_on_grid"):
            try:
                values[name] = int(await self._read_reg(s[name], 3))
            except ModbusReadError:
                values[name] = None
        modes = {v: k for k, v in self.map.work_modes.items()}
        return BatterySettings(work_mode=modes.get(values["work_mode"]), min_soc=values["min_soc"],
                               max_soc=values["max_soc"], min_soc_on_grid=values["min_soc_on_grid"])

    async def write_work_mode(self, mode: WorkMode) -> None:
        assert self.map is not None
        await self._write(self.map.settings["work_mode"], self.map.work_modes[mode])

    async def write_soc_limits(self, *, min_soc: int | None = None, max_soc: int | None = None,
                               min_soc_on_grid: int | None = None) -> None:
        assert self.map is not None
        for name, value in (("min_soc", min_soc), ("max_soc", max_soc), ("min_soc_on_grid", min_soc_on_grid)):
            if value is None:
                continue
            low = 0 if name == "min_soc" else 10  # the outage floor may be 0 % (#69)
            if not low <= value <= 100:
                raise ValueError(f"{name} must be between {low} and 100 %")
            await self._write(self.map.settings[name], value)

    async def set_remote_power(self, watts: int, timeout_s: int) -> None:
        """Order matters: timeout first, then enable (single writes), then the power setpoint.
        The inverter's own watchdog ends remote control after timeout_s without a new command."""
        assert self.map is not None
        rated = rated_power_w(self.info.model if self.info else None) or 10_000
        if not -rated <= int(watts) <= rated:
            raise ValueError(f"remote power must be between -{rated} and {rated} W")
        if not 10 <= int(timeout_s) <= 3600:
            raise ValueError("remote timeout must be between 10 and 3600 s")
        s = self.map.settings
        await self._write(s["remote_timeout"], int(timeout_s))
        await self._write(s["remote_enable"], 1)
        self._remote_owned = int(timeout_s)
        await self._write(s["remote_power"], int(watts))

    async def release_remote_power(self, timeout_s: int | None = None) -> bool:
        """Ends remote control, but only if OpenAmpere started it – never someone else's (e.g. a smartbox).

        Ours means switched on with 1 and our watchdog timeout (the smartbox uses other values, #141). timeout_s
        recognises a leftover of OpenAmpere after a restart or a new driver object, when the in-memory flag is gone.
        The watchdog may leave the switch on (#141), so it is read back. Returns whether it switched it off."""
        assert self.map is not None
        s = self.map.settings
        timeout_s = getattr(self, "_remote_owned", None) or timeout_s
        enable, timeout = await self._read(s["remote_enable"].address, 2, 3)
        if not timeout_s or enable != 1 or timeout != timeout_s:
            self._remote_owned = None  # off already, or someone else's now
            return False
        await self._write(s["remote_enable"], 0)
        for wait_s in (0, 1, 2, 3):  # the read-back may be delayed by a few seconds (registers.md)
            await asyncio.sleep(wait_s)
            if not await self._read_reg(s["remote_enable"], 3):
                self._remote_owned = None
                return True
        raise ModbusReadError("remote control is still on after switching it off")

    async def read_export_limit(self) -> ExportLimit:
        assert self.map is not None
        reg = self.map.settings.get("export_limit")
        rated = rated_power_w(self.info.model if self.info else None)
        if reg is None:
            return ExportLimit(supported=False, rated_power_w=rated)
        try:
            value = int(await self._read_reg(reg, 3))
        except ModbusIllegalError:
            return ExportLimit(supported=False, rated_power_w=rated)
        return ExportLimit(supported=True, limit_w=value, rated_power_w=rated)

    async def write_export_limit(self, watts: int) -> None:
        assert self.map is not None
        reg = self.map.settings.get("export_limit")
        if reg is None:
            raise ModbusIllegalError("export limit not available on this device")
        if not 0 <= watts <= 99_999:
            raise ValueError("export limit out of range")
        await self._write(reg, int(watts))

    async def remote_active(self) -> bool:
        assert self.map is not None
        try:
            return bool(await self._read_reg(self.map.settings["remote_enable"], 3))
        except ModbusReadError:
            return False


def to_snapshot(register_map: RegisterMap | None, raw: dict[str, float], timestamp: float) -> Snapshot:
    g = raw.get
    def inputs(prefix: str, count: int) -> list[PvInput]:
        return [PvInput(power=g(f"{prefix}{i}_power"), voltage=g(f"{prefix}{i}_voltage"), current=g(f"{prefix}{i}_current"))
                for i in range(1, count + 1) if f"{prefix}{i}_power" in raw]

    if register_map is H3_LEGACY:
        export = sum(raw.get(k, 0) for k in ("grid_ct_r", "grid_ct_s", "grid_ct_t"))
        grid_power = -export
        house_power = sum(raw.get(k, 0) for k in ("load_r", "load_s", "load_t"))
        pv_inputs = inputs("pv", 2)
        pv_power = sum(i.power or 0 for i in pv_inputs) if pv_inputs else None
    else:
        grid_power = -g("grid_power") if "grid_power" in raw else None
        house_power = g("house_power")
        # MPPT trackers correspond to module arrays; fall back to the raw PV string inputs
        pv_inputs = inputs("mppt", 3) or inputs("pv", 4)
        pv_power = g("pv_power", sum(i.power or 0 for i in pv_inputs) if pv_inputs else None)

    temperatures = {key: raw[reg] for key, reg in (
        ("inverter", "inverter_temperature"), ("ambient", "ambient_temperature"),
        ("battery", "battery_temperature"), ("battery_cell_max", "battery_cell_temp_max"),
        ("battery_cell_min", "battery_cell_temp_min"), ("battery2", "battery2_temperature"),
        ("battery2_cell_max", "battery2_cell_temp_max"), ("battery2_cell_min", "battery2_cell_temp_min"),
    ) if reg in raw}
    if register_map is H3_NEW and not g("bms1_connected", 1):
        for key in ("battery", "battery_cell_max", "battery_cell_min"):
            temperatures.pop(key, None)
    if not any(temperatures.get(k) for k in ("battery2", "battery2_cell_max", "battery2_cell_min")):
        for key in ("battery2", "battery2_cell_max", "battery2_cell_min"):  # no second battery module
            temperatures.pop(key, None)

    soc = g("battery_soc")
    if register_map is H3_NEW and not g("bms1_connected", 1):
        soc = None

    off_grid = None
    if "off_grid_flags" in raw:
        off_grid = bool(int(raw["off_grid_flags"]) & 1)

    def counters(suffix: str) -> EnergyCounters:
        return EnergyCounters(
            pv=g(f"pv_{suffix}"), load=g(f"load_{suffix}"),
            grid_import=g(f"grid_import_{suffix}"), grid_export=g(f"grid_export_{suffix}"),
            battery_charge=g(f"battery_charge_{suffix}"), battery_discharge=g(f"battery_discharge_{suffix}"),
        )

    return Snapshot(
        timestamp=timestamp,
        pv_power=pv_power,
        house_power=house_power,
        grid_power=grid_power,
        battery_power=g("battery_power"),
        battery_soc=soc,
        pv_inputs=pv_inputs,
        temperatures=temperatures,
        battery_voltage=g("battery_voltage"),
        battery_current=g("battery_current"),
        battery_temperature=g("battery_temperature"),
        battery_soh=g("battery_soh"),
        inverter_state=int(raw["inverter_state"]) if "inverter_state" in raw else None,
        off_grid=off_grid,
        alarms=[int(raw[k]) for k in ("alarm1", "alarm2", "alarm3") if k in raw],
        totals=counters("total"),
        today=counters("today"),
    )
