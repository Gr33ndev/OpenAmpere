"""Polls the inverter, keeps the latest snapshot, stores it and notifies subscribers."""

from __future__ import annotations

import asyncio
import logging
import time

from .drivers.base import DeviceInfo, InverterDriver, Snapshot, raise_if_cancelled
from .storage import Storage

log = logging.getLogger(__name__)

MAX_TRANSIENT = 3  # consecutive temporary errors tolerated before the inverter counts as disconnected


class Collector:
    def __init__(self, driver: InverterDriver | None, storage: Storage, interval: float, retention_days: int,
                 *, release_connection: bool = False) -> None:
        self.driver = driver
        self.release_connection = release_connection  # close the TCP connection after every reading
        self.storage = storage
        self.interval = interval
        self.retention_days = retention_days
        self.latest: Snapshot | None = None
        self.device: DeviceInfo | None = None
        self.connected = False
        self.last_error: str | None = None
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
        self.last_error = None
        self.latest = None
        self.start()

    @property
    def stale(self) -> bool:
        """True when the last reading is too old to be shown as live."""
        if self.latest is None:
            return True
        return not self.connected or time.time() - self.latest.timestamp > max(3 * self.interval, 30)

    async def _store(self, snap: Snapshot) -> None:
        """Storage problems (e.g. disk full) must not tear down the Modbus connection."""
        try:
            await asyncio.to_thread(self.storage.add_snapshot, snap)
        except Exception as err:  # noqa: BLE001
            log.error("could not store reading: %s", err)

    async def _run(self) -> None:
        assert self.driver is not None
        backoff = 5.0
        last_prune = 0.0
        transient_failures = 0
        while True:
            try:
                if not self.connected:
                    self.device = await self.driver.connect()
                started = time.monotonic()
                snap = await self.driver.read()
                # only a successful read counts as "connected" (connect() may just return cached device info)
                if not self.connected:
                    log.info("inverter connected")
                self.connected = True
                self.last_error = None
                backoff = 5.0
                transient_failures = 0
                self.latest = snap
                if self.release_connection and hasattr(self.driver, "disconnect"):
                    await self.driver.disconnect()
                await self._store(snap)
                self._publish(snap)
                if time.time() - last_prune > 3600:
                    try:
                        await asyncio.to_thread(self.storage.prune, self.retention_days)
                    except Exception as err:  # noqa: BLE001
                        log.error("could not prune old samples: %s", err)
                    last_prune = time.time()
                await asyncio.sleep(max(0.0, self.interval - (time.monotonic() - started)))
            except asyncio.CancelledError:
                raise
            except Exception as err:  # keep running whatever the inverter does
                raise_if_cancelled()
                # Single hiccups (timeouts, a Modbus proxy that briefly cannot reach the inverter) keep the
                # connection; only repeated failures count as "disconnected".
                if getattr(err, "transient", False) and self.connected and transient_failures < MAX_TRANSIENT:
                    transient_failures += 1
                    log.info("temporary read error (%d/%d): %s", transient_failures, MAX_TRANSIENT, err)
                    await asyncio.sleep(self.interval)
                    continue
                transient_failures = 0
                message = str(err) or type(err).__name__
                if self.connected or self.last_error != message:
                    log.warning("inverter error: %s (retry in %.0fs)", message, backoff)
                self.connected = False
                self.last_error = message
                await self.driver.close()
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 300)
