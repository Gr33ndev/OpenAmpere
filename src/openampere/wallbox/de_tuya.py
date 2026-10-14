# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""The portable 11 kW wallbox sold as "dé" (Tuya, protocol 3.5), read and controlled in the home network.

Data points as facts (nothing copied): the device definition of tuya-local (make-all/tuya-local, MIT) for this
product, the Home Assistant integration lachand/EV_charger (MIT) and a status read by an owner while idle and while
charging. See docs/devices/tuya/de-portable.md for what is confirmed and what is open.

- 102 (JSON text): "L1"…"L3" each [voltage 0.1 V, current 0.1 A, power 0.1 kW], "t" temperature 0.1 °C,
  "p" total power 0.1 kW, "d" duration of the session 0.1 s, "e" energy of the session 0.1 kWh.
- 106 (JSON text): "fv" firmware, "cp" control pilot voltage (about 12 V without a car, 9 V plugged in, 6 V
  charging), "e" energy of all sessions 0.1 kWh, "r" residual current protection.
- 107 (JSON text): the current steps the wallbox offers, e.g. [6, 8, 10, 13, 16].
- 109: state text (SLEEP, IDLE, IDLEINS, WORKING, WAIT, ERRORPAUSE, PAUSE, STOP).
- 140: start (true) or stop (false) charging; write only.
- 150: charging current in A; 152: maximum current in A (set by the owner in the app).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

STATES = {"SLEEP": "idle", "IDLE": "idle", "IDLEINS": "plugged_in", "WORKING": "charging", "WAIT": "waiting",
          "ERRORPAUSE": "fault", "PAUSE": "paused", "STOP": "finished"}
PLUGGED = frozenset({"plugged_in", "charging", "waiting", "fault", "paused", "finished"})
CP_PLUGGED_V = 10.5  # below: a car is connected (9 V), or it charges (6 V)
PHASE_ON_V = 100.0  # a phase with less is not connected
PHASE_ACTIVE_A = 1.0  # a phase with more current takes part in charging
CHARGING_W = 100.0  # WORKING can stay after a full car; power tells whether it really charges
DEFAULT_STEPS = (6, 8, 10, 13, 16)
MIN_CURRENT_A = 6  # the lowest current a car accepts (IEC 61851)


@dataclass
class Reading:
    """What OpenAmpere knows about a wallbox after one read."""
    state: str = "unknown"  # idle, plugged_in, charging, waiting, paused, finished, fault, unknown
    connected: bool = False
    charging: bool = False
    power_w: float = 0.0
    voltage_v: list[float | None] = field(default_factory=lambda: [None, None, None])
    current_a: list[float | None] = field(default_factory=lambda: [None, None, None])
    installed_phases: int = 3  # phases with voltage
    active_phases: int = 0  # phases that charge now
    session_kwh: float | None = None
    session_s: int | None = None
    temperature_c: float | None = None
    set_current_a: int | None = None
    max_current_a: int | None = None
    steps_a: list[int] = field(default_factory=lambda: list(DEFAULT_STEPS))
    firmware: str | None = None
    cp_v: float | None = None
    lifetime_kwh: float | None = None  # private: not in reports

    def phases(self) -> int:
        """Phases to calculate with: those that charge now, otherwise all that are connected."""
        return self.active_phases or self.installed_phases or 1


def _json(value) -> dict | list | None:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str):
        return None
    try:
        return json.loads(value)
    except ValueError:
        return None


def _num(value) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _tenth(value) -> float | None:
    number = _num(value)
    return None if number is None else round(number / 10, 2)


class DeTuyaCharger:
    key = "de_tuya"
    label = "dé Mobile Wallbox 11 kW (Tuya)"
    protocol = "tuya"  # the app shows the fields and the steps to the local key of a Tuya device
    START, CURRENT = "140", "150"

    @classmethod
    def interpret(cls, dps: dict[str, object]) -> Reading:
        r = Reading()
        metrics = _json(dps.get("102"))
        metrics = metrics if isinstance(metrics, dict) else {}
        info = _json(dps.get("106"))
        info = info if isinstance(info, dict) else {}
        steps = _json(dps.get("107"))
        r.state = STATES.get(str(dps.get("109") or "").upper(), "unknown")
        r.cp_v = _num(info.get("cp"))
        r.connected = r.state in PLUGGED or (r.cp_v is not None and 0 < r.cp_v < CP_PLUGGED_V)
        for i, name in enumerate(("L1", "L2", "L3")):
            phase = metrics.get(name)
            if isinstance(phase, list) and len(phase) >= 2:
                r.voltage_v[i], r.current_a[i] = _tenth(phase[0]), _tenth(phase[1])
        voltages = [v for v in r.voltage_v if v is not None]
        r.installed_phases = max(1, sum(v >= PHASE_ON_V for v in voltages)) if voltages else 3
        measured = sum((v or 0) * (a or 0) for v, a in zip(r.voltage_v, r.current_a, strict=True))
        total = _num(metrics.get("p"))
        power = measured if measured else (total * 100 if total else 0.0)
        # some firmware keeps the last values after charging: only WORKING with real power counts
        r.charging = r.state == "charging" and power >= CHARGING_W
        r.power_w = round(power) if r.charging else 0.0
        if r.charging:
            r.active_phases = sum((a or 0) >= PHASE_ACTIVE_A for a in r.current_a)
        else:
            r.current_a = [0.0 if a is not None else None for a in r.current_a]
        r.session_kwh = _tenth(metrics.get("e"))
        duration = _num(metrics.get("d"))
        r.session_s = None if duration is None else int(duration / 10)
        r.temperature_c = _tenth(metrics.get("t"))
        current, maximum = _num(dps.get(cls.CURRENT)), _num(dps.get("152"))
        r.set_current_a = None if current is None else int(current)
        r.max_current_a = None if maximum is None else int(maximum)
        if isinstance(steps, list):
            found = sorted({int(s) for s in steps if isinstance(s, (int, float)) and not isinstance(s, bool)
                            and MIN_CURRENT_A <= s <= 32})
            if found:
                r.steps_a = found
        r.firmware = str(info["fv"])[:20] if info.get("fv") else None
        r.lifetime_kwh = _tenth(info.get("e"))
        return r

    @classmethod
    def set_current(cls, amps: int) -> dict[str, object]:
        return {cls.CURRENT: int(amps)}

    @classmethod
    def start(cls, on: bool) -> dict[str, object]:
        return {cls.START: bool(on)}


MODELS = {m.key: m for m in (DeTuyaCharger,)}
