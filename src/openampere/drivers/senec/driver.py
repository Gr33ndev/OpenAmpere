# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Senec.Home V3 (hybrid, hybrid duo, also LFP) and V2.1 over their local web interface lala.cgi (#521). Display only.

Not Modbus: the device answers a POST of JSON {"SECTION": {"KEY": ""}} to /lala.cgi with the same JSON, every value a
typed hex string ("fl_" IEEE-754 float, "u…"/"i…" integers, "st_" text). Facts from marq24/ha-senec-v3 and
mchwalisz/pysenec (both Apache-2.0) and evcc's template senec-home (MIT), see NOTICE; nothing copied.

- HTTPS with a self-signed certificate since about firmware 825 (2023), plain HTTP before and on the V2.1: tried in
  this order. The certificate is not checked: OpenAmpere only reads and sends nothing secret, and the device has no
  certificate that could be checked.
- Since about NPU 2411 a first request for FACTORY gives the session cookie the device wants (as its own web page).
- Requests stay below 600 characters, the size the device's web page uses; polling more often than about every 30 s
  is said to restart the device or stop its cloud sync, so the collector's interval is raised to that.
- No energy counters: firmware 825 emptied STATISTIC, so there is no energy history for this device.
- Not found by the scan for Modbus devices and not tried by "detect automatically": the owner chooses "Senec" in the
  setup. Port 502 (the setup's default) stands for the device's own ports, 443 and then 80.

VERIFY ON HARDWARE: everything; nothing is confirmed on a device yet. The sign of GUI_BAT_DATA_POWER (+ = charging,
as evcc scales it by -1) and GUI_GRID_POW (+ = import).
"""

from __future__ import annotations

import asyncio
import http.client
import http.cookiejar
import json
import logging
import math
import ssl
import struct
import time
import urllib.error
import urllib.request

from ..base import BatterySettings, DeviceInfo, ExportLimit, Snapshot, WorkMode
from ..modbus import DetectionFailed, DeviceRefused, DeviceUnreachable, ModbusTransientError

log = logging.getLogger(__name__)

PATH = "/lala.cgi"
DEFAULT_PORTS = (443, 80)
MODBUS_PORT = 502  # the setup's default: stands for the device's own ports
MAX_REQUEST_CHARS = 600
MIN_POLL_S = 30.0
IDENTITY = {"FACTORY": {"SYS_TYPE": "", "COUNTRY": "", "DEVICE_ID": ""}}
LIVE = {"ENERGY": {"STAT_STATE": "", "GUI_INVERTER_POWER": "", "GUI_BAT_DATA_POWER": "", "GUI_BAT_DATA_FUEL_CHARGE": "",
                   "GUI_GRID_POW": "", "GUI_HOUSE_POW": "", "GUI_BAT_DATA_VOLTAGE": "", "GUI_BAT_DATA_CURRENT": ""}}
# FACTORY.SYS_TYPE, the systems with a local web interface (names as marq24/ha-senec-v3 lists them)
SYSTEM_TYPES = {15: "Home V2.1", 17: "Home V3 hybrid", 18: "Home V3 hybrid duo", 19: "Home V3 hybrid",
                20: "Home V3 hybrid LFP", 21: "Home V3 hybrid duo LFP"}


def parse_value(value):
    """A typed hex string as a number or text; None for what is no such value (e.g. "VARIABLE_NOT_FOUND")."""
    if isinstance(value, list):
        return [parse_value(v) for v in value]
    if not isinstance(value, str) or "_" not in value:
        return None
    kind, _, raw = value.partition("_")
    try:
        if kind == "fl":
            number = struct.unpack(">f", bytes.fromhex(raw))[0]
            return number if math.isfinite(number) else None  # NaN or infinity: no value (and no valid JSON)
        if kind.startswith("u"):
            return int(raw, 16)
        if kind.startswith("i"):
            number, bits = int(raw, 16), 4 * len(raw)
            return number - (1 << bits) if number >= 1 << (bits - 1) else number
        if kind == "st":
            return raw
    except (ValueError, struct.error):  # not hex, or a float of another length
        return None
    return None


def parse(answer: dict) -> dict:
    return {section: {key: parse_value(value) for key, value in (values or {}).items()}
            for section, values in answer.items() if isinstance(values, dict)}


class SenecDriver:
    KEY = "senec"
    PROTOCOL = "http"
    DEFAULT_UNITS = (0,)  # no Modbus unit
    AUTO_DETECT = False  # only when chosen in the setup: it does not answer on the Modbus port
    MIN_POLL_S = MIN_POLL_S

    def __init__(self, host: str, port: int = 0, unit: int = 0, *, timeout: float = 5.0, read_attempts: int = 3,
                 **_ignored) -> None:
        self._host = host
        self._ports = DEFAULT_PORTS if port in (0, MODBUS_PORT) else (port,)
        self._timeout = max(timeout, 5.0)
        self._attempts = read_attempts  # set by the registry like for every driver; one request per read here
        self._url: str | None = None
        self._lock = asyncio.Lock()
        self._cookies = http.cookiejar.CookieJar()
        tls = ssl.create_default_context()
        tls.check_hostname, tls.verify_mode = False, ssl.CERT_NONE  # self-signed, see the module docstring
        # never through a proxy from the environment: the device is in the home network
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                                   urllib.request.HTTPCookieProcessor(self._cookies),
                                                   urllib.request.HTTPSHandler(context=tls))
        self.info: DeviceInfo | None = None
        self.last_energy: dict = {}  # the ENERGY values of the last read, for the diagnostics (no extra request)
        self.last_energy_at: float | None = None  # when they were read

    # ---- HTTP --------------------------------------------------------------

    def _post_sync(self, url: str, form: dict) -> dict:
        body = json.dumps(form, separators=(",", ":")).encode()
        if len(body) >= MAX_REQUEST_CHARS:
            raise ValueError("request too long for the device")
        request = urllib.request.Request(url, data=body, method="POST", headers={
            "Content-Type": "application/json", "User-Agent": "OpenAmpere"})
        with self._opener.open(request, timeout=self._timeout) as response:
            answer = json.loads(response.read().decode("utf-8", "replace"))
        if not isinstance(answer, dict):
            raise ValueError("lala.cgi answered with something that is no JSON object")
        return answer

    async def _post(self, form: dict, url: str | None = None) -> dict:
        async with self._lock:
            return await asyncio.to_thread(self._post_sync, url or self._url, form)

    async def _request(self, form: dict) -> dict:
        """One answer of the known device, parsed. No retries here: the collector tries again after its interval,
        which is never shorter than MIN_POLL_S for this device."""
        try:
            return parse(await self._post(form))
        except (TimeoutError, urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as err:
            # a cut-off answer (IncompleteRead) while the device restarts is as transient as a timeout
            log.info("Senec at %s not answering: %s", self._host, err)
            raise SenecNotAnswering("Der Senec-Speicher antwortet gerade nicht. OpenAmpere fragt ihn in Kürze "
                                    "wieder.") from err

    # ---- the driver interface ----------------------------------------------

    async def connect(self) -> DeviceInfo:
        if self.info is not None:
            return self.info
        return await self._detect()

    async def _detect(self) -> DeviceInfo:
        """The first port and scheme that answers like lala.cgi. Raises DeviceUnreachable when nothing answers at all,
        ConnectionError when something answers that is no Senec."""
        answered = False
        for port in self._ports:
            for scheme in ("https", "http"):
                url = f"{scheme}://{self._host}:{port}{PATH}"
                try:
                    identity = parse(await self._post(IDENTITY, url))  # also the session cookie (NPU 2411 and later)
                    live = parse(await self._post(LIVE, url))
                except urllib.error.HTTPError:
                    answered = True  # a web server, but not this one
                    continue
                except http.client.HTTPException as err:  # e.g. plain HTTP at a TLS port: no status line
                    answered = True
                    log.debug("Senec %s: %s", url, err)
                    continue
                except (TimeoutError, urllib.error.URLError, OSError, ValueError) as err:
                    answered = answered or isinstance(getattr(err, "reason", None), ssl.SSLError)
                    log.debug("Senec %s: %s", url, err)
                    continue
                energy = live.get("ENERGY", {})
                if energy.get("GUI_BAT_DATA_FUEL_CHARGE") is None or energy.get("GUI_GRID_POW") is None:
                    answered = True
                    continue
                self._url = url
                return self._identify(identity.get("FACTORY", {}))
        host = self._host
        if answered:
            raise NotSenec(f"Unter {host} antwortet ein Gerät, aber nicht wie ein Senec-Speicher. Stimmt die Adresse?")
        raise SenecUnreachable(f"Keine Antwort von der Weboberfläche des Senec-Speichers unter {host}. Stimmt die "
                               "Adresse, und ist der Speicher eingeschaltet und im Heimnetz?")

    def _identify(self, factory: dict) -> DeviceInfo:
        system = factory.get("SYS_TYPE")
        model = SYSTEM_TYPES.get(system, f"Typ {system}" if system is not None else "Home")
        if system not in SYSTEM_TYPES:
            log.warning("unknown Senec system type %s", system)
        device = factory.get("DEVICE_ID")
        self.info = DeviceInfo(manufacturer="Senec", model=model, serial=str(device) if device else None,
                               register_map="senec_lala", driver=self.KEY, unit=0, supports_control=False)
        log.info("connected: %s", self.info)
        return self.info

    async def read(self) -> Snapshot:
        if self._url is None:
            await self.connect()
        self.last_energy = (await self._request(LIVE)).get("ENERGY", {})
        self.last_energy_at = time.time()
        return to_snapshot(self.last_energy, self.last_energy_at)

    async def disconnect(self) -> None:
        """Nothing to close: every request is its own connection."""

    async def close(self) -> None:
        """Forgets the session: the next connect() finds the device again and gets a new cookie (after a restart of the
        device the old one no longer counts, and a firmware update can move it from HTTP to HTTPS)."""
        self.info, self._url = None, None
        self._cookies.clear()

    # ---- display only -------------------------------------------------------

    async def read_settings(self) -> BatterySettings:
        return BatterySettings()

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


class SenecUnreachable(DeviceUnreachable):
    """Nothing answers like a web server at the address; .message is a German text for the app (no Modbus to check)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class SenecNotAnswering(ModbusTransientError):
    """A read without an answer; .message is a German text for the app (no Modbus proxy to blame)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotSenec(DetectionFailed):
    """Something answers, but not like lala.cgi."""


class ReadOnly(DeviceRefused):
    """OpenAmpere only shows the values of this device."""


def refuse() -> None:
    raise ReadOnly("Beim Senec-Speicher zeigt OpenAmpere nur die Werte an und ändert keine Einstellungen.")


def to_snapshot(energy: dict, timestamp: float) -> Snapshot:
    def value(key: str) -> float | None:
        found = energy.get(key)
        return round(float(found), 1) if isinstance(found, (int, float)) else None

    battery = value("GUI_BAT_DATA_POWER")
    state = energy.get("STAT_STATE")
    return Snapshot(
        timestamp=timestamp,
        pv_power=value("GUI_INVERTER_POWER"),
        house_power=value("GUI_HOUSE_POW"),
        grid_power=value("GUI_GRID_POW"),  # + = import, as the snapshot
        battery_power=None if battery is None else -battery,  # Senec: + = charging
        battery_soc=value("GUI_BAT_DATA_FUEL_CHARGE"),
        battery_voltage=value("GUI_BAT_DATA_VOLTAGE"),
        battery_current=value("GUI_BAT_DATA_CURRENT"),
        inverter_state=state if isinstance(state, int) else None,
    )
