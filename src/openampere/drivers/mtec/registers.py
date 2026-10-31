# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""M-TEC Energy-Butler GEN3 (a Solinteg inverter), sold by EKD from late 2022 (#520).

Register facts from M-TEC's "Energy Butler RTU Protocol GEN2 + GEN3 V1.0" (2022-11-19), croedel/MTECmqtt up to commit
32cdf27 (MIT; later commits are LGPL-3.0) and evcc's template solinteg-gen3 (MIT), see NOTICE. Holding registers
(FC03), 32-bit values with the high word first. GEN2 uses other addresses and is not covered.

Through the Wi-Fi/LAN module: port 502 on newer module firmware, 5743 before; unit id 252, 255 or 247 and the framing
(plain Modbus TCP or RTU frames over TCP) differ between sources, so the driver tries them.

Read only.

VERIFY ON HARDWARE: unit id and framing on port 502; the signs of 11000 (grid, + = export per evcc and MTECmqtt) and
30258 (battery, + = discharging per evcc); that 11016 − 11000 is the house load.
"""

from __future__ import annotations

from ..regs import Kind, Reg

READ_FUNCTION = 3
DEFAULT_UNITS = (252, 255, 247)

# only the addresses the sources list: a block across an undocumented gap might be refused
BLOCKS = (
    (10000, 8),  # serial number (ASCII)
    (10008, 1),  # equipment info: model code
    (10011, 4),  # firmware (bytes)
    (10105, 1),  # running state
    (10112, 4),  # fault flags 1 and 2
    (10994, 8),  # grid meter: phase powers, total power
    (11016, 2),  # inverter AC power
    (11028, 2),  # PV total power
    (11038, 4),  # PV1, PV2 voltage and current
    (11062, 4),  # PV1, PV2 power
    (30230, 2),  # backup (EPS) output power
    (30254, 3),  # battery voltage, current, mode
    (30258, 2),  # battery power
    (31000, 7),  # today's energy
    (31102, 12),  # lifetime energy
    (33000, 2),  # state of charge, state of health
)

RUNNING_STATES = {0: "wait", 1: "check", 2: "on-grid", 3: "fault", 4: "flash", 5: "off-grid"}
FAULT_STATE, OFF_GRID_STATE = 3, 5

VALUES = {
    "model_code": Reg(10008),
    "running_state": Reg(10105),
    "fault1": Reg(10112, Kind.U32),
    "fault2": Reg(10114, Kind.U32),
    "grid_l1_power": Reg(10994, Kind.I32),
    "grid_l2_power": Reg(10996, Kind.I32),
    "grid_l3_power": Reg(10998, Kind.I32),
    "grid_power": Reg(11000, Kind.I32),  # + = export (M-TEC), the snapshot uses + = import
    "inverter_power": Reg(11016, Kind.I32),  # AC side of the inverter
    "pv_power": Reg(11028, Kind.U32),
    "pv1_voltage": Reg(11038, Kind.U16, 0.1),
    "pv1_current": Reg(11039, Kind.U16, 0.1),
    "pv2_voltage": Reg(11040, Kind.U16, 0.1),
    "pv2_current": Reg(11041, Kind.U16, 0.1),
    "pv1_power": Reg(11062, Kind.U32),
    "pv2_power": Reg(11064, Kind.U32),
    "backup_power": Reg(30230, Kind.I32),  # backup (EPS) output: whether the house load includes it is open
    "battery_voltage": Reg(30254, Kind.U16, 0.1),
    "battery_current": Reg(30255, Kind.I16, 0.1),
    "battery_power": Reg(30258, Kind.I32),  # + = discharging
    # energy in 0.1 kWh -> scale 100 gives Wh
    "grid_export_today": Reg(31000, Kind.U16, 100),
    "grid_import_today": Reg(31001, Kind.U16, 100),
    "battery_charge_today": Reg(31003, Kind.U16, 100),
    "battery_discharge_today": Reg(31004, Kind.U16, 100),
    "pv_today": Reg(31005, Kind.U16, 100),
    "grid_export_total": Reg(31102, Kind.U32, 100),
    "grid_import_total": Reg(31104, Kind.U32, 100),
    "battery_charge_total": Reg(31108, Kind.U32, 100),
    "battery_discharge_total": Reg(31110, Kind.U32, 100),
    "pv_total": Reg(31112, Kind.U32, 100),
    "battery_soc": Reg(33000, Kind.U16, 0.01),
    "battery_soh": Reg(33001, Kind.U16, 0.01),
}
