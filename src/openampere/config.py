# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Configuration.

Precedence (lowest to highest):
  defaults  <  config.yaml  <  settings saved in the web app (database)  <  OPENAMPERE_* environment variables

Values set through environment variables are reported as "locked" so the web app can show them read-only.
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from datetime import date
from pathlib import Path
from typing import Iterable
from urllib.parse import urlsplit

import yaml

from . import mail
from .secretbox import PREFIX as ENCRYPTED_PREFIX


@dataclass
class InverterConfig:
    driver: str = "auto"  # auto | foxess | saj | solarmax | mtec | senec | enphase
    host: str = ""
    port: int = 502
    unit: int = 0  # Modbus unit id; 0 = the device's default (FoxESS 247, SAJ 1 or 2)
    register_map: str = "auto"  # auto | foxess_h3_new | foxess_h3_legacy
    read_function: str = "auto"  # auto | input | holding
    poll_interval: float = 10.0
    timeout: float = 3.0  # seconds per request; raise for Modbus proxies or slow networks
    # "persistent": keep one connection open; "per_poll": connect for each reading and release the slot
    # again (for inverters with very few connection slots shared with another energy manager)
    connection_mode: str = "persistent"
    token: str = ""  # access token of a device that needs one (Enphase, from the owner's Enphase account)
    # SHA-256 of the certificate the token may go to (Enphase, #539): recorded by the setup test with a new token
    cert_sha256: str = ""
    name: str = ""  # the owner's name for it, shown once there is more than one inverter (#619)
    capacity_kwh: float = 0.0  # usable capacity of its battery, for the combined state of charge (#619); 0 = unknown


# drivers a further inverter can use: not one that needs an access token (Enphase), which only the first one keeps
EXTRA_DRIVERS = ("auto", "foxess", "saj", "solarmax", "mtec", "senec")
MAX_EXTRA_INVERTERS = 5


@dataclass
class ExtraInverter:
    """A further inverter of the same installation (#619): read like the first one, its PV and battery added to the
    combined values. Its grid power counts only with `own_grid` (it measures a grid connection of its own); otherwise
    all inverters measure the same house connection and the first one's reading is taken."""

    id: str = ""  # short, stable: the settings, the app and the control log refer to it
    name: str = ""
    driver: str = "auto"
    host: str = ""
    port: int = 502
    unit: int = 0
    register_map: str = "auto"
    read_function: str = "auto"
    timeout: float = 3.0
    connection_mode: str = "persistent"
    capacity_kwh: float = 0.0
    own_grid: bool = False
    # its values are already in the first inverter's (a parallel system): shown on its own, not added (#619)
    in_first: bool = False
    # the rated power from the type plate, confirmed for a device whose reported one does not fit its type (#492)
    rated_power_w: int = 0
    rated_power_reported_w: int = 0


@dataclass
class StorageConfig:
    path: str = "data/openampere.db"
    raw_retention_days: int = 30  # 0 = keep forever


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8080
    # extra host names the web app may be opened with (besides IPs, localhost and typical home-network
    # names such as *.local, *.fritz.box, *.ts.net); "*" disables the check (only behind a trusted proxy, and only in
    # config.yaml or the environment: the app refuses it, #437)
    allowed_hosts: list = field(default_factory=list)
    # HTTPS with an own certificate for other apps such as Home Assistant (#76); 0 = off
    tls_port: int = 8443


@dataclass
class ControlConfig:
    enabled: bool = False  # master switch for anything that writes to the inverter
    dry_run: bool = True  # log intended writes instead of executing them


@dataclass
class TariffConfig:
    electricity_price_ct: float = 35.0  # gross price per kWh drawn from the grid
    feed_in_ct: float = 8.0  # feed-in compensation per kWh
    feed_in_auto: bool = False  # compensation from the EEG rates instead of feed_in_ct (#71)
    feed_in_full: bool = False  # full feed-in (Volleinspeisung): higher EEG rates


@dataclass
class PvConfig:
    input_names: list = field(default_factory=list)  # e.g. ["Süddach", "Garage"]; empty = "Modulfeld 1", ...
    hidden_inputs: list = field(default_factory=list)  # input numbers ("3") not shown anywhere, e.g. an unused MPPT
    installed_kwp: float = 0.0  # installed module power (kWp, from the Marktstammdatenregister); 0 = unknown
    commissioning_date: str = ""  # YYYY-MM-DD the plant went into operation (Inbetriebnahme); "" = unknown
    # an extra PV inverter the inverter measures with a meter (SAJ: Meter B, #419): counted as solar power
    external: bool = False
    external_name: str = ""  # e.g. "Garage"; "" = "Zweiter Wechselrichter"


@dataclass
class BatteryConfig:
    capacity_kwh: float = 0.0  # usable capacity of the home battery; 0 = unknown
    # highest charging power the battery allows (datasheet, often lower for small batteries); 0 = unknown
    max_charge_kw: float = 0.0


@dataclass
class UpdatesConfig:
    check: bool = True  # look for new versions on GitHub every few hours
    auto: bool = False  # install them at night without asking (needs the updater container from install.sh)


@dataclass
class GridConfig:
    # Which feed-in limit applies to the system (Germany):
    #   unknown   – not declared; raising the limit needs the grid operator's written consent
    #   limit_60  – 60 % of the module power (§ 9 EEG, Solarspitzengesetz; until a smart meter with control unit)
    #   limit_70  – 70 % of the module power (former rule, still valid for some older systems)
    #   operator  – fixed value from the grid connection approval (e.g. zero export); changes need consent
    #   none      – declared: neither the law nor the grid connection approval limits the feed-in
    feed_in_rule: str = "unknown"
    # the rated power from the type plate, confirmed by the owner for a device whose reported one does not fit its
    # type (#492), and the reported value it was confirmed for; 0 = not confirmed
    rated_power_w: int = 0
    rated_power_reported_w: int = 0


@dataclass
class CloudConfig:
    api_key: str = ""  # personal key from the former vendor app (Mehr -> Konfiguration API-Zugang)
    base_url: str = "https://product.ekd-iot.de"  # customer API of the former vendor cloud


@dataclass
class NotifyConfig:
    ntfy_url: str = ""  # e.g. https://ntfy.sh/<own secret topic> or an own ntfy server
    ntfy_token: str = ""  # optional access token for protected topics
    # e-mail through the owner's own mail server (#594); no server = no e-mail
    mail_host: str = ""
    mail_port: int = 587
    mail_security: str = "starttls"  # starttls (usually port 587) | tls (from the start, usually port 465)
    mail_user: str = ""
    mail_password: str = ""
    mail_from: str = ""  # empty: the user name, if it is an e-mail address
    mail_to: list = field(default_factory=list)  # empty: to the sender
    on_unreachable: bool = True
    on_alarm: bool = True
    on_overwritten: bool = True
    on_battery_full: bool = False
    on_cheap_power: bool = False
    on_firmware: bool = True  # the inverter reports a different firmware (after an update)
    on_battery_health: bool = True  # battery cells unusually warm or far apart in temperature
    on_off_grid: bool = True  # power cut: the house runs on the battery (backup / off-grid mode)
    on_storage: bool = True  # readings cannot be stored (e.g. disk full) or little free space left (#170)
    on_wallbox: bool = False  # a car starts and ends charging at a wallbox or an evcc loadpoint (#451)


@dataclass
class EvccConfig:
    url: str = ""  # e.g. http://evcc.local:7070 ; empty = no evcc
    password: str = ""  # evcc admin password, only needed if evcc asks for a login
    # who gets solar surplus first: the wallbox (evcc) or OpenAmpere's own devices (heating rod, ...)
    priority: str = "wallbox_first"  # wallbox_first | devices_first


@dataclass
class MeterConfig:
    # customer portal of the grid operator for the daily meter values (#60): none | netze_bw (see gridmeter.PROVIDERS)
    provider: str = "none"
    username: str = ""
    password: str = ""
    meter_ids: list = field(default_factory=list)  # meters that count for the billing; empty = all of the account


@dataclass
class LogConfig:
    # info: normal operation; debug: more details for finding a fault; warning: only problems
    level: str = "info"


@dataclass
class Config:
    inverter: InverterConfig = field(default_factory=InverterConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    control: ControlConfig = field(default_factory=ControlConfig)
    tariff: TariffConfig = field(default_factory=TariffConfig)
    cloud: CloudConfig = field(default_factory=CloudConfig)
    pv: PvConfig = field(default_factory=PvConfig)
    grid: GridConfig = field(default_factory=GridConfig)
    battery: BatteryConfig = field(default_factory=BatteryConfig)
    updates: UpdatesConfig = field(default_factory=UpdatesConfig)
    notify: NotifyConfig = field(default_factory=NotifyConfig)
    evcc: EvccConfig = field(default_factory=EvccConfig)
    meter: MeterConfig = field(default_factory=MeterConfig)
    log: LogConfig = field(default_factory=LogConfig)
    inverters: list = field(default_factory=list)  # further inverters (ExtraInverter), #619
    timezone: str = "Europe/Berlin"

    def to_dict(self) -> dict:
        return asdict(self)


MAIN_INVERTER = "main"  # the id of the first inverter (config.inverter) where inverters are named by id (#619)
MIN_RATED_POWER_W = 1000  # smallest rated power the owner can confirm (grid.rated_power_w, #492); 0 = not confirmed

# Settings the web app may change, with validation limits for numbers / allowed values for strings.
EDITABLE: dict[str, tuple] = {
    "inverter.driver": ("choice", "auto", "foxess", "saj", "solarmax", "mtec", "senec", "enphase"),
    "inverter.host": ("str",),
    "inverter.port": ("int", 1, 65535),
    "inverter.unit": ("int", 0, 255),
    "inverter.register_map": ("choice", "auto", "foxess_h3_new", "foxess_h3_legacy"),
    "inverter.read_function": ("choice", "auto", "input", "holding"),
    "inverter.poll_interval": ("float", 2, 300),
    "inverter.timeout": ("float", 1, 30),
    "inverter.connection_mode": ("choice", "persistent", "per_poll"),
    "inverter.token": ("secret",),
    # not secret, but kept like the token: bound to the address, never from a backup, saved only together with it
    "inverter.cert_sha256": ("secret",),
    "inverter.name": ("str", 30),
    "inverter.capacity_kwh": ("float", 0, 200),
    "inverters": ("inverters", MAX_EXTRA_INVERTERS),
    "storage.raw_retention_days": ("int", 0, 36500),  # 0 = keep forever
    "control.enabled": ("bool",),
    "control.dry_run": ("bool",),
    "tariff.electricity_price_ct": ("float", -100, 200),
    "tariff.feed_in_ct": ("float", -100, 200),
    "tariff.feed_in_auto": ("bool",),
    "tariff.feed_in_full": ("bool",),
    "timezone": ("timezone",),
    "cloud.api_key": ("secret",),
    "pv.input_names": ("strlist", 12, 30),  # up to 12 inputs of all inverters together (#619)
    "pv.hidden_inputs": ("strlist", 12, 2),
    "pv.installed_kwp": ("float", 0, 1000),
    "pv.commissioning_date": ("date",),
    "pv.external": ("bool",),
    "pv.external_name": ("str", 30),
    "battery.capacity_kwh": ("float", 0, 200),
    "battery.max_charge_kw": ("float", 0, 50),
    "updates.check": ("bool",),
    "updates.auto": ("bool",),
    "grid.feed_in_rule": ("choice", "unknown", "limit_60", "limit_70", "operator", "none"),
    "grid.rated_power_w": ("int", 0, 100_000),
    "grid.rated_power_reported_w": ("int", 0, 65_535),
    "notify.ntfy_url": ("url",),
    "notify.ntfy_token": ("secret",),
    "notify.mail_host": ("str", 253),
    "notify.mail_port": ("int", 1, 65535),
    "notify.mail_security": ("choice", "starttls", "tls"),
    "notify.mail_user": ("str", 254),
    "notify.mail_password": ("secret",),
    "notify.mail_from": ("str", 254),
    "notify.mail_to": ("strlist", 5, 254),
    "notify.on_unreachable": ("bool",),
    "notify.on_alarm": ("bool",),
    "notify.on_overwritten": ("bool",),
    "notify.on_battery_full": ("bool",),
    "notify.on_cheap_power": ("bool",),
    "notify.on_firmware": ("bool",),
    "notify.on_battery_health": ("bool",),
    "notify.on_off_grid": ("bool",),
    "notify.on_storage": ("bool",),
    "notify.on_wallbox": ("bool",),
    "evcc.url": ("url",),
    "evcc.password": ("secret",),
    "evcc.priority": ("choice", "wallbox_first", "devices_first"),
    "meter.provider": ("choice", "none", "netze_bw"),
    "meter.username": ("str",),
    "meter.password": ("secret",),
    "meter.meter_ids": ("strlist", 10, 100),
    "log.level": ("choice", "debug", "info", "warning"),
    "server.allowed_hosts": ("hosts", 20),
}

SECRETS = {key for key, rule in EDITABLE.items() if rule[0] == "secret"}
# where a secret is sent: a secret kept on restore must keep going to the same place
# changes that belong into the control log: switching control on/off and the declared feed-in rule
# and the host names allowed besides the usual ones, which loosen the protection against DNS rebinding (#437)
AUDITED = ("control.enabled", "control.dry_run", "grid.feed_in_rule", "grid.rated_power_w", "pv.installed_kwp",
           "server.allowed_hosts")
SECRET_TARGETS = {"inverter.token": ("inverter.host",), "inverter.cert_sha256": ("inverter.host",), "evcc.password": ("evcc.url",), "notify.ntfy_token": ("notify.ntfy_url",),
                  # the password goes only to the server it was entered for; another port or user name of the same
                  # server keeps it (switching the encryption changes the port)
                  "notify.mail_password": ("notify.mail_host",),
                  "meter.password": ("meter.provider", "meter.username")}

# Settings that identify a person or give access to their data (#164). Reads are open on the home network, so these
# are only shown after login: the meter portal login and meter numbers, and the ntfy address (its topic is the only
# protection of the push messages), the e-mail addresses and the mail server (often on the owner's own domain). Facts about the plant (size, battery, commissioning date) stay readable: whoever
# is on the home network is at the house anyway, and the dashboard and the household need them.
# The own host names (#437) are a domain of the owner as often as not.
PRIVATE = {"meter.username", "meter.meter_ids", "notify.ntfy_url", "notify.mail_host", "notify.mail_user",
           "notify.mail_from", "notify.mail_to", "server.allowed_hosts"}

# Never taken from a backup, the current value stays: a crafted backup could allow a name of an attacker and so open
# the way for DNS rebinding (#437).
KEPT_ON_RESTORE = {"server.allowed_hosts"}

# a host name as the Host header has it (auth._HOST), without port; no "*", no URL, no path
_HOST_NAME = re.compile(r"[a-z0-9_-]+(?:\.[a-z0-9_-]+)*")


def _coerce(current, value):
    if isinstance(current, list):
        if isinstance(value, str):  # environment variable: comma separated
            value = [v.strip() for v in value.split(",")] if value.strip() else []
        # the further inverters (#619) are entries of their own, read by inverter_entries()
        return [v if isinstance(v, dict) else str(v) for v in value or []]
    if isinstance(current, bool):
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)
    if isinstance(current, (int, float)) and not isinstance(current, bool):
        return type(current)(value)
    return value if value is None else str(value)


def _merge(target, values: dict) -> None:
    for f in fields(target):
        if f.name not in values:
            continue
        current = getattr(target, f.name)
        if is_dataclass(current):
            _merge(current, values[f.name] or {})
        else:
            setattr(target, f.name, _coerce(current, values[f.name]))


def _apply_env(target, prefix: str, path: str, locked: set[str]) -> None:
    for f in fields(target):
        current = getattr(target, f.name)
        key = f"{path}{f.name}"
        env_key = f"{prefix}_{f.name}".upper()
        if is_dataclass(current):
            _apply_env(current, env_key, key + ".", locked)
        elif env_key in os.environ:
            setattr(target, f.name, _coerce(current, os.environ[env_key]))
            locked.add(key)


def read_yaml(path: str | os.PathLike | None = None) -> dict:
    path = Path(path or os.environ.get("OPENAMPERE_CONFIG", "config.yaml"))
    if path.is_file():
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {}


def nest(flat: dict) -> dict:
    """{"inverter.host": x} -> {"inverter": {"host": x}}"""
    out: dict = {}
    for key, value in flat.items():
        node = out
        *parents, leaf = key.split(".")
        for p in parents:
            node = node.setdefault(p, {})
        node[leaf] = value
    return out


def build_config(file_values: dict | None = None, saved: dict | None = None) -> tuple[Config, set[str]]:
    config = Config()
    _merge(config, file_values or {})
    _merge(config, nest(saved or {}))
    locked: set[str] = set()
    _apply_env(config, "OPENAMPERE", "", locked)
    config.log.level = config.log.level.strip().lower()  # OPENAMPERE_LOG_LEVEL=DEBUG as usual for log levels
    config.inverters = inverter_entries(config.inverters)
    return config, locked


def inverter_entries(values: list) -> list[ExtraInverter]:
    """The further inverters (#619) from the settings or config.yaml; one that cannot be used is left out (logged by
    validate when the app saves it)."""
    entries: list[ExtraInverter] = []
    for value in values or []:
        if isinstance(value, ExtraInverter):
            entries.append(value)
            continue
        try:
            entries.append(_inverter_entry(value))
        except (TypeError, ValueError):
            continue
    _give_ids(entries)
    return entries[:MAX_EXTRA_INVERTERS]


def _give_ids(entries: list[ExtraInverter]) -> None:
    """An id for each further inverter without one: wr2, wr3, … (the first inverter is number 1), one not taken."""
    taken = {e.id for e in entries}
    number = 2
    for entry in entries:
        if entry.id:
            continue
        while f"wr{number}" in taken:
            number += 1
        entry.id = f"wr{number}"
        taken.add(entry.id)


_INVERTER_ID = re.compile(r"[a-z0-9]{1,12}")
_INVERTER_LIMITS = {"port": (1, 65535), "unit": (0, 255), "timeout": (1, 30), "capacity_kwh": (0, 200),
                    "rated_power_w": (0, 100_000), "rated_power_reported_w": (0, 65_535)}
_INVERTER_CHOICES = {"driver": EXTRA_DRIVERS, "register_map": ("auto", "foxess_h3_new", "foxess_h3_legacy"),
                     "read_function": ("auto", "input", "holding"), "connection_mode": ("persistent", "per_poll")}


def _inverter_entry(value: dict) -> ExtraInverter:
    """One further inverter, checked like the settings of the first one; raises ValueError."""
    if not isinstance(value, dict):
        raise ValueError
    entry = ExtraInverter()
    for f in fields(ExtraInverter):
        if f.name not in value:
            continue
        current = getattr(entry, f.name)
        new = _coerce(current, value[f.name])
        if f.name in _INVERTER_LIMITS:
            lo, hi = _INVERTER_LIMITS[f.name]
            if not lo <= new <= hi:
                raise ValueError
        if f.name in _INVERTER_CHOICES and new not in _INVERTER_CHOICES[f.name]:
            raise ValueError
        if isinstance(new, str):
            new = new.strip()[:30 if f.name == "name" else 253]
        setattr(entry, f.name, new)
    entry.id = entry.id.lower()
    if (entry.id and not _INVERTER_ID.fullmatch(entry.id)) or entry.id == MAIN_INVERTER or not entry.host:
        raise ValueError
    if entry.rated_power_w and entry.rated_power_w < MIN_RATED_POWER_W:
        raise ValueError
    return entry


def validate_inverters(value) -> list[dict]:
    """The further inverters as the app saves them (#619), each checked; raises ValueError with a message for the
    owner."""
    if not isinstance(value, list) or len(value) > MAX_EXTRA_INVERTERS:
        raise ValueError(f"Es lassen sich höchstens {MAX_EXTRA_INVERTERS} weitere Wechselrichter einbinden.")
    entries = []
    for item in value:
        try:
            entries.append(_inverter_entry(item))
        except (TypeError, ValueError, OverflowError):
            name = str(item.get("name") or item.get("host") or "") if isinstance(item, dict) else ""
            raise ValueError(f"Die Angaben zum weiteren Wechselrichter „{name[:30]}“ sind ungültig. Bitte Adresse, "
                             "Port und Geräteadresse prüfen.") from None
    ids = [e.id for e in entries if e.id]
    if len(set(ids)) != len(ids):
        raise ValueError("Zwei weitere Wechselrichter haben dieselbe Kennung.")
    _give_ids(entries)
    return [asdict(e) for e in entries]


def load_config(path: str | os.PathLike | None = None) -> Config:
    return build_config(read_yaml(path))[0]


def get_value(config: Config, key: str):
    node = config
    for part in key.split("."):
        node = getattr(node, part)
    return node


LABELS = {
    "inverter.host": "IP-Adresse", "inverter.port": "Port", "inverter.unit": "Geräteadresse",
    "inverter.poll_interval": "Abfrageintervall", "inverter.timeout": "Zeitlimit",
    "inverter.token": "Zugangs-Token (Enphase)", "inverter.cert_sha256": "Zertifikat des Enphase-Gateways",
    "storage.raw_retention_days": "Aufbewahrungsdauer", "tariff.electricity_price_ct": "Strompreis",
    "tariff.feed_in_ct": "Einspeisevergütung", "pv.installed_kwp": "Modulleistung",
    "grid.rated_power_w": "Nennleistung des Wechselrichters", "grid.rated_power_reported_w": "Gemeldete Nennleistung",
    "pv.input_names": "Namen der Modulfelder",
    "pv.commissioning_date": "Inbetriebnahme", "pv.external": "Zweiter Wechselrichter",
    "pv.external_name": "Name des zweiten Wechselrichters",
    "timezone": "Zeitzone", "notify.ntfy_url": "ntfy-Adresse", "evcc.url": "evcc-Adresse",
    "cloud.api_key": "API-Schlüssel der EKD-Cloud",
    "evcc.password": "Admin-Passwort von evcc", "notify.ntfy_token": "ntfy-Zugangs-Token",
    "notify.mail_host": "Mailserver", "notify.mail_port": "Port des Mailservers",
    "notify.mail_user": "Benutzername des Postfachs", "notify.mail_password": "Passwort des Postfachs",
    "notify.mail_from": "Absender", "notify.mail_to": "Empfänger",
    "meter.provider": "Netzbetreiber", "meter.username": "E-Mail für das Kundenportal",
    "meter.password": "Passwort für das Kundenportal",
    "server.allowed_hosts": "Eigene Adressen",
    "inverter.name": "Name des Wechselrichters", "inverter.capacity_kwh": "Speichergröße dieses Wechselrichters",
    "inverters": "Weitere Wechselrichter",
}


def _invalid_message(key: str, rule: tuple) -> str:
    label = LABELS.get(key, key)
    if rule[0] in ("int", "float"):
        lo, hi = (f"{v:g}".replace(".", ",") for v in rule[1:3])
        return f"{label}: bitte eine Zahl zwischen {lo} und {hi} eingeben."
    return f"{label}: ungültiger Wert."


def restore_settings(backup: dict, current: dict, outside: Iterable[str] = ()) -> dict:
    """The settings of a restored backup: only what the app may change, each value checked like a change in the app.
    A backup holds no secrets: the current ones are kept, unless the backup sends them somewhere else (another evcc
    or ntfy address, another portal login); then they have to be entered again. outside: secrets set in config.yaml
    or by an environment variable, which stay; their addresses are not taken from the backup."""
    restored: dict = {}
    for key, value in (backup if isinstance(backup, dict) else {}).items():
        if key in SECRETS or key in KEPT_ON_RESTORE or key not in EDITABLE:
            continue
        try:
            restored.update(validate({key: value}))
        except Exception:  # noqa: BLE001 - an unusable value is left out, the default applies
            continue
    for key in KEPT_ON_RESTORE & current.keys():
        restored[key] = current[key]
    for key in outside:  # GHSA-8mjv-f2x6-2v3h
        for target in SECRET_TARGETS.get(key, ()):
            if target in current:
                restored[target] = current[target]
            else:
                restored.pop(target, None)
    for key in SECRETS & current.keys():
        if all(restored.get(target) == current.get(target) for target in SECRET_TARGETS.get(key, ())):
            restored[key] = current[key]
    return restored


def _host_names(values: list) -> list[str]:
    """The allowed host names (#437): lower case, each once, empty lines left out."""
    names: list[str] = []
    for value in values:
        name = str(value).strip().lower()
        if not name or name in names:
            continue
        if "*" in name:
            raise ValueError("„*“ schaltet den Schutz gegen DNS-Rebinding ab und lässt sich deshalb in der App nicht "
                             "eintragen. Bitte jeden Namen einzeln eintragen, zum Beispiel solar.example.de.")
        if len(name) > 253 or not _HOST_NAME.fullmatch(name):
            raise ValueError(f"„{name[:60]}“ ist kein Hostname. Bitte nur den Namen eintragen, ohne https://, "
                             "Port oder Pfad, zum Beispiel solar.example.de.")
        names.append(name)
    return names


def validate(changes: dict) -> dict:
    """Validates web-app changes; returns the cleaned values or raises ValueError."""
    clean = {}
    defaults = Config()
    for key, value in changes.items():
        rule = EDITABLE.get(key)
        if rule is None:
            raise ValueError(f"Die Einstellung {key} kann in der App nicht geändert werden.")
        kind = rule[0]
        try:
            if kind == "choice":
                if value not in rule[1:]:
                    raise ValueError
            elif kind in ("int", "float"):
                value = _coerce(get_value(defaults, key), value)
                if not rule[1] <= value <= rule[2]:
                    raise ValueError
            elif kind == "bool":
                value = _coerce(True, value)
            elif kind == "url":
                value = str(value).strip()
                if value and not value.startswith(("https://", "http://")):
                    raise ValueError
            elif kind == "date":
                value = str(value).strip() and date.fromisoformat(str(value).strip()).isoformat()  # "" = not set
                if value and not "2000-01-01" <= value <= "2099-12-31":
                    raise ValueError
            elif kind == "timezone":
                from zoneinfo import ZoneInfo
                value = str(value).strip()
                ZoneInfo(value)  # raises for unknown names
            elif kind == "hosts":
                if not isinstance(value, list) or len(value) > rule[1]:
                    raise ValueError
            elif kind == "inverters":
                value = validate_inverters(value)  # with its own messages
            elif kind == "strlist":
                if not isinstance(value, list) or len(value) > rule[1]:
                    raise ValueError
                value = [str(v).strip()[: rule[2]] for v in value]
            else:
                value = str(value).strip()[: rule[1] if len(rule) > 1 else None]
        except (TypeError, ValueError, LookupError) as err:
            if kind == "inverters" and str(err):
                raise
            raise ValueError(_invalid_message(key, rule)) from None
        if kind == "hosts":
            value = _host_names(value)  # with its own messages, not the general one
        if key == "inverter.cert_sha256":
            value = value.lower()
            if value and not re.fullmatch(r"[0-9a-f]{64}", value):
                raise ValueError("Der Fingerabdruck des Zertifikats ist ungültig. Bitte die Verbindung beim Einrichten "
                                 "noch einmal prüfen und speichern.")
        if key == "grid.rated_power_w" and 0 < value < MIN_RATED_POWER_W:
            # the base of the export limit in per mille (#492): a tiny one would turn a small limit into full feed-in
            raise ValueError(f"Die Nennleistung vom Typenschild muss mindestens {MIN_RATED_POWER_W} W betragen.")
        if key == "notify.ntfy_url" and "@" in value:
            # a password in the address would be shown in the settings, kept in backups and quoted in errors
            raise ValueError("Benutzername und Passwort gehören nicht in die ntfy-Adresse. Bitte die Adresse ohne sie "
                             "eintragen. Für ein geschütztes Thema gehört ein Token von ntfy in das Feld „Zugangs-Token“.")
        if key == "notify.mail_host" and value and not mail.HOST.fullmatch(value):
            raise ValueError("Mailserver: bitte nur den Namen eintragen, ohne https://, Port oder Pfad, zum Beispiel "
                             "smtp.example.de.")
        if key == "notify.mail_from" and value and not mail.ADDRESS.fullmatch(value):
            raise ValueError("Absender: bitte eine E-Mail-Adresse eintragen, zum Beispiel name@example.de.")
        if key == "notify.mail_to":
            value = [v for v in value if v]
            wrong = next((v for v in value if not mail.ADDRESS.fullmatch(v)), None)
            if wrong is not None:
                raise ValueError("Empfänger: bitte nur E-Mail-Adressen eintragen, eine pro Zeile, zum Beispiel "
                                 "name@example.de.")
        label = LABELS.get(key, key)
        if kind == "url" and "@" in urlsplit(value).netloc:
            # like the ntfy address: an address can be shown without login, kept in backups and quoted in errors (#353)
            raise ValueError(f"Benutzername und Passwort gehören nicht in die {label}. Bitte die Adresse ohne sie "
                             "eintragen.")
        if kind == "secret" and value.startswith(ENCRYPTED_PREFIX):
            # it would be taken for an encrypted value when it is read again, and get lost (#353)
            raise ValueError(f"{label}: Der Wert beginnt mit „{ENCRYPTED_PREFIX}“. So beginnen nur verschlüsselt "
                             "gespeicherte Werte, deshalb kann OpenAmpere ihn nicht speichern. Bitte den Wert so "
                             "eintragen, wie du ihn bekommen hast.")
        clean[key] = value
    return clean
