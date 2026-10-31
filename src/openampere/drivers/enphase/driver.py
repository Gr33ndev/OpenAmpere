# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Enphase IQ Gateway (Envoy) with IQ Batteries over its local HTTPS API (#522). Display only.

Not Modbus: JSON over HTTPS with a self-signed certificate. Since gateway software 7 every request needs a token (JWT)
that the owner gets once from Enphase's website with their Enphase login (valid one year for an owner account), sent
as "Authorization: Bearer". So this is the one driver that needs Enphase's cloud: once, and again when the token runs
out. Facts from Enphase's tech brief "Accessing IQ Gateway local APIs or local UI with token-based authentication"
(2023-01), pyenphase (MIT) and evcc's template enphase (MIT), see NOTICE; nothing copied.

- /production.json?details=1: PV ("production" with measurementType "production", or the microinverters), house
  ("total-consumption") and grid ("net-consumption", + = import) in W.
- /ivp/livedata/status: battery power of the IQ Batteries (meters.storage.agg_p_mw, mW, + = discharging).
- /ivp/ensemble/inventory: state of charge, the average percentFull of the batteries.
- /info: serial number and software, without a token.
- No energy counters for the battery, so no energy history.
- Not found by the scan for Modbus devices: the owner chooses "Enphase" in the setup and enters the token. Port 502 (the
  setup's default) stands for 443, then 80.
- The certificate cannot be checked against anything, so it is pinned on first use (#539): the setup test with a new
  token records its SHA-256 fingerprint, saved as inverter.cert_sha256, and the token goes only to that certificate.

VERIFY ON HARDWARE: everything; nothing is confirmed on a device yet. The signs of net-consumption (+ = import) and
storage.agg_p_mw (+ = discharging, as evcc takes it).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import http.client
import json
import logging
import math
import re
import ssl
import time
import urllib.parse

from ..base import BatterySettings, DeviceInfo, ExportLimit, Snapshot, WorkMode
from ..modbus import DetectionFailed, DeviceRefused, DeviceUnreachable, ModbusTransientError

log = logging.getLogger(__name__)

DEFAULT_PORTS = (443, 80)
MODBUS_PORT = 502  # the setup's default: stands for the gateway's own ports
MIN_POLL_S = 15.0  # the gateway is slow to answer; Home Assistant asks every 60 s
PRODUCTION, LIVEDATA, INVENTORY, INFO = "/production.json?details=1", "/ivp/livedata/status", "/ivp/ensemble/inventory", "/info"
WARN_DAYS = 30  # the token runs out within this many days: said in the diagnostics and the log


def token_expiry(token: str) -> float | None:
    """The expiry (exp, Unix time) of a JWT, None when it is no JWT or has none. Only read, never verified: the
    gateway checks the token."""
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        # a pasted text can be valid JSON that is no object, and json takes Infinity and NaN
        return finite(claims.get("exp")) if isinstance(claims, dict) else None
    except (IndexError, ValueError, TypeError):
        return None


def token_days_left(expires: float | None, now: float | None = None) -> int | None:
    """Whole days until the token runs out (negative: since it ran out), None when not known."""
    if expires is None:
        return None
    return int((expires - (time.time() if now is None else now)) // 86400)


def finite(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) and math.isfinite(value) else None


class TokenRejected(DeviceUnreachable):
    """The gateway refuses the token (401/403); .message is a German text for the app."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class EnphaseUnreachable(DeviceUnreachable):
    """Nothing answers like a web server; .message is a German text for the app (no Modbus to check)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotEnphase(DetectionFailed):
    """Something answers, but not like an IQ Gateway."""


class CertificateChanged(DeviceUnreachable):
    """The gateway shows another certificate than the pinned one (#539); nothing is sent. .message for the app."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class HttpStatus(Exception):
    """An answer other than 200."""

    def __init__(self, code: int) -> None:
        super().__init__(f"HTTP {code}")
        self.code = code


class PlainHttpOnly(DetectionFailed):
    """The gateway answers only over plain HTTP, where the token is never sent; not a refused token."""


class EnphaseNotAnswering(ModbusTransientError):
    """A read without an answer; .message is a German text for the app."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ReadOnly(DeviceRefused):
    """OpenAmpere only shows the values of this device."""


def refuse() -> None:
    raise ReadOnly("Beim Enphase-Speicher zeigt OpenAmpere nur die Werte an und ändert keine Einstellungen.")


class EnphaseDriver:
    KEY = "enphase"
    PROTOCOL = "http"
    DEFAULT_UNITS = (0,)  # no Modbus unit
    AUTO_DETECT = False  # only when chosen in the setup, with the token
    MIN_POLL_S = MIN_POLL_S

    def __init__(self, host: str, port: int = 0, unit: int = 0, *, timeout: float = 10.0, read_attempts: int = 3,
                 token: str = "", cert_sha256: str = "", **_ignored) -> None:
        self._host = host
        self._ports = DEFAULT_PORTS if port in (0, MODBUS_PORT) else (port,)
        self._timeout = max(timeout, 10.0)
        self._attempts = read_attempts  # set by the registry like for every driver; one request per read here
        self._token = token.strip()
        self._base: str | None = None
        self._lock = asyncio.Lock()
        self._tls = ssl.create_default_context()
        # self-signed, nothing to check it against: pinned instead (#539)
        self._tls.check_hostname, self._tls.verify_mode = False, ssl.CERT_NONE
        # the fingerprint the token may go to: the stored one, else the first one seen (the setup test with a new
        # token, or a token from config.yaml until the next start)
        self.cert_sha256: str | None = cert_sha256.strip().lower() or None
        self._candidate: str | None = None  # without a pin: the certificate of the detection under way
        self.info: DeviceInfo | None = None
        self.token_expires: float | None = token_expiry(self._token)
        self.has_battery = False  # the gateway listed IQ Batteries once: from then on 0 W is an idle battery

    # ---- HTTP --------------------------------------------------------------

    def _get_sync(self, url: str, auth: bool) -> str:
        """One GET; http.client (never a proxy from the environment, no redirects), so the certificate is checked after
        the handshake and before anything is sent."""
        parts = urllib.parse.urlsplit(url)
        https = parts.scheme == "https"
        connection = (http.client.HTTPSConnection(parts.hostname, parts.port, timeout=self._timeout, context=self._tls)
                      if https else http.client.HTTPConnection(parts.hostname, parts.port, timeout=self._timeout))
        try:
            connection.connect()
            headers = {"User-Agent": "OpenAmpere", "Accept": "application/json", "Connection": "close"}
            if https:
                self._check_certificate(connection.sock.getpeercert(binary_form=True))
                # the token only over HTTPS, never in plain text on the network (a gateway that needs one has HTTPS)
                if auth and self._token:
                    headers["Authorization"] = f"Bearer {self._token}"
            connection.request("GET", f"{parts.path}?{parts.query}" if parts.query else parts.path, headers=headers)
            response = connection.getresponse()
            body = response.read()
            if response.status != 200:
                raise HttpStatus(response.status)
            return body.decode("utf-8", "replace")
        finally:
            connection.close()

    def _check_certificate(self, der: bytes | None) -> None:
        seen = hashlib.sha256(der or b"").hexdigest()
        if self.cert_sha256 is None and self._candidate is None:
            self._candidate = seen  # trust on first use: pinned once the detection succeeded
        elif seen != (self.cert_sha256 or self._candidate):
            log.warning("the Enphase gateway at %s shows another certificate than the pinned one", self._host)
            raise CertificateChanged(
                "Das Enphase-Gateway meldet sich mit einem anderen Zertifikat als beim Einrichten. Deshalb schickt "
                "OpenAmpere ihm das Zugangs-Token nicht. Wurde das Gateway getauscht oder neu eingerichtet, trag das "
                "Token beim Einrichten noch einmal ein, prüf die Verbindung und speichere sie.")

    async def _get(self, path: str, base: str | None = None, auth: bool = True) -> str:
        async with self._lock:
            return await asyncio.to_thread(self._get_sync, (base or self._base) + path, auth)

    async def _json(self, path: str) -> dict | list:
        """One answer of the known gateway; no retries (the collector tries again after its interval)."""
        try:
            answer = json.loads(await self._get(path))
        except CertificateChanged:
            raise
        except HttpStatus as err:
            if err.code in (401, 403):
                self._reject()
            raise EnphaseNotAnswering("Das Enphase-Gateway antwortet gerade nicht. OpenAmpere fragt es in Kürze "
                                      "wieder.") from err
        except (TimeoutError, OSError, ValueError, http.client.HTTPException) as err:
            log.info("Enphase gateway at %s not answering: %s", self._host, err)
            raise EnphaseNotAnswering("Das Enphase-Gateway antwortet gerade nicht. OpenAmpere fragt es in Kürze "
                                      "wieder.") from err
        if not isinstance(answer, (dict, list)):
            raise EnphaseNotAnswering("Das Enphase-Gateway antwortet gerade nicht. OpenAmpere fragt es in Kürze "
                                      "wieder.")
        return answer

    def _reject(self) -> None:
        """The gateway refused the token (or there is none)."""
        if not self._token:
            raise TokenRejected("Das Enphase-Gateway braucht ein Zugangs-Token. Trag es beim Einrichten unter "
                                "„Erweitert“ ein; wie du es bekommst, steht in der Anleitung.")
        raise TokenRejected("Das Enphase-Gateway lehnt das Zugangs-Token ab. Vermutlich ist es abgelaufen: Hol bei "
                            "Enphase ein neues und trag es beim Einrichten unter „Erweitert“ ein.")

    # ---- the driver interface ----------------------------------------------

    async def connect(self) -> DeviceInfo:
        if self.info is not None:
            return self.info
        return await self._detect()

    async def _detect(self) -> DeviceInfo:
        """The first port and scheme where /info answers like an IQ Gateway; then the token is tried."""
        answered = False
        host = self._host
        for port in self._ports:
            for scheme in ("https", "http"):
                base = f"{scheme}://{host}:{port}"
                self._candidate = None  # another server than the gateway may have answered before
                try:
                    info = await self._get(INFO, base, auth=False)
                except CertificateChanged:
                    raise
                except (HttpStatus, http.client.HTTPException):
                    answered = True
                    continue
                except (TimeoutError, OSError, ValueError) as err:
                    answered = answered or isinstance(err, ssl.SSLError)
                    log.debug("Enphase %s: %s", base, err)
                    continue
                serial, software = tag(info, "sn"), tag(info, "software")
                if not serial or "envoy" not in info.lower():
                    answered = True
                    continue
                if scheme == "http" and self._token:  # the token never goes over plain HTTP
                    raise PlainHttpOnly("Das Enphase-Gateway antwortet nur unverschlüsselt; dorthin schickt OpenAmpere "
                                        "das Zugangs-Token nicht. Prüf die Adresse und versuch es noch einmal.")
                self._base = base
                try:
                    await self._json(PRODUCTION)  # the token
                except TokenRejected:
                    self._base = None
                    raise
                if scheme == "https" and self.cert_sha256 is None:
                    self.cert_sha256 = self._candidate
                self.info = DeviceInfo(manufacturer="Enphase", model="IQ Gateway", serial=serial, firmware=software,
                                       register_map="enphase_local", driver=self.KEY, unit=0, supports_control=False)
                self._note_expiry()
                log.info("connected: %s", self.info)
                return self.info
        if answered:
            raise NotEnphase(f"Unter {host} antwortet ein Gerät, aber nicht wie ein Enphase-Gateway. Stimmt die "
                             "Adresse?")
        raise EnphaseUnreachable(f"Keine Antwort vom Enphase-Gateway unter {host}. Stimmt die Adresse, und ist das "
                                 "Gateway im Heimnetz?")

    def _note_expiry(self) -> None:
        """Only the days left, never the date: it would tell the day the owner made the token (reports show times as
        durations)."""
        days = token_days_left(self.token_expires)
        if days is not None and days < WARN_DAYS:
            log.warning("the Enphase token runs out in %d days", days)

    async def read(self) -> Snapshot:
        if self._base is None:
            await self.connect()
        production = await self._json(PRODUCTION)
        # IQ Batteries: optional, each on its own. A gateway without batteries has no ensemble, and some software or
        # token types refuse these endpoints although /production.json took the token: then only the battery is missing
        battery = soc = None
        try:
            battery = storage_power(await self._json(LIVEDATA))
        except (EnphaseNotAnswering, TokenRejected) as err:
            log.debug("Enphase battery power not readable: %s", err)
        try:
            soc = state_of_charge(await self._json(INVENTORY))
        except (EnphaseNotAnswering, TokenRejected) as err:
            log.debug("Enphase state of charge not readable: %s", err)
        if soc is not None:
            self.has_battery = True
        if not self.has_battery and battery == 0:
            battery = None  # livedata answers 0 W without IQ Batteries too: no battery to show until one is listed
        return to_snapshot(production if isinstance(production, dict) else {}, battery, soc, time.time())

    async def disconnect(self) -> None:
        """Nothing to close: every request is its own connection."""

    async def close(self) -> None:
        """Forgets where the gateway answered: the next connect() finds it again (and tries the token again)."""
        self.info, self._base = None, None

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


def tag(xml: str, name: str) -> str | None:
    """The text of the first <name> element of /info (simple XML), None when there is none."""
    found = re.search(rf"<{name}>([^<]{{1,64}})</{name}>", xml)
    return found.group(1).strip() if found else None


def measurement(entries, kind: str) -> dict | None:
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, dict) and entry.get("measurementType") == kind:
            return entry
    return None


def pv_power(production: dict) -> float | None:
    """The production meter when it is active, else what the microinverters report (as evcc)."""
    meter = measurement(production.get("production"), "production")
    if meter and (meter.get("activeCount") or 0) >= 1:
        return finite(meter.get("wNow"))
    for entry in production.get("production") or []:
        if isinstance(entry, dict) and entry.get("type") == "inverters":
            return finite(entry.get("wNow"))
    return None


def storage_power(livedata) -> float | None:
    """Battery power in W from /ivp/livedata/status (mW), + = discharging."""
    meters = livedata.get("meters") if isinstance(livedata, dict) else None
    storage = meters.get("storage") if isinstance(meters, dict) else None
    value = finite(storage.get("agg_p_mw")) if isinstance(storage, dict) else None
    return None if value is None else round(value / 1000, 1)


def state_of_charge(inventory) -> float | None:
    """The average percentFull of the batteries in /ivp/ensemble/inventory (as evcc)."""
    levels = [finite(device.get("percentFull")) for group in inventory if isinstance(group, dict)
              for device in group.get("devices") or [] if isinstance(device, dict)] if isinstance(inventory, list) else []
    levels = [level for level in levels if level is not None]
    return round(sum(levels) / len(levels), 1) if levels else None


def to_snapshot(production: dict, battery: float | None, soc: float | None, timestamp: float) -> Snapshot:
    consumption = production.get("consumption")
    net, total = measurement(consumption, "net-consumption"), measurement(consumption, "total-consumption")
    return Snapshot(
        timestamp=timestamp,
        pv_power=pv_power(production),
        grid_power=finite(net.get("wNow")) if net and (net.get("activeCount") or 0) >= 1 else None,  # + = import
        house_power=finite(total.get("wNow")) if total and (total.get("activeCount") or 0) >= 1 else None,
        battery_power=battery,  # + = discharging
        battery_soc=soc,
    )
