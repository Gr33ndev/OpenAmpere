# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Weidmüller AC SMART (ECO, VALUE, ADVANCED), sold by EKD as "AMPERE.Charge" (#523): the read-only check (#325) and
the live view, read only.

Register addresses as facts (nothing copied): Weidmüller's "AC SMART ECO / VALUE / ADVANCED Modbus-Register"
(2933050000 Rev. 02, 2023-06), public on Weidmüller's website. Holding registers, function code 3, unit id 255, port 502,
values of 4 bytes with the low word first (not stated in the document; evcc found it on real devices). Modbus TCP is on
at delivery and accepts only one client at a time: while evcc or the previous smartbox holds the connection,
OpenAmpere gets none (the messages in modbus.py say so).

OpenAmpere only shows this wallbox. Control (a satellite box with a load management current and a timeout) comes only
after reports from real devices: when the controller goes away, the wallbox charges on with its fallback current, like
the FoxESS charger (#465).
"""

from __future__ import annotations

from .base import Reading, Wallbox

# vehicle state (301, IEC 61851, ASCII in the low byte): A no car, B connected, C/D charging, E/F error
CAR_STATE = {"A": "idle", "B": "plugged_in", "C": "charging", "D": "charging", "E": "fault", "F": "fault"}
# state machine of the charge controller (302), the ones the live view needs
EVSE_PAUSED = 12
EVSE_FAULT = frozenset({14, 15})
EVSE_WAITING = frozenset({3})  # waiting for authorisation (RFID, app)
FIRMWARE = 846  # CHAR, 12 bytes
LIFETIME_ENERGY = 457  # 8 bytes, Wh
PHASE_ON_V = 100.0
PHASE_ACTIVE_A = 1.0
CHARGING_W = 100.0
# above these a value is a wrong word order or a broken read, not a measurement: not shown
MAX_PLAUSIBLE_V = 400.0
MAX_PLAUSIBLE_A = 80.0
MAX_PLAUSIBLE_W = 30_000.0  # the AC SMART charges with at most 22 kW
MAX_PLAUSIBLE_KWH = 200.0  # one charge
MAX_PLAUSIBLE_S = 7 * 86400  # one charge


def u32le(values: dict[int, int], address: int) -> int | None:
    """Two registers, the low word first (Weidmüller)."""
    if address in values and address + 1 in values:
        return values[address + 1] << 16 | values[address]
    return None


def u64le(values: dict[int, int], address: int) -> int | None:
    if all(a in values for a in range(address, address + 4)):
        return sum(values[address + i] << (16 * i) for i in range(4))
    return None


def scaled32(values: dict[int, int], address: int, factor: float) -> float | None:
    raw = u32le(values, address)
    return None if raw is None else round(raw * factor, 3)


def text(values: dict[int, int], start: int, count: int) -> str | None:
    """ASCII, two characters per register, the first in the high byte; stops at the first NUL."""
    if not all(a in values for a in range(start, start + count)):
        return None
    raw = b"".join(values[a].to_bytes(2, "big") for a in range(start, start + count))
    return raw.split(b"\0")[0].decode("ascii", "replace").strip() or None


def car_state(values: dict[int, int]) -> str | None:
    """The letter A–F of 301, None when it is not one."""
    if 301 not in values:
        return None
    letter = chr(values[301] & 0xFF)
    return letter if letter in CAR_STATE else None


def plausible(value: float | None, limit: float) -> float | None:
    return value if value is None or value <= limit else None


def voltages(values: dict[int, int]) -> list[float | None]:
    return [plausible(scaled32(values, a, 0.001), MAX_PLAUSIBLE_V) for a in (400, 402, 404)]  # mV


def currents(values: dict[int, int]) -> list[float | None]:
    return [plausible(scaled32(values, a, 0.001), MAX_PLAUSIBLE_A) for a in (406, 408, 410)]  # mA


def power_w(values: dict[int, int]) -> float | None:
    return plausible(scaled32(values, 418, 0.001), MAX_PLAUSIBLE_W)  # mW


class WeidmuellerAcSmart(Wallbox):
    key = "weidmueller_ac_smart"
    label = "Weidmüller AC SMART"
    sold_by = ("EKD",)  # as "AMPERE.Charge" (the VALUE, 11 kW)
    ports = (502,)
    unit = 255
    function = 3
    # charge controller, phases, measurements (with the energy of the last charge and the temperature), the total
    # energy, the limits, the firmware, the Modbus interface, and the load management (satellite or control box,
    # timeout, current, fallback current); only the addresses the document lists (312–316, 433 and 703 are not)
    ranges = ((300, 12), (317, 7), (400, 33), (LIFETIME_ENERGY, 4), (700, 3), (704, 4), (FIRMWARE, 6), (995, 1),
              (11000, 2), (11050, 6))
    private = frozenset(range(LIFETIME_ENERGY, LIFETIME_ENERGY + 4))
    public_text = frozenset(range(FIRMWARE, FIRMWARE + 6))

    @classmethod
    def interpret(cls, values: dict[int, int]) -> dict:
        lifetime = u64le(values, LIFETIME_ENERGY)
        power = power_w(values)
        return {
            "firmware": text(values, FIRMWARE, 6),
            "car_state": car_state(values),
            "evse_state": values.get(302),
            "offered_current_a": values.get(300),
            "voltage_v": voltages(values),
            "current_a": currents(values),
            "power_kw": None if power is None else round(power / 1000, 2),
            "session_kwh": plausible(scaled32(values, 430, 0.001), MAX_PLAUSIBLE_KWH),
            "phases_setting": {0: 1, 1: 3}.get(values.get(317)),
            "phases_load_management": {0: 1, 1: 3}.get(values.get(318)),
            "installed_phases": {0: 1, 1: 3}.get(values.get(319)),
            "limit_a": values.get(700),
            "installation_limit_a": values.get(701),
            "user_limit_a": values.get(702),
            "plug_and_charge": None if 704 not in values else bool(values[704]),
            "modbus_over": {0: "ethernet", 1: "wlan"}.get(values.get(995)),
            "satellite": None if 11000 not in values else bool(values[11000]),
            "control_box": None if 11001 not in values else bool(values[11001]),
            "timeout_ms": u32le(values, 11050),
            "load_management_current_a": values.get(11052),
            "fallback_current_a": values.get(11054),
            # only whether it makes sense: an exact counter identifies the owner (it stays 0 on ECO and VALUE, evcc)
            "energy_plausible": None if lifetime is None else lifetime < 10_000_000_000,
        }


class WeidmuellerAcSmartLive:
    """The wallbox as one OpenAmpere shows itself (native.py), read only: never written, only the mode "manual"."""
    key = WeidmuellerAcSmart.key  # the same key as the check: a readable check fills in the setup
    label = WeidmuellerAcSmart.label
    sold_by = WeidmuellerAcSmart.sold_by
    protocol = "modbus"
    read_only = True
    simulated = False
    modes = ("manual",)
    port, unit = 502, 255
    # charge controller (300–311), measurements (400–419), energy of the last charge (430–431), limits (700–702)
    ranges = ((300, 12), (400, 20), (430, 2), (700, 3))
    optional = ((FIRMWARE, 6),)

    @classmethod
    def interpret(cls, values: dict[int, int]) -> Reading:
        r = Reading()
        letter, evse = car_state(values), values.get(302)
        r.state = CAR_STATE.get(letter, "unknown") if letter else "unknown"
        if evse in EVSE_FAULT:
            r.state = "fault"
        elif evse == EVSE_PAUSED and r.state in ("plugged_in", "charging"):
            r.state = "paused"
        elif evse in EVSE_WAITING and r.state == "plugged_in":
            r.state = "waiting"
        r.connected = letter in ("B", "C", "D")
        r.voltage_v = voltages(values)
        r.current_a = currents(values)
        found = [v for v in r.voltage_v if v is not None]
        r.installed_phases = max(1, sum(v >= PHASE_ON_V for v in found)) if found else 3
        power = power_w(values)
        if power is None and 418 in values:  # implausible: nothing about this charge can be trusted
            r.state = "unknown"
        power = power or 0.0
        r.charging = r.state == "charging" and power >= CHARGING_W
        r.power_w = round(power) if r.charging else 0.0
        if r.charging:
            r.active_phases = sum((a or 0) >= PHASE_ACTIVE_A for a in r.current_a)
        else:
            r.current_a = [0.0 if a is not None else None for a in r.current_a]
        session = plausible(scaled32(values, 430, 0.001), MAX_PLAUSIBLE_KWH)  # Wh -> kWh
        duration = u32le(values, 305)  # s since the authorisation
        r.session_kwh = session if r.connected else None
        r.session_s = duration if r.connected and duration is not None and duration <= MAX_PLAUSIBLE_S else None
        r.set_current_a = values.get(300) or None  # what it offers the car now
        limits = [a for a in (values.get(700), values.get(701), values.get(702)) if a]
        if limits and 6 <= min(limits) <= 80:
            r.max_current_a = min(limits)
            r.steps_a = list(range(6, r.max_current_a + 1))
        r.firmware = text(values, FIRMWARE, 6)
        return r
