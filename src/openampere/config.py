"""Configuration.

Precedence (lowest to highest):
  defaults  <  config.yaml  <  settings saved in the web app (database)  <  OPENAMPERE_* environment variables

Values set through environment variables are reported as "locked" so the web app can show them read-only.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from datetime import date
from pathlib import Path

import yaml


@dataclass
class InverterConfig:
    driver: str = "auto"  # auto | foxess | saj
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


@dataclass
class StorageConfig:
    path: str = "data/openampere.db"
    raw_retention_days: int = 30  # 0 = keep forever


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8080
    # extra host names the web app may be opened with (besides IPs, localhost and typical home-network
    # names such as *.local, *.fritz.box, *.ts.net); "*" disables the check (only behind a trusted proxy)
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


@dataclass
class CloudConfig:
    api_key: str = ""  # personal key from the former vendor app (Mehr -> Konfiguration API-Zugang)
    base_url: str = "https://product.ekd-iot.de"  # customer API of the former vendor cloud


@dataclass
class NotifyConfig:
    ntfy_url: str = ""  # e.g. https://ntfy.sh/<own secret topic> or an own ntfy server
    ntfy_token: str = ""  # optional access token for protected topics
    on_unreachable: bool = True
    on_alarm: bool = True
    on_overwritten: bool = True
    on_battery_full: bool = False
    on_cheap_power: bool = False
    on_firmware: bool = True  # the inverter reports a different firmware (after an update)
    on_battery_health: bool = True  # battery cells unusually warm or far apart in temperature
    on_off_grid: bool = True  # power cut: the house runs on the battery (backup / off-grid mode)
    on_storage: bool = True  # readings cannot be stored (e.g. disk full) or little free space left (#170)


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
    timezone: str = "Europe/Berlin"

    def to_dict(self) -> dict:
        return asdict(self)


# Settings the web app may change, with validation limits for numbers / allowed values for strings.
EDITABLE: dict[str, tuple] = {
    "inverter.driver": ("choice", "auto", "foxess", "saj"),
    "inverter.host": ("str",),
    "inverter.port": ("int", 1, 65535),
    "inverter.unit": ("int", 0, 255),
    "inverter.register_map": ("choice", "auto", "foxess_h3_new", "foxess_h3_legacy"),
    "inverter.read_function": ("choice", "auto", "input", "holding"),
    "inverter.poll_interval": ("float", 2, 300),
    "inverter.timeout": ("float", 1, 30),
    "inverter.connection_mode": ("choice", "persistent", "per_poll"),
    "storage.raw_retention_days": ("int", 0, 36500),  # 0 = keep forever
    "control.enabled": ("bool",),
    "control.dry_run": ("bool",),
    "tariff.electricity_price_ct": ("float", -100, 200),
    "tariff.feed_in_ct": ("float", -100, 200),
    "tariff.feed_in_auto": ("bool",),
    "tariff.feed_in_full": ("bool",),
    "timezone": ("timezone",),
    "cloud.api_key": ("secret",),
    "pv.input_names": ("strlist", 6, 30),
    "pv.hidden_inputs": ("strlist", 6, 2),
    "pv.installed_kwp": ("float", 0, 1000),
    "pv.commissioning_date": ("date",),
    "battery.capacity_kwh": ("float", 0, 200),
    "battery.max_charge_kw": ("float", 0, 50),
    "updates.check": ("bool",),
    "updates.auto": ("bool",),
    "grid.feed_in_rule": ("choice", "unknown", "limit_60", "limit_70", "operator", "none"),
    "notify.ntfy_url": ("url",),
    "notify.ntfy_token": ("secret",),
    "notify.on_unreachable": ("bool",),
    "notify.on_alarm": ("bool",),
    "notify.on_overwritten": ("bool",),
    "notify.on_battery_full": ("bool",),
    "notify.on_cheap_power": ("bool",),
    "notify.on_firmware": ("bool",),
    "notify.on_battery_health": ("bool",),
    "notify.on_off_grid": ("bool",),
    "notify.on_storage": ("bool",),
    "evcc.url": ("url",),
    "evcc.password": ("secret",),
    "evcc.priority": ("choice", "wallbox_first", "devices_first"),
    "meter.provider": ("choice", "none", "netze_bw"),
    "meter.username": ("str",),
    "meter.password": ("secret",),
    "meter.meter_ids": ("strlist", 10, 100),
    "log.level": ("choice", "debug", "info", "warning"),
}

SECRETS = {key for key, rule in EDITABLE.items() if rule[0] == "secret"}


def _coerce(current, value):
    if isinstance(current, list):
        if isinstance(value, str):  # environment variable: comma separated
            value = [v.strip() for v in value.split(",")] if value.strip() else []
        return [str(v) for v in value or []]
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
    return config, locked


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
    "storage.raw_retention_days": "Aufbewahrungsdauer", "tariff.electricity_price_ct": "Strompreis",
    "tariff.feed_in_ct": "Einspeisevergütung", "pv.installed_kwp": "Modulleistung", "pv.input_names": "Namen der Modulfelder",
    "pv.commissioning_date": "Inbetriebnahme",
    "timezone": "Zeitzone", "notify.ntfy_url": "ntfy-Adresse", "evcc.url": "evcc-Adresse",
}


def _invalid_message(key: str, rule: tuple) -> str:
    label = LABELS.get(key, key)
    if rule[0] in ("int", "float"):
        lo, hi = (f"{v:g}".replace(".", ",") for v in rule[1:3])
        return f"{label}: bitte eine Zahl zwischen {lo} und {hi} eingeben."
    return f"{label}: ungültiger Wert."


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
            elif kind == "strlist":
                if not isinstance(value, list) or len(value) > rule[1]:
                    raise ValueError
                value = [str(v).strip()[: rule[2]] for v in value]
            else:
                value = str(value).strip()
        except (TypeError, ValueError, LookupError):
            raise ValueError(_invalid_message(key, rule)) from None
        clean[key] = value
    return clean
