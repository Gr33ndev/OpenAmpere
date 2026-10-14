# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Everything that leaves OpenAmpere for a report goes through here (#149, #324, #325).

Owners share the diagnostics report, the device query and the wallbox check in public issues. The rules for what is
shortened, replaced or left out live in this one place, so every export follows the same rules and a new one cannot
forget one:

- serial numbers and other readable device text: the first four and the last two characters (`shorten`);
- addresses, host names, e-mail and MAC addresses, user names in file paths and the addresses of the configured
  services and devices: the same value always by the same pseudonym (`for_report`);
- times of the owner's actions: as a duration, never the clock time (`ago`);
- register values: values at private addresses (card numbers, exact counters) removed, readable text shortened
  unless the owner includes it (`masked_block`);
- the control log: names chosen by the owner, references and times replaced, values that tell the system size
  rounded (`control_log_for_report`);
- what a wallbox said over OCPP: serial numbers shortened, SIM numbers and secret settings left out, times and
  energy counters replaced (`ocpp_for_report`).
"""

from __future__ import annotations

import ipaddress
import re
import struct
import urllib.parse
from collections import Counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .runtime import Runtime

TEXT_MIN = 6  # readable text of at least this many characters is shortened (serial numbers, names)

_IPV4 = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
_IPV6 = re.compile(r"(?<![\w:])[0-9A-Fa-f]{0,4}(?::[0-9A-Fa-f]{0,4}){2,7}(?![\w:])")
_MAC = re.compile(r"(?<![\w:-])[0-9A-Fa-f]{2}(?:[:-][0-9A-Fa-f]{2}){5}(?![\w:-])")
# time stamps in the names of database copies (storage.py): when the owner restored or lost data
_STAMP = re.compile(r"(?<!\d)\d{8}-\d{6}(?!\d)")
_HOST = re.compile(r"(?<![\w.-])[\w-]+(?:\.[\w-]+)*\.(?:local|lan|home|internal|fritz\.box|home\.arpa|ts\.net)"
                   r"(?![\w-])", re.I)
_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_HOME = re.compile(r"(?<=/)(home|Users)/[^/\s'\"]+")  # user name in file paths of a traceback
_FRAMES = re.compile(r"(?:\n>>>>> [^\n]*)+")  # raw Modbus frames pymodbus appends to its errors


def shorten(text: str | None) -> str | None:
    """A serial number or other readable device text: the first four and the last two characters."""
    if not text:
        return text
    return text[:4] + "…" + text[-2:] if len(text) > 6 else "…"


class Pseudonyms:
    """Replaces addresses and local host names in report texts, the same value always by the same pseudonym (#149):
    the report stays readable ("the same address twice") without telling anyone the user's network."""

    def __init__(self, inverter_host: str | None, known: dict[str, str] | None = None) -> None:
        """`known`: further values with their replacement, e.g. the serial number or the ntfy address."""
        self.known = {k.strip().lower(): v for k, v in (known or {}).items() if k and k.strip()}
        if inverter_host:
            self.known[inverter_host.lower()] = "<Wechselrichter>"
        self.counts: Counter = Counter()

    def name(self, value: str, kind: str) -> str:
        key = value.lower()
        if key not in self.known:
            self.counts[kind] += 1
            self.known[key] = f"<{kind}-{self.counts[kind]}>"
        return self.known[key]

    def _ip(self, match: re.Match, kind: str = "IP") -> str:
        try:
            ipaddress.ip_address(match.group(0))
        except ValueError:  # e.g. a time like 12:34:56 or a version number
            return match.group(0)
        return self.name(match.group(0), kind)

    def text(self, text: str) -> str:
        for value, name in sorted(self.known.items(), key=lambda item: -len(item[0])):
            text = re.sub(rf"(?<![\w.-]){re.escape(value)}(?![\w-]|\.\w)", lambda _m, n=name: n, text, flags=re.I)
        text = _EMAIL.sub(lambda m: self.name(m.group(0), "E-Mail"), text)
        text = _HOME.sub(r"\1/<Benutzer>", text)
        text = _MAC.sub(lambda m: self.name(m.group(0), "MAC"), text)
        text = _IPV4.sub(self._ip, text)
        text = _IPV6.sub(self._ip, text)
        text = _STAMP.sub("<Zeitpunkt>", text)  # e.g. openampere.db.before-restore-20261010-153012 (#223)
        return _HOST.sub(lambda m: self.name(m.group(0), "Host"), text)

    def apply(self, value):
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self.apply(v) for v in value]
        if isinstance(value, dict):
            return {k: self.apply(v) for k, v in value.items()}
        return value


def log_for_report(lines: list[str] | None) -> list[str] | None:
    """pymodbus appends the last raw Modbus frames to its errors (">>>>> send: 0x0 …"). They hold register values such
    as the energy counters, which identify an installation (#338); one note replaces them, the error itself stays."""
    return [_FRAMES.sub("\n>>>>> <Modbus-Rohdaten entfernt>", line) for line in lines] if lines else lines


def for_report(runtime: Runtime, include_serial: bool = False) -> Pseudonyms:
    """Pseudonyms that also know the installation's own private values: the addresses of the inverter, ntfy, evcc, the
    surplus devices and the wallboxes (with their device IDs and local keys), the meter user name and numbers and, unless the owner includes it, the serial number."""
    config = runtime.config
    known = {config.notify.ntfy_url: "<ntfy-Adresse>", config.evcc.url: "<evcc-Adresse>",
             config.meter.username: "<Benutzername>"}
    # user name and password in the ntfy address: the app refuses them, but config.yaml may still have them
    userinfo = config.notify.ntfy_url.partition("://")[2].rpartition("@")[0]
    for value in userinfo.split(":", 1):
        known[value] = known[urllib.parse.unquote(value)] = "<Zugangsdaten>"
    for meter in config.meter.meter_ids or []:
        known[str(meter)] = "<Zählernummer>"
    for device in runtime.storage.get_meta("consumers") or []:  # hosts and switch addresses of devices
        if not isinstance(device, dict):
            continue
        if device.get("host"):
            known[str(device["host"]).strip()] = "<Geräteadresse>"
        for key in ("url_on", "url_off"):
            url = runtime.secrets.decrypt(f"consumer.{key}", device.get(key) or "")
            if url:
                known[url] = "<Schaltadresse>"
                host = urllib.parse.urlsplit(url).hostname
                if host:
                    known.setdefault(host, "<Geräteadresse>")
    for wallbox in runtime.storage.get_meta("wallboxes") or []:  # wallboxes OpenAmpere controls itself (Tuya)
        if not isinstance(wallbox, dict):
            continue
        if wallbox.get("host"):
            known[str(wallbox["host"]).strip()] = "<Wallbox-Adresse>"
        if wallbox.get("device_id"):
            known[str(wallbox["device_id"]).strip()] = "<Geräte-ID>"
        key = runtime.secrets.decrypt("wallbox.local_key", wallbox.get("local_key") or "")
        if key:
            known[key] = "<Local Key>"
    if not include_serial:
        device = runtime.collector.device
        stored = (runtime.storage.get_meta("firmware") or {}).get("serial")
        for serial in {device.serial if device else None, stored}:
            if serial:
                known[serial] = shorten(serial)
    return Pseudonyms(config.inverter.host, known)


# the control log (#329): what the owner named (a person, an app access, a device), when something happens in the
# owner's day, references of the grid operator and values that tell the size of the system (#149)
LOG_NAMES = {"source", "name", "consumer", "loadpoint", "vehicle"}
LOG_TIMES = {"outage", "time"}
LOG_LEFT_OUT = {"grid_operator_confirmation"}
LOG_ROUNDED = {"export_limit_w": -2, "installed_kwp": 0, "battery_kwh": 0}
HOME_ASSISTANT = " (Home Assistant)"


def control_log_for_report(runtime: Runtime, entries: list[dict], now: float) -> list[dict]:
    """Control log entries for a report: how long ago instead of the time, and the details masked."""
    pseudonyms = for_report(runtime)

    def clean(value, key: str | None = None):
        if isinstance(value, dict):
            return {k: clean(v, k) for k, v in value.items()}
        if isinstance(value, list):
            return [clean(v, key) for v in value]
        if value is None or value == "":
            return value
        if key in LOG_LEFT_OUT:
            return "<entfernt>"
        if key in LOG_TIMES:
            return "<Zeitpunkt>"
        if key in LOG_ROUNDED and isinstance(value, (int, float)) and not isinstance(value, bool):
            return round(value, LOG_ROUNDED[key])
        if key in LOG_NAMES and isinstance(value, str):
            suffix = HOME_ASSISTANT if value.endswith(HOME_ASSISTANT) else ""
            return pseudonyms.name(value.removesuffix(HOME_ASSISTANT), "Name") + suffix
        return pseudonyms.apply(value)

    return [{"ago_s": max(0, round(now - e["ts"])), "action": e["action"], "details": clean(e["details"]),
             "dry_run": e["dry_run"], "result": pseudonyms.text(e["result"]),
             **({"note": pseudonyms.text(e["note"])} if e.get("note") else {})} for e in entries]


# OCPP (#328): serial numbers of the wallbox and its meter, SIM card numbers, settings with secrets or addresses
OCPP_SERIALS = ("chargePointSerialNumber", "chargeBoxSerialNumber", "meterSerialNumber")
OCPP_LEFT_OUT = ("iccid", "imsi")
OCPP_TIMES = {"timestamp", "scheduleStart", "startSchedule", "validFrom", "validTo"}
OCPP_SECRET = re.compile(r"pass|key|token|secret|ssid|auth|url|host|address|server|ip$|domain|user|apn|id$", re.I)


def ocpp_for_report(record: dict | None, include_serial: bool = False) -> dict | None:
    """What a wallbox said during the OCPP listener, for the report; addresses in it are pseudonymised with the rest of
    the report (`for_report`)."""
    if not record:
        return None
    boot = dict(record.get("boot") or {})
    for key in OCPP_SERIALS:
        if boot.get(key) and not include_serial:
            boot[key] = shorten(str(boot[key]))
    for key in OCPP_LEFT_OUT:
        if boot.get(key):
            boot[key] = "<entfernt>"
    def setting(key: str, value):
        # yes/no and numbers say how the wallbox behaves; text in a setting about access or addresses may be a secret
        harmless = str(value).strip().lower() in ("true", "false", "") or str(value).replace(".", "", 1).isdigit()
        return value if harmless or not OCPP_SECRET.search(key) else "<entfernt>"

    configuration = [{"key": c.get("key"), "readonly": c.get("readonly"),
                      "value": setting(str(c.get("key") or ""), c.get("value"))}
                     for c in record.get("configuration") or [] if isinstance(c, dict)]

    def without_times(value):
        if isinstance(value, dict):
            return {k: "<Zeitpunkt>" if k in OCPP_TIMES else without_times(v) for k, v in value.items()}
        if isinstance(value, list):
            return [without_times(v) for v in value]
        return value

    def meter(values: dict) -> dict:  # energy counters only as "present": exact readings identify the owner
        sampled = [{**s, "value": "<Zählerstand>"} if str(s.get("measurand", "")).startswith("Energy") else s
                   for v in values.get("meterValue") or [] for s in v.get("sampledValue") or [] if isinstance(s, dict)]
        return {"connectorId": values.get("connectorId"), "sampledValue": without_times(sampled)}

    charge_point = record.get("charge_point")
    return {"charge_point": charge_point if include_serial else shorten(charge_point), "boot": without_times(boot),
            "configuration": configuration, "unknown_keys": record.get("unknown_keys"),
            "schedule": without_times(record.get("schedule")), "status": without_times(record.get("status") or []),
            "meter_values": [meter(v) for v in record.get("meter_values") or [] if isinstance(v, dict)],
            "refused": record.get("refused") or [], "errors": record.get("errors") or [],
            "messages": record.get("messages", 0)}


def duration(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 120:
        return f"{minutes} Minuten"
    hours = minutes // 60
    return f"{hours} Stunden" if hours < 48 else f"{hours // 24} Tagen"


def ago(seconds: float) -> str:
    """How long ago, instead of the time of the owner's action (#149)."""
    return "vor weniger als einer Minute" if seconds < 60 else f"vor {duration(seconds)}"


def text_runs(words: list[int]) -> list[tuple[int, int, str]]:
    """Readable ASCII in register values (two characters per register, high byte first): (first index, index after
    the last, text) of every run of at least TEXT_MIN characters, e.g. a model name or a serial number."""
    data = b"".join(struct.pack(">H", w & 0xFFFF) for w in words)
    runs = []
    for match in re.finditer(rb"[\x20-\x7e]{%d,}" % TEXT_MIN, data):
        text = match.group(0).decode("ascii").strip()
        if len(text) >= TEXT_MIN:
            runs.append((match.start() // 2, (match.end() + 1) // 2, text))
    return runs


def masked_block(block: dict, include_text: bool, private: frozenset[int] = frozenset(),
                 public_text: frozenset[int] = frozenset()) -> dict:
    """A block of register values ({"start", "words", …}) for the report or the owner's screen: values at `private`
    addresses (e.g. an RFID card number, exact energy counters) are always removed; readable text is shortened
    unless the owner includes it or it lies within `public_text` (addresses known to hold harmless text such as a
    model code)."""
    words = block.get("words")
    if not words:
        return block
    words = [None if block["start"] + i in private else w for i, w in enumerate(words)]
    texts = []
    for first, after, text in text_runs([w or 0 for w in words]):
        address = block["start"] + first
        keep = include_text or all(a in public_text for a in range(address, block["start"] + after))
        texts.append({"address": address, "text": text if keep else shorten(text)})
        if not keep:
            words[first:after] = [None] * (after - first)
    return {**block, "words": words, "text": texts}
