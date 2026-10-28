# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""FoxESS H3 simulator (Modbus TCP).

Lets you develop and test without a real inverter:

    python -m openampere.simulator --port 5020 --speed 60

Implements a minimal Modbus TCP server (FC03, FC04, FC06, FC16) and a simple
energy model: PV follows a sun curve with clouds, the house draws a base load
with spikes, the battery follows the selected work mode or a remote-control
setpoint, and the grid balances the rest. Lifetime and daily counters integrate.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import logging
import math
import random
import struct
import time
from dataclasses import dataclass, field
from datetime import datetime

from .drivers.base import WorkMode
from .drivers.foxess.registers import H3_LEGACY, H3_NEW, MODEL_ADDRESS, SERIAL_ADDRESS, RegisterMap, encode
from . import tuya
from .drivers.saj import registers as saj

log = logging.getLogger("openampere.simulator")

ILLEGAL_FUNCTION = 1
ILLEGAL_ADDRESS = 2
GATEWAY_TARGET_FAILED = 11  # what a Modbus proxy/gateway answers when the inverter does not respond
SERVER_DEVICE_BUSY = 6


@dataclass
class EnergyModel:
    pv_peak_w: float = 9000
    # two module arrays: (share of peak power, hour of maximum sun)
    roof_sides: tuple = ((0.6, 13.0), (0.4, 15.5))
    battery_capacity_wh: float = 10000
    max_battery_w: float = 5000
    base_load_w: float = 350
    soc: float = 50.0
    work_mode: WorkMode = WorkMode.SELF_USE
    min_soc: int = 10
    max_soc: int = 100
    min_soc_on_grid: int = 10
    backup_soc: int | None = None  # SAJ: the reserve of the backup mode, above it the battery supplies the house (#380)
    remote_enabled: bool = False
    remote_power_w: float = 0
    remote_deadline: float = 0
    export_limit_w: float = 6000  # feed-in limit (60 % of 10 kW)
    cloud: float = 0.8
    # live values
    pv_inputs_w: list = field(default_factory=lambda: [0.0, 0.0])
    pv_w: float = 0
    house_w: float = 0
    battery_w: float = 0  # + discharge
    grid_w: float = 0  # + import
    totals: dict = field(default_factory=lambda: dict.fromkeys(
        ("pv", "load", "grid_import", "grid_export", "battery_charge", "battery_discharge"), 0.0))
    today: dict = field(default_factory=lambda: dict.fromkeys(
        ("pv", "load", "grid_import", "grid_export", "battery_charge", "battery_discharge"), 0.0))
    _day: int = -1

    def step(self, now: datetime, dt_s: float) -> None:
        if now.toordinal() != self._day:
            self._day = now.toordinal()
            self.today = dict.fromkeys(self.today, 0.0)

        hour = now.hour + now.minute / 60
        self.cloud = min(1.0, max(0.2, self.cloud + random.uniform(-0.03, 0.03)))
        self.pv_inputs_w = []
        for share, peak_hour in self.roof_sides:
            angle = math.pi * (hour - (peak_hour - 7)) / 14  # 14 h of daylight centred on the peak
            sun = max(0.0, math.sin(angle)) if 0 < angle < math.pi else 0.0
            self.pv_inputs_w.append(self.pv_peak_w * share * sun * self.cloud)
        self.pv_w = sum(self.pv_inputs_w)
        spike = random.choice([0, 0, 0, 0, 1500, 2500]) if random.random() < 0.05 else 0
        self.house_w = self.base_load_w + spike + (400 if 18 <= hour < 22 else 0)

        surplus = self.pv_w - self.house_w  # > 0: excess PV
        if self.remote_enabled and time.monotonic() > self.remote_deadline:
            self.remote_enabled = False  # watchdog expired -> back to work mode
        if self.remote_enabled:
            battery = self.remote_power_w
        elif self.work_mode is WorkMode.SELF_USE:
            battery = -surplus
        elif self.work_mode is WorkMode.FEED_IN_FIRST:
            battery = 0.0 if surplus >= 0 else -surplus
        elif self.work_mode is WorkMode.BACKUP and self.backup_soc is None:
            battery = min(0.0, -surplus)  # only charge from PV, never discharge
        elif self.work_mode is WorkMode.BACKUP:
            battery = -surplus  # down to its own reserve (below)
        else:  # peak shaving
            battery = max(0.0, -surplus - 3000)

        battery = max(-self.max_battery_w, min(self.max_battery_w, battery))
        floor = (self.backup_soc if self.work_mode is WorkMode.BACKUP and self.backup_soc is not None
                 else self.min_soc_on_grid)
        if battery > 0 and self.soc <= floor:
            battery = 0.0
        if battery < 0 and self.soc >= self.max_soc and not self.remote_enabled:
            battery = 0.0
        if battery < 0 and self.soc >= 100:
            battery = 0.0
        self.battery_w = battery
        self.grid_w = self.house_w - self.pv_w - battery
        if -self.grid_w > self.export_limit_w:  # curtail PV to respect the feed-in limit
            curtailed = self.pv_w - (-self.grid_w - self.export_limit_w)
            factor = curtailed / self.pv_w if self.pv_w else 0
            self.pv_inputs_w = [p * factor for p in self.pv_inputs_w]
            self.pv_w = curtailed
            self.grid_w = -self.export_limit_w

        hours = dt_s / 3600
        self.soc = min(100.0, max(0.0, self.soc - battery * hours / self.battery_capacity_wh * 100))
        flows = {
            "pv": self.pv_w * hours,
            "load": self.house_w * hours,
            "grid_import": max(0.0, self.grid_w) * hours,
            "grid_export": max(0.0, -self.grid_w) * hours,
            "battery_charge": max(0.0, -battery) * hours,
            "battery_discharge": max(0.0, battery) * hours,
        }
        for key, wh in flows.items():
            self.totals[key] += wh
            self.today[key] += wh


class SimulatedInverter:
    def __init__(self, register_map: RegisterMap, model: str, serial: str, *, strict_function: bool,
                 max_connections: int, fault_rate: float = 0.0, latency_s: float = 0.0,
                 read_only: bool = False, unit: int | None = 247) -> None:
        self.unit = unit  # only this Modbus unit id answers (None = any), like a real device behind a gateway
        self.fault_rate = fault_rate  # share of reads answered with "gateway target failed"
        self.latency_s = latency_s  # extra delay per answer (slow proxy / network)
        self.read_only = read_only  # behave like a read-only proxy (rejects writes)
        self.map = register_map
        self.model = model
        self.serial = serial
        self.strict_function = strict_function
        self.max_connections = max_connections
        self.connections = 0
        self.energy = EnergyModel()
        self.regs: dict[int, int] = {}
        for start, count in register_map.blocks:
            for address in range(start, start + count):
                self.regs[address] = 0
        self._write_string(MODEL_ADDRESS, model, 16)
        if register_map is H3_NEW:
            self._write_string(SERIAL_ADDRESS, serial, 16)
        for address, value in zip(register_map.firmware, (150, 120, 0x110)):
            self.regs[address] = value
        self._sync_settings_to_registers()
        self.update_registers()

    def _write_string(self, address: int, text: str, length: int) -> None:
        data = text.encode("ascii")[: length * 2].ljust(length * 2, b"\0")
        for i in range(length):
            self.regs[address + i] = (data[2 * i] << 8) | data[2 * i + 1]

    def _put(self, name: str, value: float, table: dict | None = None) -> None:
        reg = (table or self.map.values)[name]
        raw = int(round(value / reg.scale))
        for i, word in enumerate(encode(reg, raw)):
            self.regs[reg.address + i] = word

    def _get_setting(self, name: str) -> int:
        reg = self.map.settings[name]
        words = [self.regs.get(reg.address + i, 0) for i in range(reg.count)]
        value = words[0] if reg.count == 1 else (words[0] << 16) | words[1]
        if reg.count == 2 and value & 0x80000000:
            value -= 0x100000000
        return value

    def _sync_settings_to_registers(self) -> None:
        e = self.energy
        self._put("work_mode", self.map.work_modes[e.work_mode], self.map.settings)
        self._put("min_soc", e.min_soc, self.map.settings)
        self._put("max_soc", e.max_soc, self.map.settings)
        self._put("min_soc_on_grid", e.min_soc_on_grid, self.map.settings)
        self._put("remote_enable", 0, self.map.settings)
        self._put("remote_timeout", 0, self.map.settings)
        self._put("remote_power", 0, self.map.settings)
        if "export_limit" in self.map.settings:
            self._put("export_limit", e.export_limit_w, self.map.settings)

    def on_write(self, address: int) -> None:
        """Apply settings after a register write."""
        e, s = self.energy, self.map.settings
        modes = {v: k for k, v in self.map.work_modes.items()}
        if address == s["work_mode"].address:
            e.work_mode = modes.get(self._get_setting("work_mode"), e.work_mode)
        e.min_soc = self._get_setting("min_soc")
        e.max_soc = self._get_setting("max_soc")
        e.min_soc_on_grid = self._get_setting("min_soc_on_grid")
        e.remote_power_w = self._get_setting("remote_power")
        if "export_limit" in self.map.settings:
            e.export_limit_w = self._get_setting("export_limit")
        enabled = bool(self._get_setting("remote_enable"))
        if enabled:
            timeout = self._get_setting("remote_timeout") or 60
            e.remote_deadline = time.monotonic() + timeout
        e.remote_enabled = enabled

    def update_registers(self) -> None:
        e = self.energy
        m = self.map
        if not e.remote_enabled and self._get_setting("remote_enable"):
            self._put("remote_enable", 0, m.settings)  # watchdog expired
        temps = {
            "inverter_temperature": 28 + e.pv_w / 1000 * 2.5,
            "battery_temperature": 22 + abs(e.battery_w) / 1500,
        }
        temps["battery_cell_temp_max"] = temps["battery_temperature"] + 1.2
        temps["battery_cell_temp_min"] = temps["battery_temperature"] - 0.8

        def pv_input(index: int, prefix: str) -> dict:
            power = e.pv_inputs_w[index] if index < len(e.pv_inputs_w) else 0.0
            voltage = 360 + power / 60 if power > 5 else 0.0
            return {f"{prefix}_power": power, f"{prefix}_voltage": voltage,
                    f"{prefix}_current": power / voltage if voltage else 0.0}

        values = {
            "battery_soc": round(e.soc),
            "battery_power": e.battery_w,
            "battery_voltage": 400 + e.soc * 0.5,
            "battery_current": e.battery_w / 450,
            "battery_temperature": temps["battery_temperature"],
            "battery_soh": 98,
            "inverter_state": 3,
            "alarm1": 0, "alarm2": 0, "alarm3": 0,
        }
        if m is H3_NEW:
            values.update({
                **temps,
                **pv_input(0, "mppt1"), **pv_input(1, "mppt2"), **pv_input(2, "mppt3"),
                **pv_input(0, "pv1"), **pv_input(1, "pv2"), **pv_input(2, "pv3"), **pv_input(3, "pv4"),
                "bms1_connected": 1,
                "grid_power": -e.grid_w,
                "pv_power": e.pv_w,
                "house_power": e.house_w,
                "off_grid_flags": 0,
            })
        else:
            values.update({
                **pv_input(0, "pv1"), **pv_input(1, "pv2"),
                "inverter_temperature": temps["inverter_temperature"], "ambient_temperature": 21.0,
                **{f"grid_ct_{p}": -e.grid_w / 3 for p in "rst"},
                **{f"load_{p}": e.house_w / 3 for p in "rst"},
            })
        for key in e.totals:
            values[f"{key}_total"] = e.totals[key]
            values[f"{key}_today"] = e.today[key]
        for name, value in values.items():
            if name in m.values:
                self._put(name, value)

    # ---- Modbus TCP ------------------------------------------------------

    def readable(self, function: int, address: int, count: int) -> bool:
        if self.strict_function and function != self.map.read_function:
            # settings and identity registers are holding registers only
            if function == 4 and address >= 40000:
                return False
            if function == 3 and 31000 <= address < 40000 and self.map.read_function == 4:
                return False
        return all(a in self.regs for a in range(address, address + count))

    def handle(self, pdu: bytes) -> bytes:
        function = pdu[0]
        if function in (3, 4) and self.fault_rate and random.random() < self.fault_rate:
            return bytes([function | 0x80, GATEWAY_TARGET_FAILED])
        if function in (6, 16) and self.read_only:
            return bytes([function | 0x80, ILLEGAL_FUNCTION])
        if function in (3, 4):
            address, count = struct.unpack(">HH", pdu[1:5])
            if not 1 <= count <= 125 or not self.readable(function, address, count):
                return bytes([function | 0x80, ILLEGAL_ADDRESS])
            words = [self.regs[a] for a in range(address, address + count)]
            return struct.pack(">BB", function, count * 2) + struct.pack(f">{count}H", *words)
        if function == 6:
            address, value = struct.unpack(">HH", pdu[1:5])
            if address not in self.regs or address < 40000:
                return bytes([function | 0x80, ILLEGAL_ADDRESS])
            self.regs[address] = value
            self.on_write(address)
            return pdu[:5]
        if function == 16:
            address, count, _ = struct.unpack(">HHB", pdu[1:6])
            words = struct.unpack(f">{count}H", pdu[6:6 + count * 2])
            if any(a not in self.regs or a < 40000 for a in range(address, address + count)):
                return bytes([function | 0x80, ILLEGAL_ADDRESS])
            for i, word in enumerate(words):
                self.regs[address + i] = word
            self.on_write(address)
            return pdu[:5]
        return bytes([function | 0x80, ILLEGAL_FUNCTION])

    async def serve_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if self.connections >= self.max_connections:
            log.warning("connection refused: limit of %d reached", self.max_connections)
            writer.close()
            return
        self.connections += 1
        try:
            while True:
                header = await reader.readexactly(7)
                transaction, protocol, length, unit = struct.unpack(">HHHB", header)
                pdu = await reader.readexactly(length - 1)
                if self.unit is not None and unit != self.unit:
                    response = bytes([pdu[0] | 0x80, GATEWAY_TARGET_FAILED])  # no such device on the bus
                else:
                    response = self.handle(pdu)
                if self.latency_s:
                    await asyncio.sleep(self.latency_s)
                writer.write(struct.pack(">HHHB", transaction, protocol, len(response) + 1, unit) + response)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            self.connections -= 1
            writer.close()


class SajSimulatedInverter(SimulatedInverter):
    """SAJ H2 register layout on top of the same energy model. Accepts the writes of the control (#377): battery
    settings, work mode, the charge slots of the time-of-use mode (charging while a slot runs on the clock of the
    simulator) and the export limit."""

    WRITABLE = frozenset({saj.CHARGE_ENABLE_ADDRESS, *range(0x3606, 0x3606 + 3 * 7), *saj.SOC_ADDRESSES.values(),
                          saj.APP_MODE_ADDRESS, saj.EXPORT_LIMIT_ADDRESS, saj.EXPORT_MODE_ADDRESS,
                          saj.TOU_OUTSIDE_ADDRESS, saj.BACKUP_SOC_ADDRESS})

    def __init__(self, *, device_type: int = 0x005B, serial: str = "HST2103SIM00001", unit: int = 1,
                 max_connections: int = 2, fault_rate: float = 0.0, latency_s: float = 0.0,
                 depth_registers: bool = True, read_only: bool = False) -> None:
        self.map = None
        self.depth_registers = depth_registers
        self.model = f"SAJ {saj.DEVICE_TYPES.get(device_type, hex(device_type))}"
        self.serial = serial
        self.strict_function = False
        self.max_connections = max_connections
        self.connections = 0
        self.fault_rate, self.latency_s, self.read_only, self.unit = fault_rate, latency_s, read_only, unit
        self.rated_w = 10000
        self.meter_b_w = 0.0  # an extra PV inverter measured by Meter B (#419); 0 = no such meter
        self.clock = datetime.now  # the inverter's clock, which the charge slots follow
        self.energy = EnergyModel()
        self.regs = {}
        for start, count in saj.BLOCKS:
            for address in range(start, start + count):
                self.regs[address] = 0
        for address in (*self.WRITABLE, *range(saj.TIME_ADDRESS, saj.TIME_ADDRESS + 4), 0x365B):
            self.regs.setdefault(address, 0)
        self.regs[saj.TOU_OUTSIDE_ADDRESS] = 1
        self.regs[saj.EXPORT_LIMIT_ADDRESS] = 1000
        if depth_registers:
            for i, value in enumerate((20, 5, 100, 0)):  # on grid, off grid, charge limit, app mode
                self.regs[saj.DEPTH_ADDRESS + i] = value
            self.regs[saj.BACKUP_SOC_ADDRESS] = 10
        else:  # firmware without the settings registers rejects them
            for address in (*range(saj.DEPTH_ADDRESS, saj.DEPTH_ADDRESS + 4), saj.BACKUP_SOC_ADDRESS):
                self.regs.pop(address, None)
        info = [device_type, self.rated_w, 1234] + [0] * 26
        text = serial.encode("ascii")[:20].ljust(20, b"\0")
        info[3:13] = [(text[2 * i] << 8) | text[2 * i + 1] for i in range(10)]
        info[23:26] = [1050, 1020, 1010]
        for i, word in enumerate(info):
            self.regs[saj.INFO_ADDRESS + i] = word
        self.on_write(saj.APP_MODE_ADDRESS)
        self.update_registers()

    def handle(self, pdu: bytes) -> bytes:
        self._sync()
        function = pdu[0]
        if function in (6, 16) and not self.read_only:
            address, count = struct.unpack(">HH", pdu[1:5]) if function == 16 else (pdu[1] << 8 | pdu[2], 1)
            if any(a not in self.WRITABLE or a not in self.regs for a in range(address, address + count)):
                return bytes([function | 0x80, ILLEGAL_ADDRESS])
            if function == 6:
                self.regs[address] = struct.unpack(">H", pdu[3:5])[0]
            else:
                words = struct.unpack(f">{count}H", pdu[6:6 + count * 2])
                for i, word in enumerate(words):
                    self.regs[address + i] = word
            self.on_write(address)
            return pdu[:5]
        return super().handle(pdu)

    def on_write(self, address: int) -> None:
        e = self.energy
        if saj.DEPTH_ADDRESS in self.regs:
            e.min_soc_on_grid = self.regs[saj.SOC_ADDRESSES["min_soc_on_grid"]]
            e.min_soc = self.regs[saj.SOC_ADDRESSES["min_soc"]]
            e.max_soc = self.regs[saj.SOC_ADDRESSES["max_soc"]]
            e.work_mode = WorkMode.BACKUP if self.regs[saj.APP_MODE_ADDRESS] == 2 else WorkMode.SELF_USE
            e.backup_soc = self.regs[saj.BACKUP_SOC_ADDRESS]
        mode = self.regs[saj.EXPORT_MODE_ADDRESS]
        e.export_limit_w = (self.regs[saj.EXPORT_LIMIT_ADDRESS] * self.rated_w / 1000
                            if mode in saj.EXPORT_POWER_MODES else self.rated_w)
        self._sync()

    def _sync(self) -> None:
        """The clock registers, and charging while a charge slot of the time-of-use mode runs."""
        now = self.clock()
        for i, word in enumerate((now.year, now.month << 8 | now.day, now.hour << 8 | now.minute, now.second << 8)):
            self.regs[saj.TIME_ADDRESS + i] = word
        minute, day = now.hour * 60 + now.minute, 1 << now.weekday()
        power = 0
        if self.regs.get(saj.APP_MODE_ADDRESS) == saj.APP_MODE_TIME:
            for n in range(7):
                if not self.regs[saj.CHARGE_ENABLE_ADDRESS] & (1 << n):
                    continue
                start, end, days = (self.regs[0x3606 + 3 * n + i] for i in range(3))
                if days >> 8 & day and (start >> 8) * 60 + (start & 0xFF) <= minute < (end >> 8) * 60 + (end & 0xFF):
                    power = (days & 0xFF) * self.rated_w / 100
        self.energy.remote_enabled = power > 0
        self.energy.remote_power_w = -power
        self.energy.remote_deadline = time.monotonic() + 3600

    def update_registers(self) -> None:
        e = self.energy

        def put(name: str, value: float) -> None:
            reg = saj.VALUES[name]
            for i, word in enumerate(encode(reg, int(round(value / reg.scale)))):
                self.regs[reg.address + i] = word

        put("running_mode", 2)
        put("inverter_temperature", 30 + e.pv_w / 400)
        put("ambient_temperature", 21)
        # the read-back registers follow the settings
        put("app_mode", self.regs.get(saj.APP_MODE_ADDRESS, 0))
        put("max_soc", e.max_soc)
        put("min_soc_on_grid", e.min_soc_on_grid if self.depth_registers else 20)
        put("reserve_soc", 10)
        put("battery_voltage", 52)
        put("battery_current", -e.battery_w / 52)
        put("battery_temperature", 23)
        put("battery_soc", e.soc)
        for i in range(4):
            power = e.pv_inputs_w[i] if i < len(e.pv_inputs_w) else 0.0
            voltage = 300 + power / 50 if power > 5 else 0.0
            put(f"pv{i + 1}_voltage", voltage)
            put(f"pv{i + 1}_current", power / voltage if voltage else 0)
            put(f"pv{i + 1}_power", power)
        put("house_power", e.house_w)
        put("meter_b_pv_power", self.meter_b_w)
        put("pv_power", e.pv_w)
        put("battery_power", e.battery_w)
        put("grid_power", e.grid_w)
        for key, flow in (("pv", "pv"), ("load", "load"), ("battery_charge", "battery_charge"),
                          ("battery_discharge", "battery_discharge")):
            put(f"{key}_today", e.today[flow])
            put(f"{key}_total", e.totals[flow])
        for direction in ("import", "export"):
            put(f"grid_{direction}_sum_today", e.today[f"grid_{direction}"])
            put(f"grid_{direction}_sum_total", e.totals[f"grid_{direction}"])
            put(f"grid_{direction}_l1_today", e.today[f"grid_{direction}"] / 3)
            put(f"grid_{direction}_l1_total", e.totals[f"grid_{direction}"] / 3)
        put("battery_count", 1)
        put("battery_soh", 97)
        put("battery1_temperature", 23)


class SimulatedHeatingRod(SimulatedInverter):
    """my-PV AC ELWA-style heating rod: power setpoint at 1000, temperature 1001, target 1002, status 1003.
    The water warms up while power is applied; without a new setpoint within `timeout_s` it switches off."""

    def __init__(self, *, unit: int = 1, max_watts: int = 3000, timeout_s: float = 60.0) -> None:
        self.unit, self.max_watts, self.timeout_s = unit, max_watts, timeout_s
        self.max_connections, self.connections, self.latency_s = 3, 0, 0.0
        self.temperature, self.target, self.setpoint, self.last_write = 45.0, 60.0, 0, 0.0
        self.writes: list[int] = []

    def tick(self, seconds: float, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        if self.setpoint and now - self.last_write > self.timeout_s:
            self.setpoint = 0  # control timeout: the device stops by itself
        heat = self.power_w * seconds / 3600 / 1000 * 4.3  # roughly 200 l of water: 4.3 K per kWh
        self.temperature = max(20.0, self.temperature + heat - seconds / 3600 * 0.3)

    @property
    def status(self) -> int:
        if self.temperature >= self.target:
            return 5
        return 2 if self.setpoint else 3

    @property
    def power_w(self) -> int:
        return min(self.setpoint, self.max_watts) if self.status == 2 else 0

    def handle(self, pdu: bytes) -> bytes:
        function = pdu[0]
        if function == 3:
            address, count = struct.unpack(">HH", pdu[1:5])
            regs = {1000: self.setpoint, 1001: round(self.temperature * 10), 1002: round(self.target * 10),
                    1003: self.status}
            if any(a not in regs for a in range(address, address + count)):
                return bytes([function | 0x80, ILLEGAL_ADDRESS])
            return struct.pack(">BB", function, count * 2) + struct.pack(f">{count}H", *(regs[a] for a in range(address, address + count)))
        if function == 6:
            address, value = struct.unpack(">HH", pdu[1:5])
            if address != 1000:
                return bytes([function | 0x80, ILLEGAL_ADDRESS])
            self.setpoint, self.last_write = value, time.monotonic()
            self.writes.append(value)
            return pdu[:5]
        return bytes([function | 0x80, ILLEGAL_FUNCTION])


class SimulatedFoxCharger(SimulatedInverter):
    """FoxESS AC EV Charger for the read-only wallbox check (#325), with the register addresses OpenAmpere assumes
    (wallbox/foxess.py, not yet confirmed on a device). `busy`: like a charger outside the app's "Modbus TCP" mode
    that refuses reads; `single_phase`: without the phase-switching registers 0x3008–0x300B; `charging`: a car
    charges at 16 A on three phases (the live view, #439). Every function code is kept in `functions` (the check and
    the live view must only read); the read/write registers 0x3000–0x300B take function code 16, every accepted write
    is kept in `writes` as (address, value). Work mode 2 ("locked") sets the status to 8, like a real charger (#464).
    Session control 0x4001 (function code 6, #465): 1 starts a car that is plugged in at the current of 0x3001, 2
    pauses it; like a real charger, a current written after a stop starts charging again (`restarted_by_current`)."""

    def __init__(self, *, unit: int = 1, busy: bool = False, single_phase: bool = False,
                 model: str = "A022KP1", serial: str = "SIMULATEDCHARGER", charging: bool = False) -> None:
        self.unit, self.busy = unit, busy
        self.max_connections, self.connections, self.latency_s = 1, 0, 0.0
        self.map, self.model, self.serial = None, model, serial
        self.functions: list[int] = []  # every function code received: the check must only read
        self.writes: list[tuple[int, int]] = []
        self.stopped = False  # the last session command was a stop
        self.restarted_by_current = 0  # charges started by a current written after a stop (a controller's mistake)
        self.resets_current = False  # like a charger that resets 0x3001 at once outside a charge (open, #465)
        self.regs = {0x1000: unit, 0x1001: 0x0110, 0x1002: 0, 0x1003: 0, 0x1004: 1, 0x1005: 0, 0x1006: 700,
                     0x1007: 700, 0x1008: 2301, 0x1009: 2302, 0x100A: 2303, 0x100B: 0, 0x100C: 0, 0x100D: 0,
                     0x100E: 0, 0x100F: 0, 0x1010: 0, 0x1011: 220, 0x1012: 42, 0x1013: 320, 0x1014: 60, 0x1015: 0,
                     0x1016: 0, 0x1017: 12345, 0x1018: 0, 0x1019: 0, 0x101A: 0, 0x101B: 0, 0x101C: 0x1234,
                     0x101D: 0x5678, 0x3000: 1, 0x3001: 320, 0x3002: 220, 0x3003: 0xFFFF, 0x3004: 0xFFFF, 0x3005: 60,
                     0x3006: 60}
        if not single_phase:
            self.regs.update({0x3007: 0, 0x3008: 0, 0x3009: 0, 0x300A: 1, 0x300B: 12})
        if charging:
            self.regs.update({0x1003: 3, 0x1005: 1, 0x100B: 160, 0x100C: 160, 0x100D: 160, 0x100E: 110})
        self._write_string(0x101E, model, 4)
        self._write_string(0x1022, serial, 16)

    def handle(self, pdu: bytes) -> bytes:
        self.functions.append(pdu[0])
        if pdu[0] in (3, 4) and self.busy:
            return bytes([pdu[0] | 0x80, SERVER_DEVICE_BUSY])
        if pdu[0] == 3:
            address, count = struct.unpack(">HH", pdu[1:5])
            if not 1 <= count <= 125 or any(a not in self.regs for a in range(address, address + count)):
                return bytes([3 | 0x80, ILLEGAL_ADDRESS])
            return struct.pack(">BB", 3, count * 2) + struct.pack(f">{count}H", *(self.regs[a] for a in range(address, address + count)))
        if pdu[0] == 16:
            address, count = struct.unpack(">HH", pdu[1:5])
            values = struct.unpack(f">{count}H", pdu[6:6 + 2 * count]) if count else ()
            if not 1 <= count <= 123 or len(values) != count \
                    or any(not 0x3000 <= a <= 0x300B or a not in self.regs for a in range(address, address + count)):
                return bytes([16 | 0x80, ILLEGAL_ADDRESS])
            for offset, value in enumerate(values):
                self.regs[address + offset] = value
                self.writes.append((address + offset, value))
            if address <= 0x3001 < address + count and self.resets_current and self.regs[0x1003] != 3:
                self.regs[0x3001] = self.regs[0x1013]  # back to its highest current
            elif address <= 0x3001 < address + count:
                if self.regs[0x1003] == 4 and self.stopped:  # a setpoint after a stop starts it again
                    self.restarted_by_current += 1
                    self._session(True)
                elif self.regs[0x1003] == 3:
                    self._session(True)  # the new current
            if 0x3000 in range(address, address + count):
                locked = self.regs[0x3000] == 2
                self.regs[0x1003] = 8 if locked else (0 if self.regs[0x1003] == 8 else self.regs[0x1003])
            return pdu[:5]
        if pdu[0] == 6:
            address, value = struct.unpack(">HH", pdu[1:5])
            if address != 0x4001 or value not in (0, 1, 2):
                return bytes([6 | 0x80, ILLEGAL_ADDRESS])
            self.writes.append((address, value))
            if value == 1 and self.regs[0x3000] != 2 and self.regs[0x1003] in (1, 4, 5):
                self.stopped = False
                self._session(True)
            elif value == 2 and self.regs[0x1003] == 3:
                self.stopped = True
                self._session(False)
            return pdu[:5]
        return bytes([pdu[0] | 0x80, ILLEGAL_FUNCTION])

    def plug(self) -> None:
        """A car is plugged in and waits for the start command (work mode "controlled")."""
        self.regs.update({0x1003: 1, 0x1005: 1})

    def _session(self, charging: bool) -> None:
        amps = self.regs[0x3001] if charging else 0
        self.regs.update({0x1003: 3 if charging else 4, 0x100B: amps, 0x100C: amps, 0x100D: amps,
                          0x100E: round(amps * 3 * 230 / 1000) if charging else 0})


class SimulatedTuyaCharger:
    """The portable dé wallbox over Tuya LAN 3.5 (wallbox/de_tuya.py): data points like an owner's status reads,
    the session key exchange of tuya.py, and only one connection at a time like the real device. `plugged`: a car is
    connected; it charges after a start (140) with the current of 150 on all `phases`. `stopped_shows`: the state
    after a stop (140 false); not known from a device yet, the real one may show STOP as for a full car (#454)."""

    STATES = {"idle": "SLEEP", "plugged": "IDLEINS", "charging": "WORKING", "finished": "STOP"}

    def __init__(self, local_key: str = "0123456789abcdef", *, plugged: bool = False, phases: int = 3,
                 steps: tuple[int, ...] = (6, 8, 10, 13, 16), stopped_shows: str = "IDLEINS") -> None:
        self.local_key = local_key.encode()
        self.plugged, self.phases, self.steps = plugged, phases, steps
        self.charging, self.full = False, False
        self.stopped, self.stopped_shows = False, stopped_shows  # stopped by 140 false since the car last charged
        self.current_a, self.max_current_a = 16, 16
        self.session_wh, self.session_s, self.lifetime_wh = 0.0, 0.0, 350_000.0
        self.busy = False  # a connection is open: the next one is closed right away
        self.writes: list[dict] = []  # every data point written, for tests
        self.connections = 0

    def dps(self) -> dict[str, object]:
        charging = self.charging and self.plugged and not self.full
        self.stopped = self.stopped and self.plugged  # unplugged: the next car starts afresh
        amps = self.current_a if charging else 0
        phase = [[2300, amps * 10, round(amps * 230 / 100)] if i < self.phases else [0, 0, 0] for i in range(3)]
        state = ("charging" if charging else "finished" if self.full and self.plugged
                 else "plugged" if self.plugged else "idle")
        metrics = {"L1": phase[0], "L2": phase[1], "L3": phase[2], "t": 220,
                   "p": round(amps * 230 * self.phases / 100), "d": int(self.session_s * 10),
                   "e": int(self.session_wh / 100)}
        info = {"r": "Type B, AC 30mA + DC 6mA", "fv": "1.0.0", "cp": "6.6" if charging else "9.0" if self.plugged
                else "12.6", "t": "230", "e": str(int(self.lifetime_wh / 100))}
        return {"101": 300 if charging else 101, "102": json.dumps(metrics, separators=(",", ":")),
                "106": json.dumps(info, separators=(",", ":")), "107": json.dumps(list(self.steps)),
                "109": self.stopped_shows if state == "plugged" and self.stopped else self.STATES[state], "150": self.current_a, "152": self.max_current_a, "155": False,
                "156": False, "157": 1, "188": False, "189": 150}

    def apply(self, values: dict) -> dict:
        self.writes.append(dict(values))
        changed = {}
        if "150" in values and isinstance(values["150"], int) and 6 <= values["150"] <= self.max_current_a:
            self.current_a = changed["150"] = values["150"]
        if "140" in values:
            self.charging = bool(values["140"]) and self.plugged
            self.stopped = self.plugged and not values["140"]
            if self.charging:
                self.session_wh, self.session_s = 0.0, 0.0
        return changed

    def tick(self, seconds: float) -> None:
        if self.charging and self.plugged and not self.full:
            self.session_s += seconds
            watts = self.current_a * 230 * self.phases
            self.session_wh += watts * seconds / 3600
            self.lifetime_wh += watts * seconds / 3600

    async def serve_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if self.busy:
            writer.close()
            return
        self.busy, self.connections = True, self.connections + 1
        key, sequence, own, client = self.local_key, 0, b"", b""

        def send(command: int, payload: bytes) -> None:
            nonlocal sequence
            sequence += 1
            writer.write(tuya.pack(key, sequence, command, struct.pack(">I", 0) + payload))

        try:
            while True:
                _, command, payload = tuya.unpack(key, await tuya.read_frame(reader))
                if command == tuya.KEY_START:
                    own = random.randbytes(16)
                    send(tuya.KEY_ANSWER, own + hmac.new(self.local_key, payload, hashlib.sha256).digest())
                    client = payload
                elif command == tuya.KEY_FINISH:
                    if payload != hmac.new(self.local_key, own, hashlib.sha256).digest():
                        return
                    key = tuya.session_key(self.local_key, client, own)
                elif command == tuya.QUERY:
                    send(tuya.QUERY, json.dumps({"protocol": 4, "t": int(time.time()), "data": {"dps": self.dps()}}).encode())
                elif command == tuya.CONTROL:
                    body = json.loads(payload[len(tuya.VERSION_HEADER):])
                    changed = self.apply(body.get("data", {}).get("dps", {}))
                    send(tuya.CONTROL, b"")
                    if changed:
                        report = {"protocol": 4, "t": int(time.time()), "data": {"dps": changed}}
                        send(tuya.STATUS, tuya.VERSION_HEADER + json.dumps(report).encode())
                elif command == tuya.HEARTBEAT:
                    send(tuya.HEARTBEAT, b"")
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, tuya.TuyaError, ValueError):
            pass
        finally:
            self.busy = False
            writer.close()


async def run(host: str, port: int, speed: float, sim: SimulatedInverter, tick_s: float = 1.0) -> None:
    server = await asyncio.start_server(sim.serve_client, host, port)
    log.info("simulating %s (%s, unit %s) on %s:%d, speed x%g", sim.model, sim.map.name if sim.map else "saj_h2",
             sim.unit, host, port, speed)
    sim_time = time.time()
    async with server:
        while True:
            await asyncio.sleep(tick_s)
            sim_time += tick_s * speed
            sim.energy.step(datetime.fromtimestamp(sim_time), tick_s * speed)
            sim.update_registers()


def main() -> None:
    parser = argparse.ArgumentParser(description="FoxESS H3 Modbus TCP simulator")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5020)
    parser.add_argument("--vendor", choices=["foxess", "saj", "mypv", "foxess-charger", "de-tuya"], default="foxess",
                        help="device family to emulate (mypv = my-PV heating rod, foxess-charger = FoxESS wallbox, "
                             "de-tuya = portable dé wallbox over Tuya LAN 3.5, port 6668 like the device)")
    parser.add_argument("--local-key", default="0123456789abcdef", help="de-tuya: the 16-character local key")
    parser.add_argument("--plugged", action="store_true", help="de-tuya: a car is connected; foxess-charger: a car charges at 16 A")
    parser.add_argument("--map", choices=["new", "legacy"], default="new", help="FoxESS register map to emulate")
    parser.add_argument("--unit", type=int, default=None, help="Modbus unit id (default: FoxESS 247, SAJ 1)")
    parser.add_argument("--model", default=None, help="model string at register 30000")
    parser.add_argument("--serial", default="SIM0000000000001")
    parser.add_argument("--speed", type=float, default=1.0, help="simulated seconds per real second")
    parser.add_argument("--strict-function", action="store_true",
                        help="only answer measurements on the map's native function code")
    parser.add_argument("--max-connections", type=int, default=2)
    parser.add_argument("--fault-rate", type=float, default=0.0,
                        help="share of reads answered with 'gateway target failed' (simulates a flaky proxy)")
    parser.add_argument("--latency", type=float, default=0.0, help="extra seconds per answer")
    parser.add_argument("--read-only", action="store_true", help="reject writes like a read-only proxy")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    sim: SimulatedInverter | None = None
    if args.vendor in ("mypv", "foxess-charger", "de-tuya"):
        pass
    elif args.vendor == "saj":
        sim = SajSimulatedInverter(unit=args.unit or 1, max_connections=args.max_connections,
                                   fault_rate=args.fault_rate, latency_s=args.latency, read_only=args.read_only)
    else:
        register_map = H3_NEW if args.map == "new" else H3_LEGACY
        model = args.model or ("H3-10.0-Smart" if args.map == "new" else " H3-10.0-E")
        sim = SimulatedInverter(register_map, model, args.serial, strict_function=args.strict_function,
                                max_connections=args.max_connections, fault_rate=args.fault_rate,
                                latency_s=args.latency, read_only=args.read_only, unit=args.unit or 247)
    if args.vendor == "foxess-charger":
        async def run_charger() -> None:
            charger = SimulatedFoxCharger(unit=args.unit or 1, charging=args.plugged)
            server = await asyncio.start_server(charger.serve_client, args.host, args.port)
            log.info("simulating a FoxESS AC EV Charger (read-only) on %s:%d", args.host, args.port)
            async with server:
                await server.serve_forever()
        try:
            asyncio.run(run_charger())
        except KeyboardInterrupt:
            pass
        return
    if args.vendor == "de-tuya":
        async def run_tuya() -> None:
            charger = SimulatedTuyaCharger(args.local_key, plugged=args.plugged)
            server = await asyncio.start_server(charger.serve_client, args.host, args.port)
            log.info("simulating a portable dé wallbox (Tuya 3.5) on %s:%d", args.host, args.port)
            async with server:
                while True:
                    await asyncio.sleep(1)
                    charger.tick(args.speed)
        try:
            asyncio.run(run_tuya())
        except KeyboardInterrupt:
            pass
        return
    if args.vendor == "mypv":
        async def run_rod() -> None:
            rod = SimulatedHeatingRod(unit=args.unit or 1)
            server = await asyncio.start_server(rod.serve_client, args.host, args.port)
            log.info("simulating my-PV heating rod on %s:%d", args.host, args.port)
            async with server:
                while True:
                    await asyncio.sleep(1)
                    rod.tick(args.speed)
        try:
            asyncio.run(run_rod())
        except KeyboardInterrupt:
            pass
        return
    try:
        asyncio.run(run(args.host, args.port, args.speed, sim))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
