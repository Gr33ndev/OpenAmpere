# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""RCT Power Storage DC / Storage AC / Power Inverter over their local protocol (#646). Display only.

Not Modbus: the inverter answers RCT's own frames on TCP port 8899, the port the RCT app uses in the home network
(see protocol.py). Every value is an object with a 4-byte id; OpenAmpere sends one READ per object and collects the
answers. Object ids, types and units from RCT Power's "Serial Communication Protocol" (version 1.14), in own words;
the signs of the grid power as evcc's rct meter (MIT) uses them, see NOTICE and docs/devices/rct/.

- Not found by the scan for Modbus devices and not tried by "detect automatically": the owner chooses "RCT Power" in
  the setup. Port 502 (the setup's default) stands for the device's own port 8899.
- One TCP connection, kept open like the RCT app does. Objects the device does not know get no answer: they stay
  empty after a short wait, a device that answers nothing at all is no RCT.
- The protocol has no object that names the model; the serial number and the control software version are read.
- Only the device OpenAmpere is connected to (the master of a plant); further devices of a plant are not read.
- Not read yet: a further PV inverter on the S0 input (io_board.s0_external_power). RCT's household load is
  "load minus external power", how both add up needs a device with one.

VERIFY ON HARDWARE: everything; nothing is confirmed on a device yet. Especially the sign of g_sync.p_ac_grid_sum_lp
(+ = import, as evcc uses it) and of battery.current.
"""

from __future__ import annotations

import asyncio
import logging
import time

from ..base import BatterySettings, DeviceInfo, EnergyCounters, ExportLimit, PvInput, Snapshot, WorkMode
from ..modbus import DetectionFailed, DeviceRefused, DeviceUnreachable, ModbusTransientError, WrongDevice
from . import protocol as p

log = logging.getLogger(__name__)

DEFAULT_PORT = 8899
MODBUS_PORT = 502  # the setup's default: stands for the device's own port
ANSWER_WAIT_S = 2.0  # how long a read waits for the answers after its last request
DETECT_WAIT_S = 3.0  # the first answer on a new connection may take longer (the WiFi board)

# name: (object id, type), names as RCT's document calls the objects
OBJECTS: dict[str, tuple[int, str]] = {
    # identity
    "inverter_sn": (0x7924ABD9, p.STRING),  # serial number
    "svnversion": (0xDDD1C2D0, p.STRING),  # control software version
    # power, W
    "dc_a_power": (0xB5317B78, p.FLOAT),  # dc_conv.dc_conv_struct[0].p_dc, solar generator A
    "dc_b_power": (0xAA9AA253, p.FLOAT),  # dc_conv.dc_conv_struct[1].p_dc, solar generator B
    "dc_a_voltage": (0xB298395D, p.FLOAT),  # dc_conv.dc_conv_struct[0].u_sg_lp, V
    "dc_b_voltage": (0x5BB8075A, p.FLOAT),  # dc_conv.dc_conv_struct[1].u_sg_lp, V
    "house_power": (0x1AC87AA0, p.FLOAT),  # g_sync.p_ac_load_sum_lp, household load
    "grid_power": (0x91617C58, p.FLOAT),  # g_sync.p_ac_grid_sum_lp, total grid power (+ = import, as evcc)
    "battery_power": (0x400F015B, p.FLOAT),  # g_sync.p_acc_lp, + = discharging (RCT's document)
    "inverter_power": (0x4E49AEC5, p.FLOAT),  # g_sync.p_ac_sum, real power of the inverter
    # battery
    "battery_soc": (0x959930BF, p.FLOAT),  # battery.soc, 0..1
    "battery_soh": (0x381B8BF9, p.FLOAT),  # battery.soh, 0..1
    "battery_voltage": (0x65EED11B, p.FLOAT),  # battery.voltage, V
    "battery_current": (0x21961B58, p.FLOAT),  # battery.current, A
    "battery_temperature": (0x902AFAFB, p.FLOAT),  # battery.temperature, °C
    "heat_sink_temperature": (0xF79D41D9, p.FLOAT),  # db.temp1, °C
    "inverter_state": (0x5F33284E, p.UINT8),  # prim_sm.state
    # lifetime energy, Wh
    "pv_a_total": (0xFC724A9E, p.FLOAT),  # energy.e_dc_total[0]
    "pv_b_total": (0x68EEFD3D, p.FLOAT),  # energy.e_dc_total[1]
    "grid_import_total": (0x62FBE7DC, p.FLOAT),  # energy.e_grid_load_total
    "grid_export_total": (0x44D4C533, p.FLOAT),  # energy.e_grid_feed_total
    "load_total": (0xEFF4B537, p.FLOAT),  # energy.e_load_total, household
    "battery_charge_total": (0x5570401B, p.FLOAT),  # battery.stored_energy
    "battery_discharge_total": (0xA9033880, p.FLOAT),  # battery.used_energy
    # the owner's battery settings, 0..1 (read only)
    "soc_min": (0xCE266F0F, p.FLOAT),  # power_mng.soc_min
    "soc_max": (0x97997C93, p.FLOAT),  # power_mng.soc_max
}
IDENTITY = ("inverter_sn", "svnversion")
LIVE = tuple(name for name in OBJECTS if name not in IDENTITY + ("soc_min", "soc_max"))
NAMES = {oid: name for name, (oid, _) in OBJECTS.items()}


class RctDriver:
    KEY = "rct"
    PROTOCOL = "rct"
    DEFAULT_UNITS = (0,)  # no Modbus unit
    AUTO_DETECT = False  # only when chosen in the setup: it does not answer on the Modbus port

    def __init__(self, host: str, port: int = 0, unit: int = 0, *, timeout: float = 3.0, read_attempts: int = 3,
                 **_ignored) -> None:
        self._host = host
        self._port = DEFAULT_PORT if port in (0, MODBUS_PORT) else port
        self._timeout = max(timeout, 2.0)
        self._attempts = read_attempts  # set by the registry like for every driver; one request per object here
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._frames = p.FrameReader()
        self._lock = asyncio.Lock()
        self.info: DeviceInfo | None = None
        self.expected_serial: str | None = None  # the owner's inverter, set by the collector (#567)
        self.last_values: dict = {}  # the values of the last read, for the diagnostics (no extra request)
        self.last_values_at: float | None = None

    # ---- connection ----------------------------------------------------------

    async def _open(self) -> None:
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, self._port), self._timeout)
        except (OSError, asyncio.TimeoutError) as err:
            host, port = self._host, self._port
            raise RctUnreachable(f"Keine Verbindung zum RCT-Wechselrichter unter {host}, Port {port}. "
                                 "Stimmt die Adresse, und ist der Wechselrichter eingeschaltet und im Heimnetz?") from err
        self._frames = p.FrameReader()

    async def _query(self, names: tuple[str, ...], wait_s: float = ANSWER_WAIT_S) -> dict:
        """The values of the objects that answered within wait_s after the requests; missing ones are left out."""
        async with self._lock:
            if self._writer is None:
                await self._open()
            assert self._reader is not None and self._writer is not None
            wanted = {OBJECTS[name][0]: name for name in names}
            values: dict = {}
            try:
                self._writer.write(b"".join(p.read_request(oid) for oid in wanted))
                await self._writer.drain()
                deadline = time.monotonic() + wait_s
                while len(values) < len(wanted):
                    left = deadline - time.monotonic()
                    if left <= 0:
                        break
                    try:
                        chunk = await asyncio.wait_for(self._reader.read(4096), left)
                    except asyncio.TimeoutError:
                        break
                    if not chunk:
                        raise ConnectionResetError("the inverter closed the connection")
                    for command, oid, data in self._frames.feed(chunk):
                        name = wanted.get(oid)
                        if name is not None and command in (p.RESPONSE, p.LONG_RESPONSE):
                            values[name] = p.decode(OBJECTS[name][1], data)
            except (OSError, ConnectionError) as err:
                await self._drop()
                log.info("RCT at %s: %s", self._host, err)
                raise RctNotAnswering("Der RCT-Wechselrichter antwortet gerade nicht. OpenAmpere fragt ihn in Kürze "
                                      "wieder.") from err
            return values

    async def _drop(self) -> None:
        writer, self._reader, self._writer = self._writer, None, None
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except (OSError, ConnectionError):
                pass

    # ---- the driver interface ----------------------------------------------

    async def connect(self) -> DeviceInfo:
        """Opens the connection if needed and checks that the owner's inverter answers on it (#567)."""
        if self.info is not None and self._writer is not None:
            return self.info
        identity = await self._query(IDENTITY, DETECT_WAIT_S)
        serial = identity.get("inverter_sn")
        expected = self.expected_serial or (self.info.serial if self.info else None)
        if not serial and expected:  # the owner's inverter, silent for now (e.g. its network board restarts)
            await self._drop()
            raise RctNotAnswering("Der RCT-Wechselrichter antwortet gerade nicht. OpenAmpere fragt ihn in Kürze "
                                  "wieder.")
        if not serial:
            await self._drop()
            host, port = self._host, self._port
            raise NotRct(f"Unter {host}, Port {port} antwortet kein RCT-Wechselrichter. Stimmt die "
                         "Adresse?")
        if expected and serial != expected:
            await self._drop()
            self.info = None
            raise WrongDevice(expected, serial)
        self.info = DeviceInfo(manufacturer="RCT Power", model="Power Storage / Power Inverter", serial=serial,
                               firmware=identity.get("svnversion"), register_map="rct_serial", driver=self.KEY,
                               unit=0, supports_control=False)
        log.info("connected: %s", self.info)
        return self.info

    async def read(self) -> Snapshot:
        if self.info is None or self._writer is None:
            await self.connect()
        values = await self._query(LIVE)
        if not values:  # the connection is open, but the inverter answers nothing (e.g. while it restarts)
            await self._drop()
            raise RctNotAnswering("Der RCT-Wechselrichter antwortet gerade nicht. OpenAmpere fragt ihn in Kürze "
                                  "wieder.")
        self.last_values, self.last_values_at = values, time.time()
        return to_snapshot(values, self.last_values_at)

    async def disconnect(self) -> None:
        await self._drop()

    async def close(self) -> None:
        await self._drop()

    # ---- display only -------------------------------------------------------

    async def read_settings(self) -> BatterySettings:
        values = await self._query(("soc_min", "soc_max"))
        return BatterySettings(min_soc=whole_percent(values.get("soc_min")),
                               max_soc=whole_percent(values.get("soc_max")))

    async def read_export_limit(self) -> ExportLimit:
        return ExportLimit(supported=False)

    async def write_work_mode(self, mode: WorkMode) -> None:
        refuse()

    async def write_soc_limits(self, *, min_soc: int | None = None, max_soc: int | None = None,
                               min_soc_on_grid: int | None = None) -> None:
        refuse()

    async def set_remote_power(self, watts: int, timeout_s: int, restore: dict | None = None) -> None:
        refuse()

    async def release_remote_power(self, timeout_s: int | None = None, restore: dict | None = None) -> bool:
        return False  # OpenAmpere never started anything here

    async def remote_active(self) -> bool:
        return False

    async def write_export_limit(self, watts: int) -> None:
        refuse()


class RctUnreachable(DeviceUnreachable):
    """Nothing accepts a connection at the address; .message is a German text for the app (no Modbus to check)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class RctNotAnswering(ModbusTransientError):
    """A read without an answer; .message is a German text for the app (no Modbus proxy to blame)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotRct(DetectionFailed):
    """Something accepts the connection, but answers no RCT frame."""


class ReadOnly(DeviceRefused):
    """OpenAmpere only shows the values of this device."""


def refuse() -> None:
    raise ReadOnly("Beim RCT-Wechselrichter zeigt OpenAmpere nur die Werte an und ändert keine Einstellungen.")


def percent(fraction) -> float | None:
    """RCT gives shares from 0 to 1."""
    return None if fraction is None else round(fraction * 100, 1)


def whole_percent(fraction) -> int | None:
    return None if fraction is None else round(fraction * 100)


def to_snapshot(values: dict, timestamp: float) -> Snapshot:
    def value(name: str) -> float | None:
        found = values.get(name)
        return round(float(found), 1) if isinstance(found, (int, float)) else None

    def total(*names: str) -> float | None:
        found = [value(name) for name in names]
        return None if any(v is None for v in found) else sum(found)

    inputs = [PvInput(power=value(f"dc_{side}_power"), voltage=value(f"dc_{side}_voltage")) for side in ("a", "b")]
    inputs = [pv for pv in inputs if pv.power is not None]
    state = values.get("inverter_state")
    temperatures = {"inverter": t for t in [value("heat_sink_temperature")] if t is not None}
    return Snapshot(
        timestamp=timestamp,
        pv_power=total("dc_a_power", "dc_b_power"),
        house_power=value("house_power"),
        grid_power=value("grid_power"),
        battery_power=value("battery_power"),
        battery_soc=percent(values.get("battery_soc")),
        battery_soh=percent(values.get("battery_soh")),
        battery_voltage=value("battery_voltage"),
        battery_current=value("battery_current"),
        battery_temperature=value("battery_temperature"),
        temperatures=temperatures,
        pv_inputs=inputs,
        inverter_state=state if isinstance(state, int) else None,
        totals=EnergyCounters(
            pv=total("pv_a_total", "pv_b_total"),
            load=value("load_total"),
            grid_import=value("grid_import_total"),
            grid_export=value("grid_export_total"),
            battery_charge=value("battery_charge_total"),
            battery_discharge=value("battery_discharge_total"),
        ),
    )
