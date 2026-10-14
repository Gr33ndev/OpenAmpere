# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""SAJ H2 / HS2 driver. Control (battery settings, export limit, grid charging) for the types in CONTROL_TYPES."""

from __future__ import annotations

import logging
import math
import time
from datetime import datetime

from ..base import BatterySettings, DeviceInfo, EnergyCounters, ExportLimit, PvInput, Snapshot, WorkMode
from ..modbus import DeviceRefused, ModbusDevice, ModbusIllegalError, ModbusReadError, ascii_text
from ..regs import Reg, counter_step_wh, decode
from .registers import (APP_MODE_ADDRESS, APP_MODE_TIME, APP_MODES, BACKUP_SOC_ADDRESS, BLOCKS, CHARGE_ENABLE_ADDRESS, CHARGE_SLOT,
                        CHARGE_SLOT_ADDRESS, CONTROL_TYPES, DEPTH_ADDRESS, DEVICE_TYPE_RANGE, DEVICE_TYPES,
                        EXPORT_LIMIT_ADDRESS, EXPORT_MODE_ADDRESS, EXPORT_POWER_MODES, INFO_ADDRESS, INFO_LENGTH,
                        OFF_GRID_MODE, READ_FUNCTION, SOC_ADDRESSES, TIME_ADDRESS, TOU_OUTSIDE_ADDRESS, VALUES)

log = logging.getLogger(__name__)


class NotSupported(DeviceRefused):
    """Writing is not possible in the inverter's current state; the message says what the owner can do."""


class SajDriver(ModbusDevice):
    KEY = "saj"
    DEFAULT_UNITS = (1, 2)

    def __init__(self, host: str, port: int = 502, unit: int = 1, *, timeout: float = 3.0, read_attempts: int = 3,
                 **_ignored) -> None:
        super().__init__(host, port, unit, timeout=timeout, read_attempts=read_attempts)
        self.read_function = READ_FUNCTION
        self.grid_counter_source: str | None = None  # "sum" or "l1", fixed after the first reading
        self.has_depth_registers = True  # False once the device rejects them (some firmware)
        self._remote: dict | None = None  # OpenAmpere's charge slot and the owner's values it replaced

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
                               rated_power_w=rated_w or None, supports_control=device_type in CONTROL_TYPES,
                               energy_step_wh=counter_step_wh(VALUES), work_modes=tuple(APP_MODES.values()),
                               charging_continues_min=SLOT_MINUTES, backup_reserve=True)
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

    # ---- settings and control (#377) ----------------------------------------

    async def read_settings(self) -> BatterySettings:
        raw = await self.read_raw()
        settings = None
        if self.has_depth_registers:
            try:
                settings = await self._read(DEPTH_ADDRESS, 4, READ_FUNCTION)
            except ModbusIllegalError:
                self.has_depth_registers = False
            except ModbusReadError as err:
                log.debug("settings registers not readable, using the read-back registers: %s", err)
        if settings and self._remote and settings[3] == APP_MODE_TIME:
            settings[3] = self._remote["app_mode"]  # time-of-use only while OpenAmpere charges: the owner's mode
        backup = None
        if settings:
            try:
                backup = (await self._read(BACKUP_SOC_ADDRESS, 1, READ_FUNCTION))[0]
            except ModbusReadError as err:
                log.debug("reserve of the backup mode not readable: %s", err)
        return to_settings(raw, settings, backup)

    async def write_backup_soc(self, value: int) -> None:
        """The reserve the backup work mode keeps for a power cut (0x3651, #380); above it the battery supplies the
        house as usual."""
        if not 10 <= value <= 100:
            raise ValueError("backup_soc must be between 10 and 100 %")
        await self._write(Reg(BACKUP_SOC_ADDRESS), value)

    async def write_work_mode(self, mode: WorkMode) -> None:
        values = {name: value for value, name in APP_MODES.items()}
        if mode.value not in values:
            raise ValueError(f"work mode {mode.value} is not available on SAJ")
        if self._remote:  # charging uses time-of-use: the new mode applies when charging ends
            self._remote["app_mode"] = values[mode.value]
            return
        await self._write(Reg(APP_MODE_ADDRESS), values[mode.value])

    async def write_soc_limits(self, *, min_soc: int | None = None, max_soc: int | None = None,
                               min_soc_on_grid: int | None = None) -> None:
        for name, value in (("min_soc", min_soc), ("max_soc", max_soc), ("min_soc_on_grid", min_soc_on_grid)):
            if value is None:
                continue
            low = 10 if name == "max_soc" else 0
            if not low <= value <= 100:
                raise ValueError(f"{name} must be between {low} and 100 %")
            await self._write(Reg(SOC_ADDRESSES[name]), value)

    # grid charging: SAJ has no remote control that ends by itself (no timeout in the protocol, passive mode stays on
    # for hours, stanus74/home-assistant-saj-h2-modbus#157). A charge slot of the time-of-use mode ends at its end
    # time, so OpenAmpere sets a short one and extends it while it charges: if OpenAmpere stops, charging stops at
    # the end of the slot.

    async def set_remote_power(self, watts: int, timeout_s: int, restore: dict | None = None) -> dict:
        """Charges with -watts through OpenAmpere's charge slot (only charging). restore: what the previous call
        returned, also from before a restart. Returns the owner's values to put back and OpenAmpere's slot."""
        rated = (self.info.rated_power_w if self.info else None) or 10_000
        if not -rated <= int(watts) < 0:
            raise ValueError(f"charging power must be between -{rated} and -1 W")
        percent = min(100, max(1, math.ceil(-int(watts) * 100 / rated)))
        now = await self._clock()
        mask, slot, app_mode, outside = await self._charge_state()
        prior = self._remote or restore
        if mask & SLOT_BIT and not (prior and slot == prior.get("slot_ours")):
            # the owner uses this slot (or someone switched on other values): never take it over
            raise NotSupported(f"Ladezeit {CHARGE_SLOT} ist im Wechselrichter schon belegt. Bitte in der "
                               "SAJ-App freigeben, dann kann OpenAmpere aus dem Netz laden.")
        if prior:
            # a running session keeps the owner's values from its start, also when someone changed the slot or the
            # mode in between: otherwise these would be taken for the owner's and put back at the end (#382)
            state = dict(prior)
            if slot != state.get("slot_ours"):
                state["slot_ours"] = None  # changed while switched off: written again
        else:
            state = {"app_mode": app_mode, "outside": outside, "slot": slot, "slot_ours": None}
        ours = state["slot_ours"]
        wanted = charge_slot(now, percent)
        if ours is not None and ours[2] == wanted[2]:
            wanted[0] = ours[0]  # the same day: only the end moves, one stored register every few minutes
        if ours is None or ours[2] != wanted[2] or minutes_left(now, ours) < EXTEND_BELOW_MIN:
            for i, word in enumerate(wanted):
                if ours is None or ours[i] != word:
                    await self._write(Reg(CHARGE_SLOT_ADDRESS + i), word)
            state = {**state, "slot_ours": wanted}
        self._remote = state  # before switching on: a start that fails half way is switched off by the release
        # switched on after the slot holds OpenAmpere's values, time-of-use last; with every command, like the remote
        # control of FoxESS: what another device (e.g. the old IQ box) changed meanwhile is set again (#382)
        if outside != 1:
            await self._write(Reg(TOU_OUTSIDE_ADDRESS), 1)  # outside the slot: self-use, not standby
        if not mask & SLOT_BIT:
            await self._write(Reg(CHARGE_ENABLE_ADDRESS), mask | SLOT_BIT)
        if app_mode != APP_MODE_TIME:
            await self._write(Reg(APP_MODE_ADDRESS), APP_MODE_TIME)
        return dict(state)

    async def release_remote_power(self, timeout_s: int | None = None, restore: dict | None = None) -> bool:
        """Switches OpenAmpere's charge slot off and puts the owner's values back – only if the slot still holds
        OpenAmpere's values, never someone else's. Returns whether it switched it off."""
        state = self._remote or restore
        if not state or not state.get("slot_ours"):
            self._remote = None
            return False
        mask, slot, app_mode, outside = await self._charge_state()
        if slot != state["slot_ours"]:
            self._remote = None  # changed by someone else (or put back already): left as it is
            return False
        switched_on = bool(mask & SLOT_BIT)  # off: a start that failed half way, the owner's values are put back
        if switched_on:
            await self._write(Reg(CHARGE_ENABLE_ADDRESS), mask & ~SLOT_BIT)
        if app_mode == APP_MODE_TIME and state["app_mode"] != APP_MODE_TIME:
            await self._write(Reg(APP_MODE_ADDRESS), state["app_mode"])
        if outside != state["outside"]:
            await self._write(Reg(TOU_OUTSIDE_ADDRESS), state["outside"])
        for i, word in enumerate(state["slot"]):  # the owner's slot as it was, switched off as before
            await self._write(Reg(CHARGE_SLOT_ADDRESS + i), word)
        if (await self._read(CHARGE_ENABLE_ADDRESS, 1, READ_FUNCTION))[0] & SLOT_BIT:
            raise ModbusReadError("the charge slot is still on after switching it off")
        self._remote = None
        return switched_on

    async def remote_active(self) -> bool:
        """Another device controls the inverter: passive mode (only external controllers use it)."""
        try:
            return (await self._read(APP_MODE_ADDRESS, 1, READ_FUNCTION))[0] == APP_MODE_PASSIVE
        except ModbusReadError:
            return False

    async def _charge_state(self) -> tuple[int, list[int], int, int]:
        mask = (await self._read(CHARGE_ENABLE_ADDRESS, 1, READ_FUNCTION))[0]
        slot = await self._read(CHARGE_SLOT_ADDRESS, 3, READ_FUNCTION)
        app_mode = (await self._read(APP_MODE_ADDRESS, 1, READ_FUNCTION))[0]
        outside = (await self._read(TOU_OUTSIDE_ADDRESS, 1, READ_FUNCTION))[0]
        return mask, list(slot), app_mode, outside

    async def _clock(self) -> datetime:
        """The inverter's clock: the slot follows it, not the clock of the computer OpenAmpere runs on."""
        words = await self._read(TIME_ADDRESS, 4, READ_FUNCTION)
        try:
            return datetime(words[0], words[1] >> 8, words[1] & 0xFF, words[2] >> 8, words[2] & 0xFF, words[3] >> 8)
        except ValueError as err:
            raise ModbusReadError(f"implausible inverter clock {words}") from err

    async def read_export_limit(self) -> ExportLimit:
        rated = self.info.rated_power_w if self.info else None
        if not rated:
            return ExportLimit(supported=False)
        try:
            limit, _, mode = await self._read(EXPORT_LIMIT_ADDRESS, 3, READ_FUNCTION)
        except ModbusIllegalError:
            return ExportLimit(supported=False, rated_power_w=rated)
        if mode == 2:  # limited by phase current (0x365B, A): not a power OpenAmpere can show or set
            return ExportLimit(supported=False, rated_power_w=rated)
        # switched off: no limit, the inverter feeds in up to its rated power
        limit_w = min(rated, limit * rated // 1000) if mode in EXPORT_POWER_MODES else rated
        return ExportLimit(supported=True, limit_w=limit_w, rated_power_w=rated, step_w=math.ceil(rated / 1000))

    async def write_export_limit(self, watts: int) -> None:
        rated = self.info.rated_power_w if self.info else None
        if not rated:
            raise ModbusIllegalError("rated power unknown: the export limit cannot be converted")
        if not 0 <= watts <= rated:
            raise ValueError("export limit out of range")
        mode = (await self._read(EXPORT_MODE_ADDRESS, 1, READ_FUNCTION))[0]
        if mode == 2:
            raise ModbusIllegalError("the export limit works with phase current (0x365C = 2)")
        # rounded down: never more feed-in than asked for; the limit before the mode, so it applies right away
        await self._write(Reg(EXPORT_LIMIT_ADDRESS), watts * 1000 // rated)
        if mode not in EXPORT_POWER_MODES:
            await self._write(Reg(EXPORT_MODE_ADDRESS), EXPORT_POWER_MODES[0])


SLOT_BIT = 1 << (CHARGE_SLOT - 1)
APP_MODE_PASSIVE = 3
SLOT_MINUTES = 10  # length of OpenAmpere's charge slot: charging continues at most this long after OpenAmpere stops
EXTEND_BELOW_MIN = 5  # extended when fewer minutes are left, so a stored register is written about every 5 minutes


def charge_slot(now: datetime, percent: int) -> list[int]:
    """Start, end and days/power of a slot from now for SLOT_MINUTES, today only (bit 0 = Monday). Never past
    midnight: the next day gets a new slot."""
    start = now.hour * 60 + now.minute
    end = min(start + SLOT_MINUTES, 23 * 60 + 59)
    return [(start // 60) << 8 | start % 60, (end // 60) << 8 | end % 60, (1 << now.weekday()) << 8 | percent]


def minutes_left(now: datetime, slot: list[int]) -> int:
    """Minutes until the slot ends; 0 if it is not for today or over."""
    if not (slot[2] >> 8) & (1 << now.weekday()):
        return 0
    return max(0, (slot[1] >> 8) * 60 + (slot[1] & 0xFF) - (now.hour * 60 + now.minute))


def to_settings(raw: dict[str, float], settings: list[int] | None = None, backup: int | None = None) -> BatterySettings:
    """settings are the registers 0x3644–0x3647: on-grid and off-grid discharge depth, charge limit, work mode;
    backup is 0x3651, the reserve of the backup work mode (#380).
    Without them only the read-back registers are known (0x402A on grid, 0x4029 charge limit, 0x4022 work mode);
    the off-grid limit is not guessed (#334)."""
    modes = {value: WorkMode(name) for value, name in APP_MODES.items()}
    as_int = lambda key: int(raw[key]) if key in raw else None  # noqa: E731
    if settings:
        on_grid, off_grid, max_soc, app_mode = settings
    else:
        on_grid, off_grid, max_soc, app_mode = as_int("min_soc_on_grid"), None, as_int("max_soc"), as_int("app_mode")
    return BatterySettings(work_mode=modes.get(app_mode), min_soc=off_grid, max_soc=max_soc, min_soc_on_grid=on_grid,
                           backup_soc=backup)


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
