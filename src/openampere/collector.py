# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Polls the inverter, keeps the latest snapshot, stores it and notifies subscribers."""

from __future__ import annotations

import asyncio
import logging
import time

from .drivers.base import DeviceInfo, InverterDriver, Snapshot, raise_if_cancelled
from .drivers.modbus import friendly_error
from .outages import Outages
from .storage import Storage

log = logging.getLogger(__name__)

MAX_TRANSIENT = 3  # consecutive temporary errors tolerated before the inverter counts as disconnected
# below this, the owner is warned: SQLite needs room for its journal, and a full disk stops the history (#170)
LOW_DISK_BYTES = 200 * 1024 * 1024
STORAGE_LOG_EVERY_S = 3600  # while writing keeps failing, repeat the error in the log only this often


def storage_problem(err: BaseException) -> str:
    """What kind of storage error this is, for the notice in the app: full | read_only | other."""
    text = str(err).lower()
    errno = getattr(err, "errno", None)
    if errno == 28 or "disk is full" in text or "no space" in text:  # ENOSPC
        return "full"
    if errno in (13, 30) or "readonly" in text or "read-only" in text or "permission denied" in text:  # EACCES, EROFS
        return "read_only"
    return "other"


class Collector:
    def __init__(self, driver: InverterDriver | None, storage: Storage, interval: float, retention_days: int,
                 *, release_connection: bool = False) -> None:
        self.driver = driver
        self.release_connection = release_connection  # close the TCP connection after every reading
        self.storage = storage
        self.outages = Outages(storage)
        self.interval = interval
        self.retention_days = retention_days
        self.latest: Snapshot | None = None
        self.device: DeviceInfo | None = None
        self.connected = False
        self.connected_at: float | None = None  # when the current connection was established (#143)
        self.last_error: str | None = None
        self.disconnected_since: float | None = None  # first failed attempt since the last good reading
        # readings cannot be written to the database (e.g. disk full) since this time; None while it works (#170)
        self.storage_failing_since: float | None = None
        self.storage_error: str | None = None  # full | read_only | other
        self._storage_logged = 0.0
        self._last_today_pv: float | None = None
        self.external_pv = False  # count an extra PV inverter the inverter measures (setting pv.external, #419)
        self.rated_power = (0, 0)  # (reported, confirmed) rated power the owner confirmed (grid.rated_power_w, #492)
        self.external_pv_w: float | None = None  # what that meter reads, also before the owner counts it
        self._subscribers: set[asyncio.Queue] = set()
        self._task: asyncio.Task | None = None

    @property
    def configured(self) -> bool:
        return self.driver is not None

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=5)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def _publish(self, snap: Snapshot) -> None:
        for queue in list(self._subscribers):
            if queue.full():
                queue.get_nowait()  # slow client: drop the oldest reading
            queue.put_nowait(snap)

    def start(self) -> None:
        if self.driver is not None:
            self._task = asyncio.create_task(self._run(), name="collector")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await asyncio.wait_for(self._task, timeout=10)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            self._task = None
        if self.driver is not None:
            await self.driver.close()

    async def reconfigure(self, driver: InverterDriver | None, interval: float, *, release_connection: bool = False) -> None:
        """Switch to a new inverter connection without restarting the app."""
        await self.stop()
        self.driver = driver
        self.interval = interval
        self.release_connection = release_connection
        self.device = None
        self.connected = False
        self.connected_at = None
        self.disconnected_since = None
        self.last_error = None
        self.latest = None
        self.start()

    @property
    def stale(self) -> bool:
        """True when the last reading is too old to be shown as live."""
        return self.stale_after(30)

    def stale_after(self, seconds: float) -> bool:
        """Not connected, or no reading for three poll intervals and at least `seconds`."""
        if self.latest is None:
            return True
        return not self.connected or time.time() - self.latest.timestamp > max(3 * self.poll_interval(), seconds)

    def min_gap(self) -> float:
        """The shortest time between two requests the device stands, 0 for none."""
        try:
            return getattr(self.driver, "MIN_POLL_S", 0.0)
        except ConnectionError:  # the automatic driver before it detected the device
            return 0.0

    def token_expires(self) -> float | None:
        """When the device's access token runs out (Enphase, #538), None without one."""
        try:
            return getattr(self.driver, "token_expires", None)
        except ConnectionError:  # the automatic driver before it detected the device
            return None

    def token_days_left(self) -> int | None:
        """Whole days until the access token runs out (negative: since it ran out), None without one. What the app and
        Home Assistant show: never the date, which would tell the day the owner made the token."""
        from .drivers.enphase.driver import token_days_left
        return token_days_left(self.token_expires())

    def poll_interval(self) -> float:
        """The poll interval, but at least what the device stands (a Senec restarts when asked too often, #521)."""
        return max(self.interval, self.min_gap())

    def _check_firmware(self) -> None:
        """An update can change registers: remember the firmware and note when it changes."""
        if not self.device:
            return
        try:
            change = self.storage.note_firmware(self.device.serial, self.device.firmware)
        except Exception:  # noqa: BLE001 - must never disturb polling
            return
        if change:
            log.warning("inverter firmware changed: %s -> %s", change["old"], change["new"])
            self._event("firmware", f"{change['old']} -> {change['new']}")

    def _event(self, kind: str, detail: str = "") -> None:
        """Connection history for the diagnostics (e.g. does the inverter refuse connections at night?)."""
        try:
            events = (self.storage.get_meta("connection_events") or [])[-99:]
            events.append({"ts": time.time(), "event": kind, "detail": detail[:200]})
            self.storage.set_meta("connection_events", events)
        except Exception:  # noqa: BLE001 - diagnostics must never disturb polling
            pass

    def _watch_daily_reset(self, snap: Snapshot) -> None:
        """Remembers when the inverter resets its daily counters (its own clock, maybe without DST)."""
        value = snap.today.pv
        previous, self._last_today_pv = self._last_today_pv, value
        if value is None or previous is None or value >= previous - 50:
            return
        try:
            resets = (self.storage.get_meta("daily_resets") or [])[-9:]
            resets.append(snap.timestamp)
            self.storage.set_meta("daily_resets", resets)
        except Exception:  # noqa: BLE001
            pass

    async def _store(self, snap: Snapshot) -> None:
        """Storage problems (e.g. disk full) must not tear down the Modbus connection. They are shown in the app and
        sent as a notification instead (#170); the log gets them once and then only every hour, not every poll."""
        try:
            await asyncio.to_thread(self.storage.add_snapshot, snap)
            # the second PV inverter (#419): a lifetime counter of its own for Home Assistant's energy dashboard, and
            # part of the consumption like in the app. Switched off, the integral stays as it is, so the consumption
            # counter never goes back (Home Assistant would count that as a meter reset).
            external = self.storage.external_total()
            if external and snap.totals.consumption is not None:
                snap.totals.consumption += external
            if snap.pv_external_power is not None:
                snap.totals.pv_external = external
        except Exception as err:  # noqa: BLE001
            now = time.time()
            self.storage_error = storage_problem(err)
            if self.storage_failing_since is None:
                self.storage_failing_since = now
                self._storage_logged = now
                log.error("could not store reading: %s", err)
            elif now - self._storage_logged >= STORAGE_LOG_EVERY_S:
                self._storage_logged = now
                log.error("still cannot store readings (for %.0f min): %s", (now - self.storage_failing_since) / 60, err)
            return
        if self.storage_failing_since is not None:
            log.info("readings are stored again after %.0f min", (time.time() - self.storage_failing_since) / 60)
            self.storage_failing_since = None
            self.storage_error = None

    def storage_state(self) -> dict:
        """Whether readings get stored (#170): a failed write and how much space is left on the disk."""
        free = self.storage.free_bytes()
        return {"failing_since": self.storage_failing_since, "error": self.storage_error, "free_bytes": free,
                "low_space": free is not None and free < LOW_DISK_BYTES}

    def energy_step_wh(self) -> int:
        """Step of the energy counters in Wh (#194), so the app shows no decimal the inverter does not count:
        what the recorded data shows, else what the driver's register map says, else 0.01 kWh."""
        hint = self.device.energy_step_wh if self.device else None
        return self.storage.energy_step_wh() or hint or 10

    async def _run(self) -> None:
        assert self.driver is not None
        backoff = 5.0
        last_prune = 0.0
        transient_failures = 0
        while True:
            try:
                if not self.connected:
                    self.device = await self.driver.connect()
                    self.device.confirm_rated_power(*self.rated_power)
                if self.external_pv:
                    # a second inverter the owner counts is there at night too (the Solarmax driver reports one only
                    # once it saw it, which a restart forgets, #519)
                    getattr(self.driver, "assume_external", lambda: None)()
                started = time.monotonic()
                snap = (await self.driver.read()).sanitize()
                self.external_pv_w = snap.pv_external_power
                snap.count_external_pv(self.external_pv)
                log.debug("reading took %.0f ms", (time.monotonic() - started) * 1000)
                # only a successful read counts as "connected" (connect() may just return cached device info)
                if not self.connected:
                    log.info("inverter connected")
                    self.connected_at = time.time()
                    self._event("verbunden")
                    self._check_firmware()
                self._watch_daily_reset(snap)
                try:
                    self.outages.observe(snap)
                except Exception as err:  # noqa: BLE001 - statistics must never disturb polling
                    log.warning("could not record power cut: %s", err)
                self.connected = True
                self.disconnected_since = None
                self.last_error = None
                backoff = 5.0
                transient_failures = 0
                if self.release_connection and hasattr(self.driver, "disconnect"):
                    await self.driver.disconnect()
                await self._store(snap)
                # shown only once stored: storing adds the second PV inverter's counter to the totals (#419), a
                # reader in between would see the consumption without it
                self.latest = snap
                self._publish(snap)
                if time.time() - last_prune > 3600:
                    try:
                        await asyncio.to_thread(self.storage.prune, self.retention_days)
                    except Exception as err:  # noqa: BLE001
                        log.error("could not prune old samples: %s", err)
                    last_prune = time.time()
                await asyncio.sleep(max(0.0, self.poll_interval() - (time.monotonic() - started)))
            except asyncio.CancelledError:
                raise
            except Exception as err:  # keep running whatever the inverter does
                raise_if_cancelled()
                try:
                    self.outages.unreachable(time.time())  # OpenAmpere runs, only the inverter is gone (#93)
                except Exception as note_err:  # noqa: BLE001 - statistics must never disturb polling
                    log.warning("could not record power cut: %s", note_err)
                # Single hiccups (timeouts, a Modbus proxy that briefly cannot reach the inverter) keep the
                # connection; only repeated failures count as "disconnected".
                if getattr(err, "transient", False) and self.connected and transient_failures < MAX_TRANSIENT:
                    transient_failures += 1
                    log.info("temporary read error (%d/%d): %s", transient_failures, MAX_TRANSIENT, err)
                    await asyncio.sleep(self.poll_interval())
                    continue
                transient_failures = 0
                if self.connected or self.disconnected_since is None:
                    self.disconnected_since = time.time()
                    self._event("getrennt", str(err) or type(err).__name__)
                message = friendly_error(err)
                if self.connected or self.last_error != message:
                    log.warning("inverter error: %s (retry in %.0fs)", str(err) or type(err).__name__, backoff)
                self.connected = False
                self.last_error = message
                await self.driver.close()
                # never sooner than the device stands (a Senec restarts when asked too often, #521)
                await asyncio.sleep(max(backoff, self.min_gap()))
                backoff = min(backoff * 2, 300)
