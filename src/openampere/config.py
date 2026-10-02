"""Configuration.

Precedence (lowest to highest):
  defaults  <  config.yaml  <  settings saved in the web app (database)  <  OPENAMPERE_* environment variables

Values set through environment variables are reported as "locked" so the web app can show them read-only.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field, fields, is_dataclass
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


@dataclass
class StorageConfig:
    path: str = "data/openampere.db"
    raw_retention_days: int = 30


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8080


@dataclass
class ControlConfig:
    enabled: bool = False  # master switch for anything that writes to the inverter
    dry_run: bool = True  # log intended writes instead of executing them


@dataclass
class TariffConfig:
    electricity_price_ct: float = 35.0  # gross price per kWh drawn from the grid
    feed_in_ct: float = 8.0  # feed-in compensation per kWh


@dataclass
class PvConfig:
    input_names: list = field(default_factory=list)  # e.g. ["Süddach", "Garage"]; empty = "Modulfeld 1", ...


@dataclass
class CloudConfig:
    api_key: str = ""  # personal key from the former vendor app (Mehr -> Konfiguration API-Zugang)
    base_url: str = "https://product.ekd-iot.de"  # customer API of the former vendor cloud


@dataclass
class Config:
    inverter: InverterConfig = field(default_factory=InverterConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    control: ControlConfig = field(default_factory=ControlConfig)
    tariff: TariffConfig = field(default_factory=TariffConfig)
    cloud: CloudConfig = field(default_factory=CloudConfig)
    pv: PvConfig = field(default_factory=PvConfig)
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
    "storage.raw_retention_days": ("int", 1, 3650),
    "control.enabled": ("bool",),
    "control.dry_run": ("bool",),
    "tariff.electricity_price_ct": ("float", -100, 200),
    "tariff.feed_in_ct": ("float", -100, 200),
    "timezone": ("str",),
    "cloud.api_key": ("secret",),
    "pv.input_names": ("strlist", 6, 30),
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
    return config, locked


def load_config(path: str | os.PathLike | None = None) -> Config:
    return build_config(read_yaml(path))[0]


def get_value(config: Config, key: str):
    node = config
    for part in key.split("."):
        node = getattr(node, part)
    return node


def validate(changes: dict) -> dict:
    """Validates web-app changes; returns the cleaned values or raises ValueError."""
    clean = {}
    defaults = Config()
    for key, value in changes.items():
        rule = EDITABLE.get(key)
        if rule is None:
            raise ValueError(f"{key} cannot be changed")
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
            elif kind == "strlist":
                if not isinstance(value, list) or len(value) > rule[1]:
                    raise ValueError
                value = [str(v).strip()[: rule[2]] for v in value]
            else:
                value = str(value).strip()
        except (TypeError, ValueError):
            raise ValueError(f"invalid value for {key}") from None
        clean[key] = value
    return clean
