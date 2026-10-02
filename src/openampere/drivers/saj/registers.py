"""SAJ H2 / HS2 / AS2 hybrid inverters.

Register facts from community projects (sources and licenses: see NOTICE). All reads use FC03,
unit id 1 or 2.

VERIFY ON HARDWARE: sign of battery power (0x40A6, discharge positive per evcc) and grid power
(0x40AD, import positive per evcc); whether the PV energy counter covers all MPPTs.
"""

from __future__ import annotations

from ..regs import Kind, Reg

READ_FUNCTION = 3
DEFAULT_UNITS = (1, 2)

INFO_ADDRESS = 0x8F00  # device type, rated power, comm version, serial (10 regs), product code (10 regs), versions
INFO_LENGTH = 29

# Known device type codes (0x8F00)
DEVICE_TYPES = {
    0x0055: "AS2 3-6K", 0x0056: "AS2 5-10K",
    0x005A: "H2 3-6K S2", 0x005B: "H2 5-10K S3",
    0x005C: "HS2 3-6K S2", 0x005D: "HS2 5-10K S3",
}
DEVICE_TYPE_RANGE = range(0x0050, 0x0070)  # family range; unknown codes inside it are accepted with a warning

BLOCKS = (
    (0x4004, 14),  # running mode, faults, temperatures
    (0x4022, 11),  # app mode, SoC limits (read-back)
    (0x4069, 20),  # battery, PV1-4
    (0x40A0, 14),  # power totals
    (0x40BF, 64),  # energy counters (L1 based)
    (0x4167, 16),  # summed import/export counters (missing on some firmware -> ignored)
    (0xA000, 18),  # battery modules (BMS)
)

VALUES = {
    "running_mode": Reg(0x4004),
    "fault1": Reg(0x4005, Kind.U32),
    "fault2": Reg(0x4007, Kind.U32),
    "fault3": Reg(0x4009, Kind.U32),
    "inverter_temperature": Reg(0x4010, Kind.I16, 0.1),
    "ambient_temperature": Reg(0x4011, Kind.I16, 0.1),
    "app_mode": Reg(0x4022),
    "max_soc": Reg(0x4029),
    "min_soc": Reg(0x402A),
    "reserve_soc": Reg(0x402C),
    "battery_voltage": Reg(0x4069, Kind.U16, 0.1),
    "battery_current": Reg(0x406A, Kind.I16, 0.01),
    "battery_temperature": Reg(0x406E, Kind.I16, 0.1),
    "battery_soc": Reg(0x406F, Kind.U16, 0.01),
    "pv1_voltage": Reg(0x4071, Kind.U16, 0.1),
    "pv1_current": Reg(0x4072, Kind.U16, 0.01),
    "pv1_power": Reg(0x4073),
    "pv2_voltage": Reg(0x4074, Kind.U16, 0.1),
    "pv2_current": Reg(0x4075, Kind.U16, 0.01),
    "pv2_power": Reg(0x4076),
    "pv3_voltage": Reg(0x4077, Kind.U16, 0.1),
    "pv3_current": Reg(0x4078, Kind.U16, 0.01),
    "pv3_power": Reg(0x4079),
    "pv4_voltage": Reg(0x407A, Kind.U16, 0.1),
    "pv4_current": Reg(0x407B, Kind.U16, 0.01),
    "pv4_power": Reg(0x407C),
    "house_power": Reg(0x40A0, Kind.I16),
    "pv_power": Reg(0x40A5, Kind.I16),
    "battery_power": Reg(0x40A6, Kind.I16),  # + = discharge
    "grid_power": Reg(0x40AD, Kind.I16),  # + = import (system meter)
    # energy counters: U32, 0.01 kWh -> scale 10 gives Wh
    "pv_today": Reg(0x40BF, Kind.U32, 10),
    "pv_total": Reg(0x40C5, Kind.U32, 10),
    "battery_charge_today": Reg(0x40C7, Kind.U32, 10),
    "battery_charge_total": Reg(0x40CD, Kind.U32, 10),
    "battery_discharge_today": Reg(0x40CF, Kind.U32, 10),
    "battery_discharge_total": Reg(0x40D5, Kind.U32, 10),
    "load_today": Reg(0x40DF, Kind.U32, 10),
    "load_total": Reg(0x40E5, Kind.U32, 10),
    "grid_export_l1_today": Reg(0x40EF, Kind.U32, 10),  # SAJ calls this "sell"
    "grid_export_l1_total": Reg(0x40F5, Kind.U32, 10),
    "grid_import_l1_today": Reg(0x40F7, Kind.U32, 10),  # SAJ calls this "feed-in" (counter-intuitive)
    "grid_import_l1_total": Reg(0x40FD, Kind.U32, 10),
    "grid_import_sum_today": Reg(0x4167, Kind.U32, 10),
    "grid_import_sum_total": Reg(0x416D, Kind.U32, 10),
    "grid_export_sum_today": Reg(0x416F, Kind.U32, 10),
    "grid_export_sum_total": Reg(0x4175, Kind.U32, 10),
    "battery_count": Reg(0xA000),
    "battery_soh": Reg(0xA00D, Kind.U16, 0.01),
    "battery1_temperature": Reg(0xA010, Kind.I16, 0.1),
}

RUNNING_MODES = {0: "Init", 1: "Wait", 2: "Run", 3: "Off-grid", 4: "On-grid storage", 5: "Fault",
                 6: "Update", 7: "Test", 8: "Self-check", 9: "Reset"}
OFF_GRID_MODE = 3
