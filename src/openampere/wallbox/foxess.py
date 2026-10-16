# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""FoxESS AC EV Charger, read-only check (#325).

Register addresses as facts (nothing copied): the FoxESS EV Charger Modbus TCP protocol V1.6, which FoxESS support
gives to owners, and owner reports in public evcc discussions. NOT YET CONFIRMED on a device for OpenAmpere: the
check exists to confirm them (#318). In the FoxESS app's default mode Modbus TCP is said to be read-only; outside the
"Modbus TCP" mode some firmware refuses reads with exception 6 ("server device busy"), which the check reports.
"""

from __future__ import annotations

from .base import Wallbox, scaled, text, u32

STATUS = {0: "idle", 1: "connected", 2: "starting", 3: "charging", 4: "paused", 5: "finished", 6: "fault",
          8: "locked", 9: "switching_phases"}  # 9: seen on real devices, not in the protocol document
WORK_MODE = {0: "controlled", 1: "plug_and_charge", 2: "locked"}
LIFETIME_ENERGY = 0x1016  # u32, 0.1 kWh
RFID = 0x101C  # u32: the card that started the session
MODEL = 0x101E  # ASCII, 4 registers


class FoxEssCharger(Wallbox):
    key = "foxess_ac"
    label = "FoxESS AC EV Charger"
    sold_by = ("EKD",)  # with Ampere.IQ systems; announces itself as EVC-<serial> like any FoxESS charger (#412)
    ports = (502, 1502)  # the protocol says 502; at least one integration uses 1502
    unit = 1
    function = 3
    # identity, status, measurements, capabilities (0x1000–0x1021, without the serial number at 0x1022), the
    # configuration and, separately, the part single-phase models may not have
    ranges = ((0x1000, 0x22), (0x3000, 7), (0x3007, 5))
    private = frozenset({*range(LIFETIME_ENERGY, LIFETIME_ENERGY + 4), RFID, RFID + 1})
    public_text = frozenset(range(MODEL, MODEL + 4))

    @classmethod
    def interpret(cls, values: dict[int, int]) -> dict:
        firmware = values.get(0x1001)
        lifetime = u32(values, LIFETIME_ENERGY)
        rfid = u32(values, RFID)
        phase_switching = values.get(0x300A)
        return {
            "model": text(values, MODEL, 4),
            "firmware": f"{firmware >> 8}.{firmware & 0xFF}" if firmware is not None else None,
            "status": STATUS.get(values[0x1003], f"unknown ({values[0x1003]})") if 0x1003 in values else None,
            "plug_connected": bool(values[0x1005]) if 0x1005 in values else None,
            "voltage_v": [scaled(values, a, 0.1) for a in (0x1008, 0x1009, 0x100A)],
            "current_a": [scaled(values, a, 0.1) for a in (0x100B, 0x100C, 0x100D)],
            "power_kw": scaled(values, 0x100E, 0.1),
            "max_power_kw": scaled(values, 0x1011, 0.1),
            "min_power_kw": scaled(values, 0x1012, 0.1),
            "max_current_a": scaled(values, 0x1013, 0.1),
            "min_current_a": scaled(values, 0x1014, 0.1),
            "alarm_bits": values.get(0x1015),
            "fault_bits": u32(values, 0x101A),
            # only whether they make sense: exact counters and the card number identify the owner
            "energy_plausible": None if lifetime is None else lifetime < 10_000_000,
            "rfid_card": None if rfid is None else bool(rfid),
            "work_mode": WORK_MODE.get(values[0x3000], f"unknown ({values[0x3000]})") if 0x3000 in values else None,
            "validity_s": values.get(0x3005),
            "fallback_current_a": scaled(values, 0x3006, 0.1),
            "phase_switching": None if phase_switching is None else bool(phase_switching),
            "switch_interval_min": values.get(0x300B),
        }
