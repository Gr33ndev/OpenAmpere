"""FoxESS H3 register maps.

Register facts are taken from the MIT-licensed foxess_modbus project
(https://github.com/nathanmarlor/foxess_modbus) and community documentation of FoxESS H3
based storage systems. See docs/registers.md.

32-bit values: high word at the lower address.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..base import WorkMode
from ..regs import Kind, Reg, decode, encode  # noqa: F401  (re-exported)


@dataclass(frozen=True)
class RegisterMap:
    name: str
    # Function code used for measurements: 4 = input registers, 3 = holding registers
    read_function: int
    # Contiguous address ranges (start, count) read every poll
    blocks: tuple[tuple[int, int], ...]
    # blocks the device may legitimately not have (second battery, MPPT details, ...): skipped if rejected
    optional_blocks: frozenset
    values: dict[str, Reg]
    # Writable settings (always holding registers)
    settings: dict[str, Reg]
    work_modes: dict[WorkMode, int]
    firmware: tuple[int, ...]


# Newer FoxESS map (H3 Smart / H3 Pro and devices with newer firmware).
# Energy counters: 0.01 kWh -> scale 10 gives Wh.
H3_NEW = RegisterMap(
    name="foxess_h3_new",
    read_function=4,
    blocks=(
        (37002, 1),
        (37609, 24),
        (38814, 8),
        (38309, 8),  # second battery module (BMS2) temperatures
        (39063, 15),
        (39118, 2),
        (39141, 1),
        (39219, 20),
        (39279, 8),
        (39327, 12),  # MPPT 1-3 (MPPT3 power is a 32-bit value at 39337-39338)
        (39601, 32),
    ),
    optional_blocks=frozenset({37002, 38309, 39063, 39118, 39141, 39279, 39327}),
    values={
        "bms1_connected": Reg(37002),
        "battery_voltage": Reg(37609, Kind.U16, 0.1),
        "battery_current": Reg(37610, Kind.I16, 0.1),
        "battery_temperature": Reg(37611, Kind.I16, 0.1),
        "battery_soc": Reg(37612),
        "battery_cell_temp_max": Reg(37617, Kind.I16, 0.1),
        "battery_cell_temp_min": Reg(37618, Kind.I16, 0.1),
        "battery_soh": Reg(37624),
        "battery2_temperature": Reg(38309, Kind.I16, 0.1),
        "battery2_cell_temp_max": Reg(38315, Kind.I16, 0.1),
        "battery2_cell_temp_min": Reg(38316, Kind.I16, 0.1),
        "inverter_temperature": Reg(39141, Kind.I16, 0.1),  # 0.1 °C, confirmed on an H3 (raw 452 = 45.2 °C)
        "grid_power": Reg(38814, Kind.I32, 0.1),  # + = export
        "inverter_state": Reg(39063),
        "off_grid_flags": Reg(39065, Kind.U32),
        "alarm1": Reg(39067),
        "alarm2": Reg(39068),
        "alarm3": Reg(39069),
        "pv_power": Reg(39118, Kind.I32),
        "house_power": Reg(39225, Kind.I32),
        "battery_power": Reg(39237, Kind.I32),  # + = discharge
        "pv1_voltage": Reg(39070, Kind.I16, 0.1),
        "pv1_current": Reg(39071, Kind.I16, 0.01),
        "pv2_voltage": Reg(39072, Kind.I16, 0.1),
        "pv2_current": Reg(39073, Kind.I16, 0.01),
        "pv3_voltage": Reg(39074, Kind.I16, 0.1),
        "pv3_current": Reg(39075, Kind.I16, 0.01),
        "pv4_voltage": Reg(39076, Kind.I16, 0.1),
        "pv4_current": Reg(39077, Kind.I16, 0.01),
        "pv1_power": Reg(39279, Kind.I32),
        "pv2_power": Reg(39281, Kind.I32),
        "pv3_power": Reg(39283, Kind.I32),
        "pv4_power": Reg(39285, Kind.I32),
        "mppt1_voltage": Reg(39327, Kind.I16, 0.1),
        "mppt1_current": Reg(39328, Kind.I16, 0.01),
        "mppt1_power": Reg(39329, Kind.I32),
        "mppt2_voltage": Reg(39331, Kind.I16, 0.1),
        "mppt2_current": Reg(39332, Kind.I16, 0.01),
        "mppt2_power": Reg(39333, Kind.I32),
        "mppt3_voltage": Reg(39335, Kind.I16, 0.1),
        "mppt3_current": Reg(39336, Kind.I16, 0.01),
        "mppt3_power": Reg(39337, Kind.I32),
        "pv_total": Reg(39601, Kind.U32, 10),
        "pv_today": Reg(39603, Kind.U32, 10),
        "battery_charge_total": Reg(39605, Kind.U32, 10),
        "battery_charge_today": Reg(39607, Kind.U32, 10),
        "battery_discharge_total": Reg(39609, Kind.U32, 10),
        "battery_discharge_today": Reg(39611, Kind.U32, 10),
        "grid_export_total": Reg(39613, Kind.U32, 10),
        "grid_export_today": Reg(39615, Kind.U32, 10),
        "grid_import_total": Reg(39617, Kind.U32, 10),
        "grid_import_today": Reg(39619, Kind.U32, 10),
        "load_total": Reg(39629, Kind.U32, 10),
        "load_today": Reg(39631, Kind.U32, 10),
    },
    settings={
        "remote_enable": Reg(46001),
        "remote_timeout": Reg(46002),
        "remote_power": Reg(46003, Kind.I32),
        "max_charge_current": Reg(46607, Kind.U16, 0.1),
        "max_discharge_current": Reg(46608, Kind.U16, 0.1),
        "min_soc": Reg(46609),
        "max_soc": Reg(46610),
        "min_soc_on_grid": Reg(46611),
        "work_mode": Reg(49203),
        # Export (feed-in) power limit in W. Known from foxess_modbus for H3 Smart; not yet verified on
        # every device. Not available on the legacy map.
        "export_limit": Reg(46616, Kind.I32),
    },
    work_modes={
        WorkMode.SELF_USE: 1,
        WorkMode.FEED_IN_FIRST: 2,
        WorkMode.BACKUP: 3,
        WorkMode.PEAK_SHAVING: 4,
    },
    firmware=(36001, 36002, 36003),
)

# Classic FoxESS H3 map. Energy counters: 0.1 kWh -> scale 100 gives Wh.
H3_LEGACY = RegisterMap(
    name="foxess_h3_legacy",
    read_function=3,
    blocks=(
        (31000, 52),
        (31090, 1),
        (32000, 24),
    ),
    optional_blocks=frozenset({31090}),
    values={
        "pv1_voltage": Reg(31000, Kind.I16, 0.1),
        "pv1_current": Reg(31001, Kind.I16, 0.1),
        "pv1_power": Reg(31002, Kind.I16),
        "pv2_voltage": Reg(31003, Kind.I16, 0.1),
        "pv2_current": Reg(31004, Kind.I16, 0.1),
        "pv2_power": Reg(31005, Kind.I16),
        "inverter_temperature": Reg(31032, Kind.I16, 0.1),
        "ambient_temperature": Reg(31033, Kind.I16, 0.1),
        "grid_ct_r": Reg(31026, Kind.I16),  # + = export
        "grid_ct_s": Reg(31027, Kind.I16),
        "grid_ct_t": Reg(31028, Kind.I16),
        "load_r": Reg(31029, Kind.I16),
        "load_s": Reg(31030, Kind.I16),
        "load_t": Reg(31031, Kind.I16),
        "battery_voltage": Reg(31034, Kind.I16, 0.1),
        "battery_current": Reg(31035, Kind.I16, 0.1),
        "battery_power": Reg(31036, Kind.I16),  # + = discharge
        "battery_temperature": Reg(31037, Kind.I16, 0.1),
        "battery_soc": Reg(31038),
        "inverter_state": Reg(31041),
        "alarm1": Reg(31044),
        "alarm2": Reg(31045),
        "alarm3": Reg(31047),
        "battery_soh": Reg(31090),
        "pv_total": Reg(32000, Kind.U32, 100),
        "pv_today": Reg(32002, Kind.U16, 100),
        "battery_charge_total": Reg(32003, Kind.U32, 100),
        "battery_charge_today": Reg(32005, Kind.U16, 100),
        "battery_discharge_total": Reg(32006, Kind.U32, 100),
        "battery_discharge_today": Reg(32008, Kind.U16, 100),
        "grid_export_total": Reg(32009, Kind.U32, 100),
        "grid_export_today": Reg(32011, Kind.U16, 100),
        "grid_import_total": Reg(32012, Kind.U32, 100),
        "grid_import_today": Reg(32014, Kind.U16, 100),
        "load_total": Reg(32021, Kind.U32, 100),
        "load_today": Reg(32023, Kind.U16, 100),
    },
    settings={
        "remote_enable": Reg(44000),
        "remote_timeout": Reg(44001),
        "remote_power": Reg(44002, Kind.I32),
        "work_mode": Reg(41000),
        "max_charge_current": Reg(41007, Kind.U16, 0.1),
        "max_discharge_current": Reg(41008, Kind.U16, 0.1),
        "min_soc": Reg(41009),
        "max_soc": Reg(41010),
        "min_soc_on_grid": Reg(41011),
    },
    work_modes={
        WorkMode.SELF_USE: 0,
        WorkMode.FEED_IN_FIRST: 1,
        WorkMode.BACKUP: 2,
        WorkMode.PEAK_SHAVING: 4,
    },
    firmware=(30016, 30017, 30018),
)

MAPS = {m.name: m for m in (H3_NEW, H3_LEGACY)}

MODEL_ADDRESS = 30000
MODEL_LENGTH = 16
SERIAL_ADDRESS = 30016  # new map only; on the legacy map these are firmware registers
