# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""FoxESS AC EV Charger: the read-only check (#325) and the live view, read only (#439).

Register addresses as facts (nothing copied): the FoxESS EV Charger Modbus TCP protocol V1.6, which FoxESS support
gives to owners, and owner reports in public evcc discussions. The check exists to confirm them on real devices
(#318, #431). In the FoxESS app's default mode Modbus TCP is said to be read-only; outside the "Modbus TCP" mode some
firmware refuses reads with exception 6 ("server device busy"), which the check reports.

FoxEssChargerLive is the same charger as a wallbox OpenAmpere shows itself (native.py), read over Modbus TCP with
function code 3. Its work mode (WORK_MODE_REGISTER, function code 16) is a stored setting the owner changes in the app,
e.g. to unlock a charger that went "locked" (#464). Its charging is a simulation (#440) until the owner switches on
real control for this charger (`controllable`, #465): then OpenAmpere writes the current (0x3001, FC16) and start or
stop (0x4001, FC06), sets the command validity to 60 s and the fallback current to 6 A once (PREPARE), and writes the
current again at least every HEARTBEAT_S while the car charges. The charger's watchdog fails in the unsafe direction
(#318): if OpenAmpere stops writing, it charges on with the fallback current. A current written after a stop starts
charging again, so it is only refreshed while the car charges. It charges as it is set in its own app. Its modes other than "manual" are a simulation (#440):
native.py decides as for a wallbox it controls, but only writes to the control log what it would have set.
"""

from __future__ import annotations

from math import ceil, floor

from .base import Reading, Wallbox, scaled, text, u32

STATUS = {0: "idle", 1: "connected", 2: "starting", 3: "charging", 4: "paused", 5: "finished", 6: "fault",
          8: "locked", 9: "switching_phases"}  # 9: seen on real devices, not in the protocol document
WORK_MODE = {0: "controlled", 1: "plug_and_charge", 2: "locked"}
WORK_MODE_REGISTER = 0x3000  # read/write, function code 16 (FoxESS EV Charger Modbus TCP protocol V1.6, #318, #463)
MAX_CURRENT = 0x3001  # u16, 0.1 A: the current setpoint, read/write
MAX_POWER = 0x3002  # u16, 0.1 kW: the power setpoint; with automatic switching it also selects the phases (#478)
ONE_PHASE = 41  # 4.1 kW: below the 4.2 kW from which the charger charges on three phases
SESSION = 0x4001  # write only (function code 6): 1 start, 2 stop
PREPARE = {0x3005: 60, 0x3006: 60}  # command validity 60 s (its maximum), fallback current 6.0 A (#318)
HEARTBEAT_S = 25.0  # well within the validity of 60 s
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


# the charger's status as the state every wallbox is shown with (base.Reading); 8 "locked" stays unknown, the work
# mode says that it is locked
LIVE_STATE = {0: "idle", 1: "plugged_in", 2: "waiting", 3: "charging", 4: "paused", 5: "finished", 6: "fault",
              9: "charging"}
PLUGGED = frozenset({1, 2, 3, 4, 5, 9})  # the plug register 0x1005 is doubtful (read 1 without a car, #431)
PHASE_ON_V = 100.0  # a phase with less is not connected (single-phase models)
PHASE_ACTIVE_A = 1.0  # a phase with more current takes part in charging
CHARGING_W = 100.0


class FoxEssChargerLive:
    """The charger as a wallbox OpenAmpere reads itself, read only (#439)."""
    key = FoxEssCharger.key  # the same key as the check: a readable check fills in the setup
    label = FoxEssCharger.label
    sold_by = FoxEssCharger.sold_by
    protocol = "modbus"
    read_only = True  # its charging is not written, unless the owner switches on real control (controllable)
    work_modes = {mode: value for value, mode in WORK_MODE.items()}  # a setting the owner changes (#464)
    work_mode_register = WORK_MODE_REGISTER
    controllable = True  # real control once the owner agrees, per wallbox (#465)
    writable = frozenset({WORK_MODE_REGISTER, MAX_CURRENT, MAX_POWER, *PREPARE})  # function code 16, read back
    write_only = frozenset({SESSION})  # function code 6, written after the others
    prepare = PREPARE
    heartbeat_s = HEARTBEAT_S
    # its other modes only simulate the control (#440): what OpenAmpere would set goes into the control log
    simulated = True
    modes = ("manual", "off", "pv", "minpv", "now")  # native.MODES
    port, unit = 502, 1  # confirmed on a real device (#431)
    # status, measurements and limits (0x1000–0x1015, without the energy counters, the card number, the model code
    # and the serial number: the live view needs none of them) and the configuration; the part single-phase models
    # may not have is optional
    ranges = ((0x1000, 0x16), (0x3000, 7))
    optional = ((0x3007, 5),)

    @staticmethod
    def set_current(amps: int) -> dict[int, int]:
        return {MAX_CURRENT: amps * 10}

    @staticmethod
    def phase_power(phases: int, reading: Reading) -> dict[int, int]:
        """The power setpoint that makes a charger with automatic switching use these phases (#478): just below the
        three-phase threshold for one phase, its highest power for three. Empty when its highest power is not known."""
        if phases == 1:
            return {MAX_POWER: ONE_PHASE}
        return {MAX_POWER: round(reading.max_power_w / 100)} if reading.max_power_w else {}

    @staticmethod
    def start(on: bool) -> dict[int, int]:
        return {SESSION: 1 if on else 2}

    @classmethod
    def interpret(cls, values: dict[int, int]) -> Reading:
        r = Reading()
        status = values.get(0x1003)
        r.state = LIVE_STATE.get(status, "unknown") if status is not None else "unknown"
        r.connected = status in PLUGGED
        r.voltage_v = [scaled(values, a, 0.1) for a in (0x1008, 0x1009, 0x100A)]
        r.current_a = [scaled(values, a, 0.1) for a in (0x100B, 0x100C, 0x100D)]
        voltages = [v for v in r.voltage_v if v is not None]
        r.installed_phases = max(1, sum(v >= PHASE_ON_V for v in voltages)) if voltages else 3
        measured = sum((v or 0) * (a or 0) for v, a in zip(r.voltage_v, r.current_a, strict=True))
        power = measured or (scaled(values, 0x100E, 100.0) or 0.0)  # the register in 0.1 kW
        r.charging = r.state == "charging" and power >= CHARGING_W
        r.power_w = round(power) if r.charging else 0.0
        if r.charging:
            r.active_phases = sum((a or 0) >= PHASE_ACTIVE_A for a in r.current_a)
        else:
            r.current_a = [0.0 if a is not None else None for a in r.current_a]
        low, high = scaled(values, 0x1014, 0.1), scaled(values, 0x1013, 0.1)
        if high is not None and 6 <= high <= 80:
            r.max_current_a = floor(high)
            lowest = min(max(6, ceil(low)) if low is not None else 6, r.max_current_a)
            r.steps_a = list(range(lowest, r.max_current_a + 1))  # any whole ampere between its limits
        firmware = values.get(0x1001)
        r.firmware = f"{firmware >> 8}.{firmware & 0xFF}" if firmware is not None else None
        r.work_mode = WORK_MODE.get(values[0x3000]) if 0x3000 in values else None
        r.phase_switching = bool(values[0x300A]) if 0x300A in values else None
        r.phase_switch_min = values.get(0x300B)
        r.max_power_w = scaled(values, 0x1011, 100.0)  # 0.1 kW
        r.power_setpoint_w = scaled(values, MAX_POWER, 100.0)
        return r
