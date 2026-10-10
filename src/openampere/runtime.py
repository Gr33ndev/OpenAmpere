# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Holds configuration, storage and collector; applies settings changes at runtime."""

from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from . import eeg, logs, tls
from .collector import Collector
from .config import EDITABLE, PRIVATE, SECRETS, Config, build_config, get_value, read_yaml, validate
from .drivers import registry
from .cloud_import import CloudImport
from .secretbox import SecretBox
from .storage import Storage
from .tariffs import Tariffs

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
        self.secrets = SecretBox(Path(storage.path).resolve().parent / "secret.key")
        self._tls: tls.Certificate | None = None
        self.tls_error: str | None = None  # the HTTPS port could not be opened (#76)
        # set by the server: ends it, after which the process starts again (a restored backup is put in place then)
        self.restart_hook: Callable[[], None] | None = None
        self.restart_requested = False
        self.config, self.locked = build_config(file_values, self._load_settings())
        logs.apply_level(self.config.log.level)
        self.collector = Collector(make_driver(self.config), storage, self.config.inverter.poll_interval,
                                   self.config.storage.raw_retention_days,
                                   release_connection=self.config.inverter.connection_mode == "per_poll")
        self.cloud_import = CloudImport(storage, lambda: (self.config.cloud.api_key, self.config.cloud.base_url))
        self.tariffs = Tariffs(storage, lambda: (self.config.tariff.electricity_price_ct, self.config.tariff.feed_in_ct),
                               self.eeg_rate)

    @classmethod
    def from_files(cls, config_path: str | None = None) -> Runtime:
        file_values = read_yaml(config_path)
        bootstrap, _ = build_config(file_values)  # storage path may come from file or env only
        return cls(file_values, Storage(bootstrap.storage.path))

    def _load_settings(self) -> dict:
        """The saved settings with the secrets decrypted; secrets of older versions get encrypted now. Only settings
        the app may change: others (e.g. from a crafted backup restored by an older version) are ignored."""
        stored = self.storage.get_settings()
        ignored = sorted(set(stored) - set(EDITABLE))
        if ignored:
            log.warning("saved settings ignored, they cannot be changed in the app: %s", ", ".join(ignored))
            stored = {k: v for k, v in stored.items() if k in EDITABLE}
        plain = {k: self.secrets.decrypt(k, v) if k in SECRETS else v for k, v in stored.items()}
        if any(k in SECRETS and v and not SecretBox.is_encrypted(v) for k, v in stored.items()):
            self._save_settings(plain)
            log.info("stored secrets are encrypted now")
        return plain

    def _save_settings(self, plain: dict) -> None:
        self.storage.save_settings({k: self.secrets.encrypt(k, v) if k in SECRETS and isinstance(v, str) else v
                                    for k, v in plain.items()})

    def eeg_view(self) -> dict:
        """Feed-in compensation under the EEG for this plant (#71): the rate, or why it cannot be determined."""
        tariff, pv = self.config.tariff, self.config.pv
        view = {"auto": tariff.feed_in_auto, "full": tariff.feed_in_full, "commissioning_date": pv.commissioning_date,
                "installed_kwp": pv.installed_kwp, "rate": None, "error": None}
        try:
            if not pv.commissioning_date:
                raise ValueError("Bitte das Datum der Inbetriebnahme angeben.")
            view["rate"] = asdict(eeg.rate(date.fromisoformat(pv.commissioning_date), pv.installed_kwp,
                                           tariff.feed_in_full))
        except ValueError as err:
            view["error"] = str(err)
        return view

    def eeg_rate(self) -> eeg.FeedInRate | None:
        """The EEG rate when the compensation is determined automatically, otherwise None (own value per tariff)."""
        tariff, pv = self.config.tariff, self.config.pv
        if not tariff.feed_in_auto or not pv.commissioning_date:
            return None
        try:
            return eeg.rate(date.fromisoformat(pv.commissioning_date), pv.installed_kwp, tariff.feed_in_full)
        except ValueError:
            return None

    def request_restart(self) -> bool:
        """Starts OpenAmpere again; False if this server cannot do it itself (tests, embedded use)."""
        self.restart_requested = True
        if self.restart_hook is None:
            return False
        self.restart_hook()
        return True

    @property
    def tls(self) -> tls.Certificate:
        """Certificate of the HTTPS port, created next to the database on first use (#76)."""
        if self._tls is None:
            self._tls = tls.ensure_certificate(Path(self.storage.path).resolve().parent)
        return self._tls

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.config.timezone)

    def settings_view(self, authenticated: bool = True) -> dict:
        """Secrets are never sent back; only whether they are set and their last characters.

        Without login the private values (config.PRIVATE) are null and listed under "hidden", and secrets show no
        hint (#164): reads are open on the home network, these values identify the owner or give access to data."""
        hidden = set() if authenticated else PRIVATE
        values = {key: None if key in hidden else get_value(self.config, key) for key in EDITABLE if key not in SECRETS}
        secrets = {}
        for key in SECRETS:
            value = get_value(self.config, key)
            # the end of a long key helps to recognise it, a password shows nothing of itself
            hint = f"…{value[-4:]}" if authenticated and len(value) >= 8 and not key.endswith(".password") else None
            secrets[key] = {"set": bool(value), "hint": hint}
        return {"values": values, "secrets": secrets, "locked": sorted(self.locked & set(EDITABLE)),
                "hidden": sorted(hidden), "revision": self.settings_revision}

    @property
    def settings_revision(self) -> int:
        """Counts saved changes, so a second device editing an outdated form is noticed."""
        return int(self.storage.get_meta("settings_revision") or 0)

    async def update_settings(self, changes: dict) -> dict:
        clean = validate(changes)
        blocked = set(clean) & self.locked
        if blocked:
            raise PermissionError(f"Fest eingestellt (Umgebungsvariable), in der App nicht änderbar: {', '.join(sorted(blocked))}")
        if "timezone" in clean:
            ZoneInfo(clean["timezone"])  # raises for unknown zones

        saved = {**self._load_settings(), **clean}
        old = self.config
        new, locked = build_config(self.file_values, saved)
        make_driver(new)  # validate before persisting
        self._save_settings(saved)
        self.storage.set_meta("settings_revision", self.settings_revision + 1)
        self.config, self.locked = new, locked
        log.info("settings changed: %s", ", ".join(sorted(clean)))

        self.collector.retention_days = new.storage.raw_retention_days
        if old.log.level != new.log.level:
            logs.apply_level(new.log.level)
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
