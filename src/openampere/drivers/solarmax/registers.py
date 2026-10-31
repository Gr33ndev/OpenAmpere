# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Solarmax MAX.STORAGE / MAX.STORAGE Ultimate hybrid inverters (sold by EKD as "AMPERE.Storage", #519).

Register facts from Solarmax's "MAX.STORAGE / MAX.STORAGE Ultimate ModbusTCP Datenpunktliste" (2024-09-11, firmware
3.4.4 and later), checked against evcc's template solarmax-maxstorage (MIT, see NOTICE). Input registers (FC04), unit
id 1, addresses from 0; 32-bit values with the low word first. 100–126 are readable on every device, 130–159 only
after Solarmax unlocked "Connectivity+" (the energy counters among them, in whole kWh).

Read only: OpenAmpere never switches on a storage function EKD switched off remotely (#515).

VERIFY ON HARDWARE: whether port 502 answers with factory settings; the signs of 114 (battery, charging positive) and
118 (grid, export positive); what 150–159 return without "Connectivity+" (exception or zeros); what the registers show
while EKD's switch-off is active.
"""

from __future__ import annotations

from ..regs import Kind, Reg

READ_FUNCTION = 4
DEFAULT_UNITS = (1,)

STANDARD_BLOCK = (100, 27)  # identification and live values, without "Connectivity+"
COUNTER_BLOCK = (150, 10)  # lifetime energy counters, only with "Connectivity+"

DEVICE_STATES = {0: "unknown", 1: "ok", 2: "warning", 3: "error"}
ERROR_STATE = 3


def u32(address: int, scale: float = 1.0) -> Reg:
    return Reg(address, Kind.U32, scale, low_word_first=True)


def i32(address: int, scale: float = 1.0) -> Reg:
    return Reg(address, Kind.I32, scale, low_word_first=True)


VALUES = {
    "serial": u32(100),
    "firmware": Reg(102),  # e.g. 344 = 3.4.4
    "device_state": Reg(103),
    "rated_power": Reg(105),  # nominal AC power, W
    "pv_power": i32(110),  # DC power of this inverter
    "pv_plant_power": i32(112),  # DC power of the whole plant, other connected inverters included
    "battery_power": i32(114),  # + = charging (Solarmax), the snapshot uses + = discharging
    "house_power": i32(116),  # "Eigenverbrauchsleistung"
    "grid_power": i32(118),  # + = export (Solarmax), the snapshot uses + = import
    "pv_ac_power": i32(120),  # AC power of the whole plant
    "battery_soc": Reg(122),  # %
    "battery_capacity": Reg(123),  # usable capacity, whole kWh
    "battery_energy": Reg(124),  # energy stored now, whole kWh
    # "Connectivity+": lifetime counters, whole kWh -> scale 1000 gives Wh
    "pv_total": u32(150, 1000),  # this inverter and the inverters connected to it
    "grid_export_total": u32(152, 1000),
    "grid_import_total": u32(154, 1000),
    "battery_charge_total": u32(156, 1000),
    "battery_discharge_total": u32(158, 1000),
}
