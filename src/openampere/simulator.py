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
import logging
import math
import random
import struct
import time
from dataclasses import dataclass, field
from datetime import datetime

from .drivers.base import WorkMode
from .drivers.foxess.registers import H3_LEGACY, H3_NEW, MODEL_ADDRESS, SERIAL_ADDRESS, RegisterMap, encode
from .drivers.saj import registers as saj

log = logging.getLogger("openampere.simulator")

ILLEGAL_FUNCTION = 1
ILLEGAL_ADDRESS = 2
GATEWAY_TARGET_FAILED = 11  # what a Modbus proxy/gateway answers when the inverter does not respond


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
        elif self.work_mode is WorkMode.BACKUP:
            battery = min(0.0, -surplus)  # only charge from PV, never discharge
        else:  # peak shaving
            battery = max(0.0, -surplus - 3000)

        battery = max(-self.max_battery_w, min(self.max_battery_w, battery))
        floor = self.min_soc_on_grid
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
    """SAJ H2 register layout on top of the same energy model. Read-only."""

    def __init__(self, *, device_type: int = 0x005B, serial: str = "HST2103SIM00001", unit: int = 1,
                 max_connections: int = 2, fault_rate: float = 0.0, latency_s: float = 0.0) -> None:
        self.map = None
        self.model = f"SAJ {saj.DEVICE_TYPES.get(device_type, hex(device_type))}"
        self.serial = serial
        self.strict_function = False
        self.max_connections = max_connections
        self.connections = 0
        self.fault_rate, self.latency_s, self.read_only, self.unit = fault_rate, latency_s, True, unit
        self.energy = EnergyModel()
        self.regs = {}
        for start, count in saj.BLOCKS:
            for address in range(start, start + count):
                self.regs[address] = 0
        info = [device_type, 10000, 1234] + [0] * 26
        text = serial.encode("ascii")[:20].ljust(20, b"\0")
        info[3:13] = [(text[2 * i] << 8) | text[2 * i + 1] for i in range(10)]
        info[23:26] = [1050, 1020, 1010]
        for i, word in enumerate(info):
            self.regs[saj.INFO_ADDRESS + i] = word
        self.update_registers()

    def on_write(self, address: int) -> None:  # pragma: no cover - writes are rejected (read_only)
        pass

    def update_registers(self) -> None:
        e = self.energy

        def put(name: str, value: float) -> None:
            reg = saj.VALUES[name]
            for i, word in enumerate(encode(reg, int(round(value / reg.scale)))):
                self.regs[reg.address + i] = word

        put("running_mode", 2)
        put("inverter_temperature", 30 + e.pv_w / 400)
        put("ambient_temperature", 21)
        put("app_mode", 0)
        put("max_soc", 100)
        put("min_soc", 10)
        put("reserve_soc", 20)
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
    parser.add_argument("--vendor", choices=["foxess", "saj", "mypv"], default="foxess",
                        help="device family to emulate (mypv = my-PV heating rod)")
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
    if args.vendor == "mypv":
        pass
    elif args.vendor == "saj":
        sim = SajSimulatedInverter(unit=args.unit or 1, max_connections=args.max_connections,
                                   fault_rate=args.fault_rate, latency_s=args.latency)
    else:
        register_map = H3_NEW if args.map == "new" else H3_LEGACY
        model = args.model or ("H3-10.0-Smart" if args.map == "new" else " H3-10.0-E")
        sim = SimulatedInverter(register_map, model, args.serial, strict_function=args.strict_function,
                                max_connections=args.max_connections, fault_rate=args.fault_rate,
                                latency_s=args.latency, read_only=args.read_only, unit=args.unit or 247)
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
