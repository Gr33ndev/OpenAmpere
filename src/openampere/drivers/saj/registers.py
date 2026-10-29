# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
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
    # not in SAJ's protocol: the model the manufacturer's app shows for this code, without its power class (#339)
    0x005E: "3P-X E2",
}
# power class in W per type code, from the names in SAJ's protocol; 0x005E has none (#492)
POWER_CLASSES = {0x0055: (3000, 6000), 0x0056: (5000, 10000), 0x005A: (3000, 6000), 0x005B: (5000, 10000),
                 0x005C: (3000, 6000), 0x005D: (5000, 10000)}
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
    "min_soc_on_grid": Reg(0x402A),  # "BatDisSocDowLimit", set SoC_L; SAJ's H1 protocol: grid-connected (#334)
    "reserve_soc": Reg(0x402C),  # "BatResSoc", reserved SoC: which setting it mirrors is unknown (#334)
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
    # "CT PV power": a second meter (Meter B) measuring an extra PV inverter, not part of 0x40A5 (#419)
    "meter_b_pv_power": Reg(0x40A3, Kind.I16),
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

# settings (community, not in a public SAJ protocol): on-grid discharge depth (lowest SoC in normal operation,
# i.e. the backup reserve, in % SoC, confirmed on devices), then the off-grid depth (lowest SoC during a power
# outage, name only). Read only for the battery settings, not with every poll.
DEPTH_ADDRESS = 0x3644
APP_MODE_ADDRESS = 0x3647  # work mode setting (0 self-use, 2 backup)
BACKUP_SOC_ADDRESS = 0x3651  # reserve kept in the backup work mode (protocol, evcc; read and written since #380)

# control (#377), from SAJ's "H2-3~6K-S2 single phase communication protocol" (2022-12-02), used the same way by
# stanus74/home-assistant-saj-h2-modbus and evcc (saj-h2.yaml). All written with FC06, one register at a time.
SOC_ADDRESSES = {"min_soc_on_grid": 0x3644, "min_soc": 0x3645, "max_soc": 0x3646}  # % SoC
APP_MODES = {0: "self_use", 2: "backup"}  # 0x3647; 1 = time-of-use, 3 = passive, 10 = peak shaving are not offered
APP_MODE_TIME = 1
TIME_ADDRESS = 0x4000  # inverter clock, read-only: year, month << 8 | day, hour << 8 | minute, second << 8
CHARGE_ENABLE_ADDRESS = 0x3604  # bit n: charge slot n + 1 is on
# grid charging uses the last of the seven charge slots, so the owner's own slots (usually the first) stay as they are
CHARGE_SLOT = 7
CHARGE_SLOT_ADDRESS = 0x3606 + 3 * (CHARGE_SLOT - 1)  # start hh << 8 | mm, end hh << 8 | mm, days << 8 | power %
TOU_OUTSIDE_ADDRESS = 0x365F  # time-of-use mode outside the slots: 0 standby, 1 self-use
EXPORT_LIMIT_ADDRESS = 0x365A  # per mille of rated power
EXPORT_MODE_ADDRESS = 0x365C  # 0 off, 1 total power, 2 phase current, 3 phase power
EXPORT_POWER_MODES = (1, 3)  # the modes in which 0x365A applies
# device types with control: H2 and HS2 (protocol, stanus74, evcc) and 0x005E, a three-phase type sold as
# "Ampere.StoragePro" whose settings registers matched the manufacturer's app (#334); AS2 and other codes stay read-only
CONTROL_TYPES = frozenset({0x005A, 0x005B, 0x005C, 0x005D, 0x005E})

RUNNING_MODES = {0: "Init", 1: "Wait", 2: "Run", 3: "Off-grid", 4: "On-grid storage", 5: "Fault",
                 6: "Update", 7: "Test", 8: "Self-check", 9: "Reset"}
OFF_GRID_MODE = 3
