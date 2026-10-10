# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Meter readings from the grid operator (#60).

The grid operator bills by its own meters, not by the inverter. Many operators show the daily values of a smart meter
in their customer portal. OpenAmpere fetches them with the user's portal login and the analysis compares the
prepayments with these values where they exist.

Grid operators are regional, so every operator is a provider module (see base.Provider) listed in PROVIDERS.
Adding one: a module with meters() and daily(), an entry here and a test with a fake portal (CONTRIBUTING.md).
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

from .base import Meter, Provider, ProviderAuthError, ProviderError
from .netze_bw import NetzeBw

if TYPE_CHECKING:
    from ..runtime import Runtime

log = logging.getLogger(__name__)

PROVIDERS: dict[str, type[Provider]] = {p.key: p for p in (NetzeBw,)}
SYNC_EVERY_S = 3 * 3600  # operators publish the day before at no fixed time
RETRY_AFTER_ERROR_S = 3600
BACKFILL_DAYS = 400  # the running billing year and a bit of the one before
RECHECK_DAYS = 7  # the operator may still correct recent days

__all__ = ["PROVIDERS", "GridMeter", "Meter", "Provider", "ProviderAuthError", "ProviderError"]


class GridMeter:
    def __init__(self, runtime: Runtime, providers: dict[str, type[Provider]] | None = None) -> None:
        self.runtime = runtime
        self.providers = providers or PROVIDERS
        self.synced = 0.0
        self.error: str | None = None
        self.busy = False
        self._client: Provider | None = None
        self._login: tuple | None = None
        self._refused: tuple | None = None  # login the portal refused: not retried by itself (the account could lock)

    @property
    def cfg(self):
        return self.runtime.config.meter

    @property
    def configured(self) -> bool:
        return self.cfg.provider in self.providers and bool(self.cfg.username and self.cfg.password)

    @property
    def label(self) -> str:
        provider = self.providers.get(self.cfg.provider)
        return provider.label if provider else ""

    def _meters(self) -> list[Meter]:
        return [Meter(**m) for m in self.runtime.storage.get_meta(f"grid_meters_{self.cfg.provider}") or []]

    def active_ids(self) -> list[str]:
        """Stored meter ids that count for the billing: the chosen meters of the configured operator."""
        if not self.configured:
            return []
        ids = [m.id for m in self._meters()]
        chosen = [i for i in ids if i in self.cfg.meter_ids] or ids
        return [f"{self.cfg.provider}:{i}" for i in chosen]

    def view(self) -> dict:
        storage = self.runtime.storage
        active = self.active_ids()
        ends = [day for m in active for kind in ("import", "export") if (day := storage.meter_last_day(m, kind))]
        return {"providers": [p.info() for p in self.providers.values()], "configured": self.configured,
                "meters": [m.to_dict() for m in self._meters()] if self.configured else [],
                "active": [i.split(":", 1)[1] for i in active], "synced": self.synced or None,
                "until": max(ends) if ends else None, "error": self.error, "busy": self.busy}

    def _provider(self) -> Provider:
        login = (self.cfg.provider, self.cfg.username, self.cfg.password)
        if self._client is None or self._login != login:  # keep the session while the login stays the same
            self._client, self._login = self.providers[self.cfg.provider](self.cfg.username, self.cfg.password), login
        return self._client

    def _fetch(self, today: date) -> None:
        provider = self._provider()
        meters = provider.meters()
        self.runtime.storage.set_meta(f"grid_meters_{self.cfg.provider}", [m.to_dict() for m in meters])
        chosen = [m for m in meters if m.id in self.cfg.meter_ids] or meters
        for meter in chosen:
            stored = f"{provider.key}:{meter.id}"
            for kind in meter.kinds:
                last = self.runtime.storage.meter_last_day(stored, kind)
                first = today - timedelta(days=BACKFILL_DAYS)
                if last:
                    first = max(first, date.fromisoformat(last) - timedelta(days=RECHECK_DAYS))
                days = provider.daily(meter, kind, first, today + timedelta(days=1))
                # the answer counts for the whole range: a day stored before but incomplete now is unknown again
                self.runtime.storage.replace_meter_days(stored, kind, first.isoformat(), days)

    async def sync(self, force: bool = False, now: float | None = None) -> None:
        now = time.time() if now is None else now
        if not self.configured or self.busy:
            return
        login = (self.cfg.provider, self.cfg.username, self.cfg.password)
        if not force and self._refused == login:
            return
        wait = RETRY_AFTER_ERROR_S if self.error else SYNC_EVERY_S
        if not force and self.synced and now - self.synced < wait:
            return
        self.busy = True
        try:
            await asyncio.to_thread(self._fetch, datetime.fromtimestamp(now, self.runtime.tz).date())
            self.error, self._refused = None, None
        except ProviderAuthError as err:
            self.error, self._refused = str(err), login
            log.info("grid meter: %s", err)
        except ProviderError as err:
            self.error = str(err)
            log.info("grid meter: %s", err)
        except Exception as err:  # noqa: BLE001 - unexpected portal answer: show it and try again later
            self.error = "Unerwartete Antwort vom Kundenportal. Bitte später nochmal versuchen."
            log.warning("grid meter sync failed: %r", err)
        finally:
            self.synced, self.busy = now, False

    tick = sync
