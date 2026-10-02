"""Holds configuration, storage and collector; applies settings changes at runtime."""

from __future__ import annotations

import logging
from zoneinfo import ZoneInfo

from .collector import Collector
from .config import EDITABLE, SECRETS, Config, build_config, get_value, read_yaml, validate
from .drivers import registry
from .cloud_import import CloudImport
from .storage import Storage

log = logging.getLogger(__name__)

RECONNECT_KEYS = {k for k in EDITABLE if k.startswith("inverter.")}


def make_driver(config: Config):
    inv = config.inverter
    if not inv.host:
        return None
    options = dict(timeout=inv.timeout, register_map=inv.register_map, read_function=inv.read_function)
    if inv.driver == "auto":
        return registry.AutoDriver(inv.host, inv.port, inv.unit, **options)
    if inv.driver not in registry.DRIVERS:
        raise ValueError(f"unknown inverter driver {inv.driver!r}")
    return registry.create(inv.driver, inv.host, inv.port, inv.unit, **options)


class Runtime:
    def __init__(self, file_values: dict, storage: Storage) -> None:
        self.file_values = file_values
        self.storage = storage
        self.config, self.locked = build_config(file_values, storage.get_settings())
        self.collector = Collector(make_driver(self.config), storage, self.config.inverter.poll_interval,
                                   self.config.storage.raw_retention_days,
                                   release_connection=self.config.inverter.connection_mode == "per_poll")
        self.cloud_import = CloudImport(storage, lambda: (self.config.cloud.api_key, self.config.cloud.base_url))

    @classmethod
    def from_files(cls, config_path: str | None = None) -> Runtime:
        file_values = read_yaml(config_path)
        bootstrap, _ = build_config(file_values)  # storage path may come from file or env only
        return cls(file_values, Storage(bootstrap.storage.path))

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.config.timezone)

    def settings_view(self) -> dict:
        """Secrets are never sent back; only whether they are set and their last characters."""
        values = {key: get_value(self.config, key) for key in EDITABLE if key not in SECRETS}
        secrets = {}
        for key in SECRETS:
            value = get_value(self.config, key)
            secrets[key] = {"set": bool(value), "hint": f"…{value[-4:]}" if len(value) >= 8 else None}
        return {"values": values, "secrets": secrets, "locked": sorted(self.locked & set(EDITABLE))}

    async def update_settings(self, changes: dict) -> dict:
        clean = validate(changes)
        blocked = set(clean) & self.locked
        if blocked:
            raise PermissionError(f"set by environment variable: {', '.join(sorted(blocked))}")
        if "timezone" in clean:
            ZoneInfo(clean["timezone"])  # raises for unknown zones

        saved = {**self.storage.get_settings(), **clean}
        old = self.config
        new, locked = build_config(self.file_values, saved)
        make_driver(new)  # validate before persisting
        self.storage.save_settings(saved)
        self.config, self.locked = new, locked
        log.info("settings changed: %s", ", ".join(sorted(clean)))

        self.collector.retention_days = new.storage.raw_retention_days
        control_changes = {k: (get_value(old, k), get_value(new, k))
                           for k in ("control.enabled", "control.dry_run", "grid.feed_in_rule", "pv.installed_kwp")
                           if get_value(old, k) != get_value(new, k)}
        if control_changes:  # switching control on/off and the declared feed-in rule belong into the audit log
            self.storage.log_control("control_switches", {"from": {k: v[0] for k, v in control_changes.items()},
                                                          "to": {k: v[1] for k, v in control_changes.items()}},
                                     False, "ok")
        if get_value(old, "cloud.api_key") != get_value(new, "cloud.api_key"):
            await self.cloud_import.stop()
            self.cloud_import.reset()
        if any(get_value(old, k) != get_value(new, k) for k in RECONNECT_KEYS):
            await self.collector.reconfigure(make_driver(new), new.inverter.poll_interval,
                                             release_connection=new.inverter.connection_mode == "per_poll")
        return self.settings_view()
