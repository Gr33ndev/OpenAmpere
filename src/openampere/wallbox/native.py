# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Wallboxes OpenAmpere reads and controls itself, without evcc (#318): the portable dé wallbox over Tuya, and the
FoxESS AC EV Charger over Modbus TCP, read only (#439).

- Every model is a class in MODELS with the same face: key, label, protocol ("tuya" or "modbus"), the modes it
  offers, read_only, and interpret() that turns what the wallbox answered into a base.Reading. reader() gives what a
  wallbox is read with (tuya.TuyaDevice, or modbus.ModbusWallbox with one short connection per read, closed
  afterwards); both read with status() and fail with an error that has a German message. Only a model that is not
  read_only has set_current() and start(), and only the Tuya connection (device()) can write.
- A read-only model is never written to; its power is shown and counted in the house load like any wallbox. Without
  `simulated` it offers only the mode "manual" (the other modes are refused when saved or chosen).
- A read-only model with `simulated` (the FoxESS charger, #440) offers all modes, but its control is only a simulation,
  whatever the master switch and the test mode say: step() decides as for a wallbox OpenAmpere controls, with the
  charger's own limits and its 1/3-phase switching, and writes what it would have set into the control log, marked as
  a simulation: every planned start and stop, changes of the current at most every SIM_LOG_EVERY_S as one summary
  (the current now and the range since the last entry), so they never bury real changes. The plan (Plan: on or off, current, phases, timers) is kept in memory and is the starting point of the
  next check, not the charger's values; it is taken over from the charger once a car is plugged in and dropped with
  the mode "manual". Letting go of a charge the simulation started is logged as the explicit stop real control would
  send (#318). The plan never counts as load: the charger charges by itself as it is set (consumers.py).

- Several wallboxes are fine (at most MAX_WALLBOXES). They share the place "wallbox" in the order of the solar surplus
  (consumers.py), in the order of their list, next to an evcc wallbox if there is one.
- A portable wallbox is unplugged for days. It is read in the background, so a wallbox that does not answer never
  holds up the surplus control; after a failure it is asked again after 15 s, 30 s, … up to every 5 minutes. It is
  shown as "not reachable since …" without its old values, logged once when it goes and once when it comes back,
  and controlled again as soon as it answers. With its device ID it is searched in the home network when its
  address may have changed.
- Modes: "manual" (only shown, OpenAmpere never writes), "off", "pv" (solar surplus only), "minpv" (minimum current
  plus surplus), "now" (full current). The current is set in the steps the wallbox offers (data point 107), up to the
  owner's limit and, per mode, the current the owner chose for it (#402). Without a car nothing is written: the mode
  applies once one is plugged in. Many wallboxes start charging on their own when a car is plugged in; in "pv" mode
  without surplus such a charge is stopped at once. A solar charge OpenAmpere started goes on with the lowest step
  through clouds, the rest from the battery or the grid, and stops only after the waiting time the owner chose (#402).
- Writing follows the rules of every control: the master switch and the test mode, the limits of the wallbox and of
  the owner, the value read back, every change in the control log. The wallbox has no watchdog: if OpenAmpere stops,
  it keeps charging with the last current. The owner's current is kept in the meta "wallbox_owned" before the first
  write and put back when OpenAmpere lets go (mode "manual", master switch off, wallbox removed or switched off);
  a charge OpenAmpere started is then also stopped, except when the owner chose "manual" (they take over).
- The local key is stored encrypted with secret.key. A backup restored on another server (or a lost secret.key) leaves
  a key that cannot be read (#391): the wallbox stays in the list with its settings, is neither read nor controlled,
  and the app asks for the key again. Saving it with a new key makes it work again under the same id.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import secrets
import time
from collections.abc import Collection
from dataclasses import asdict, dataclass, field, replace

from .. import ha_app, tuya
from ..outbound import in_home_network
from ..probe import host_addresses
from ..runtime import Runtime
from .base import Reading, WallboxUnreachable
from .de_tuya import DeTuyaCharger
from .foxess import FoxEssChargerLive
from .modbus import ModbusWallbox

log = logging.getLogger(__name__)

MODELS = {m.key: m for m in (DeTuyaCharger, FoxEssChargerLive)}
UNREACHABLE = (tuya.TuyaError, WallboxUnreachable)  # both carry a German .message for the owner

MAX_WALLBOXES = 4
MODES = ("manual", "off", "pv", "minpv", "now")
CONTROLLED = ("off", "pv", "minpv", "now")
CHARGING = ("pv", "minpv", "now")  # modes with their own highest current
POLL_S = 10.0
FRESH_S = 35.0  # older values are not used to decide anything
RETRY_S, RETRY_MAX_S = 15.0, 300.0
SEARCH_AFTER_S, SEARCH_EVERY_S = 120.0, 900.0  # look for a new address of a wallbox that went away
VOLTAGE = 230.0
MARGIN_W = 200.0  # surplus needed above the lowest step before charging starts
CONFIRM_TICKS = 2  # surplus must be there this many checks in a row before charging starts (clouds!)
STOP_AFTER_MIN = 10  # default: a solar charge goes on this long without enough surplus before it stops
MIN_ON_S = MIN_OFF_S = 300.0  # the car's contactor should not switch every minute
RAISE_EVERY_S = 30.0  # the current goes up at most this often, down right away
SIM_LOG_EVERY_S = 300.0  # simulation: current changes go into the control log at most this often, collected
PHASE_SWITCH_MIN = 5  # simulation: shortest time between two phase switches when the charger does not tell its own
START_AGAIN_S = 300.0  # a car that does not start (full, or waiting for its own timer) is asked again after this
GIVE_UP_S = 24 * 3600.0  # a removed wallbox that stays unreachable is given up after a day
OWNED = "wallbox_owned"  # meta: id -> {"current_a": owner's current, "started": bool, "wallbox": sealed config}
HOST = re.compile(r"[A-Za-z0-9.-]{1,253}")
DEVICE_ID = re.compile(r"[A-Za-z0-9]{10,40}")


@dataclass
class NativeWallbox:
    name: str
    model: str = "de_tuya"
    host: str = ""
    device_id: str = ""  # optional: lets OpenAmpere find the wallbox again after its address changed
    local_key: str = ""
    max_current_a: int = 16  # the owner's limit, e.g. for the socket it is plugged into
    mode: str = "manual"
    mode_current_a: dict = field(default_factory=dict)  # highest current per charging mode, missing: max_current_a
    stop_after_min: int = STOP_AFTER_MIN  # solar mode: minutes without enough surplus before a charge stops
    enabled: bool = True
    id: str = field(default_factory=lambda: secrets.token_hex(4))
    port: int | None = None  # Modbus models only (None for Tuya): missing means the model's usual port
    unit: int | None = None  # Modbus models only: the unit id


def needs_key(wb: NativeWallbox) -> bool:
    """A Tuya wallbox talks encrypted with its local key; a Modbus one needs none."""
    return MODELS[wb.model].protocol == "tuya"


def key_missing(wb: NativeWallbox) -> bool:
    """Its stored key could not be read (#391): neither read nor controlled until it is entered again."""
    return needs_key(wb) and not wb.local_key


def _connection(wb: NativeWallbox) -> tuple:
    """What a wallbox is reached with: a change means another device, or the same one at another address."""
    return wb.host, wb.local_key, wb.port, wb.unit


@dataclass
class Plan:
    """What the simulated control of a read-only wallbox would have set (#440), kept between the checks instead of the
    charger's own values, which it does not change. Nothing of it is written anywhere but the control log."""
    synced: bool = False  # taken over from the charger (charging or not) once a car is plugged in
    on: bool = False
    started: bool = False  # the simulation started this charge, not the charger by itself
    current_a: int | None = None
    phases: int | None = None
    since: float = float("-inf")  # last planned start or stop
    current_at: float = float("-inf")  # last planned change of the current
    phases_at: float = float("-inf")  # last planned start or switch of the phases
    want_on: int = 0
    short_since: float | None = None
    logged_at: float = float("-inf")  # last entry in the control log: start, stop or the summary of current changes
    pending: dict | None = None  # current changes not logged yet: lowest and highest current, the last reason
    state: str = "unknown"  # for the app: charging, waiting, no_surplus, off, finished, no_car, unknown

    def reading(self, real: Reading) -> Reading:
        """The charger as it would be under the planned control: charging as planned, with the planned current."""
        return replace(real, charging=self.on, set_current_a=self.current_a,
                       active_phases=(self.phases or 0) if self.on else 0)

    def power_w(self) -> float:
        return float(self.current_a * self.phases * VOLTAGE) if self.on and self.current_a and self.phases else 0.0


@dataclass
class Live:
    reading: Reading | None = None
    updated: float = 0.0
    online: bool | None = None  # None: not asked yet
    offline_since: float = 0.0
    error: str | None = None
    failures: int = 0
    retry_at: float = 0.0
    last_search: float = 0.0
    since: float = float("-inf")  # last start or stop
    current_at: float = float("-inf")  # last change of the current
    start_sent: float = float("-inf")
    stop_sent: float = float("-inf")
    planned: dict = field(default_factory=dict)  # test mode: what would have been written, logged once
    plan: Plan = field(default_factory=Plan)  # a simulated control (#440)
    want_on: int = 0
    short_since: float | None = None  # solar charging: first check without enough surplus
    task: asyncio.Task | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def validate(raw: list, keyless: Collection[str] = ()) -> list[NativeWallbox]:
    """keyless: ids of stored wallboxes whose key could not be read; they may stay without one."""
    if not isinstance(raw, list) or len(raw) > MAX_WALLBOXES:
        raise ValueError(f"Höchstens {MAX_WALLBOXES} Wallboxen.")
    wallboxes, ids = [], set()
    for item in raw:
        try:
            wb = NativeWallbox(**{k: v for k, v in item.items() if k in NativeWallbox.__dataclass_fields__})
            wb.name, wb.host, wb.device_id = str(wb.name).strip()[:40], str(wb.host).strip(), str(wb.device_id).strip()
            wb.local_key, wb.max_current_a, wb.enabled = str(wb.local_key or ""), int(wb.max_current_a), bool(wb.enabled)
            wb.id = str(wb.id)
        except (TypeError, ValueError, AttributeError, OverflowError):  # OverflowError: Infinity in the JSON
            raise ValueError("Ungültige Angaben bei einer Wallbox.") from None
        if not wb.name:
            raise ValueError("Bitte jeder Wallbox einen Namen geben.")
        model = MODELS.get(wb.model)
        if model is None:
            raise ValueError(f"{wb.name}: Diese Wallbox kennt OpenAmpere noch nicht.")
        if not HOST.fullmatch(wb.host):
            raise ValueError(f"{wb.name}: Bitte die IP-Adresse der Wallbox im Heimnetz angeben.")
        if model.protocol == "modbus":
            try:  # missing or 0: the model's usual port and unit id
                wb.port, wb.unit = int(wb.port or model.port), int(wb.unit or model.unit)
            except (TypeError, ValueError, OverflowError):
                raise ValueError("Ungültige Angaben bei einer Wallbox.") from None
            if not 1 <= wb.port <= 65535 or not 1 <= wb.unit <= 255:
                raise ValueError(f"{wb.name}: Der Port muss zwischen 1 und 65535 liegen, die Geräteadresse "
                                 "zwischen 1 und 255.")
            wb.local_key, wb.device_id = "", ""  # neither is used: nothing stored that is not needed
        else:
            wb.port = wb.unit = None
        if wb.device_id and not DEVICE_ID.fullmatch(wb.device_id):
            raise ValueError(f"{wb.name}: Die Geräte-ID besteht aus 10 bis 40 Buchstaben und Ziffern.")
        if needs_key(wb) and len(wb.local_key.encode()) != 16 and not (not wb.local_key and wb.id in keyless):
            raise ValueError(f"{wb.name}: Der Local Key muss genau 16 Zeichen lang sein.")
        if not 6 <= wb.max_current_a <= 32:
            raise ValueError(f"{wb.name}: Der höchste Ladestrom muss zwischen 6 und 32 A liegen.")
        if wb.mode not in MODES:
            raise ValueError(f"{wb.name}: Unbekannter Lademodus.")
        if wb.mode not in model.modes:
            raise ValueError(f"{wb.name}: Diese Wallbox zeigt OpenAmpere nur an, steuern kann es sie nicht.")
        try:
            wb.stop_after_min = int(wb.stop_after_min)
            currents = {str(k): int(v) for k, v in dict(wb.mode_current_a or {}).items()}
        except (TypeError, ValueError, OverflowError):
            raise ValueError("Ungültige Angaben bei einer Wallbox.") from None
        if not 1 <= wb.stop_after_min <= 60:
            raise ValueError(f"{wb.name}: Die Wartezeit vor dem Abbrechen muss zwischen 1 und 60 Minuten liegen.")
        if any(k not in CHARGING or not 6 <= v <= 32 for k, v in currents.items()):
            raise ValueError(f"{wb.name}: Der Ladestrom eines Lademodus muss zwischen 6 und 32 A liegen.")
        # above a lowered highest current it is cut, not refused: the stored wallbox must stay valid
        wb.mode_current_a = {} if model.read_only and not model.simulated \
            else {k: min(v, wb.max_current_a) for k, v in currents.items()}
        if not re.fullmatch(r"[0-9a-f]{8}", wb.id) or wb.id in ids:
            wb.id = secrets.token_hex(4)
        ids.add(wb.id)
        wallboxes.append(wb)
    return wallboxes


class NativeWallboxes:
    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime
        self.live: dict[str, Live] = {}
        self._retired: dict[str, NativeWallbox] = {}  # removed or switched off while OpenAmpere controlled them
        self._retired_live: dict[str, Live] = {}
        self._restored = False
        self._unreadable: set[str] = set()  # stored keys that cannot be decrypted: tried and logged once

    # ---- settings ------------------------------------------------------------------

    def _sealed(self, wb: dict) -> dict:
        box = self.runtime.secrets
        key = wb.get("local_key") or ""
        return {**wb, "local_key": key if not key or box.is_encrypted(key) else box.encrypt("wallbox.local_key", key)}

    def _opened(self, wb: dict) -> dict:
        """A key that cannot be read (another secret.key) becomes empty: the wallbox waits for a new one."""
        key = wb.get("local_key") or ""
        if not key or key in self._unreadable:
            return {**wb, "local_key": ""}
        opened = self.runtime.secrets.decrypt("wallbox.local_key", key)
        if not opened:
            self._unreadable.add(key)
            log.warning("wallbox %s: the local key cannot be read, it has to be entered again", wb.get("name"))
        return {**wb, "local_key": opened}

    @property
    def wallboxes(self) -> list[NativeWallbox]:
        """Checked like a change in the app also when read: the list may come from a restored backup."""
        found = []
        for raw in self.runtime.storage.get_meta("wallboxes") or []:
            try:
                found += validate([self._opened(raw)], keyless=(str(raw.get("id")),))
            except (ValueError, AttributeError, TypeError) as err:
                log.warning("stored wallbox ignored: %s", err)
        return found

    def enabled(self) -> list[NativeWallbox]:
        return [wb for wb in self.wallboxes if wb.enabled]

    def _store(self, wallboxes: list[NativeWallbox]) -> None:
        self.runtime.storage.set_meta("wallboxes", [self._sealed(asdict(wb)) for wb in wallboxes])

    def save(self, raw: list) -> list[NativeWallbox]:
        """A local key sent empty keeps the stored one (the app never gets it back); a wallbox whose key could not
        be read may stay without one until it is entered again."""
        stored = {wb.id: wb for wb in self.wallboxes}
        if not isinstance(raw, list):
            raise ValueError("Ungültige Angaben bei einer Wallbox.")
        items = []
        for item in raw:
            if isinstance(item, dict) and not item.get("local_key") and item.get("id") in stored:
                item = {**item, "local_key": stored[item["id"]].local_key}
            items.append(item)
        wallboxes = validate(items, keyless={wb.id for wb in stored.values() if not wb.local_key})
        for wb in wallboxes:
            if not in_home_network(wb.host):
                raise ValueError(f"{wb.name}: Bitte die IP-Adresse der Wallbox im Heimnetz angeben.")
            if wb.port is not None and self.is_inverter(wb.host, wb.port):
                raise ValueError(f"{wb.name}: Das ist die Adresse deines Wechselrichters. Bitte gib die Adresse der "
                                 "Wallbox ein, du findest sie zum Beispiel in der Geräteliste deines Routers.")
        new = {wb.id: wb for wb in wallboxes}
        for old in stored.values():
            now = new.get(old.id)
            if old.id in self._owned() and key_missing(old):
                # without its key it cannot be reached: with a new key it goes on under its id, or is let go of
                if now is None or not now.enabled:
                    self._retired[old.id] = now or old
                else:
                    self._retired.pop(old.id, None)
            elif now is None or not now.enabled or _connection(now) != _connection(old):
                if old.id in self._owned():
                    self._retired[old.id] = old  # let go of it with the address it was controlled at
            if now is None or not now.enabled or _connection(now) != _connection(old):
                if MODELS[old.model].simulated and old.id in self.live:  # its simulation ends here (#440)
                    self._sim_release(old, self.live[old.id], "Wallbox entfernt, ausgeschaltet oder geändert")
            elif now.mode == "manual" != old.mode and MODELS[old.model].simulated and old.id in self.live:
                # "manual" saved with the list (a restored backup, another client): ended here as in set_mode(), since
                # with the master switch off step() only runs in the simulated modes
                self._sim_release(now, self.live[old.id], "Lademodus „von Hand“")
            if now is None or _connection(now) != _connection(old):
                self.live.pop(old.id, None)
        self._store(wallboxes)
        return wallboxes

    def is_inverter(self, host: str, port: int) -> bool:
        """A Modbus wallbox at the inverter's address and port: most likely the wrong address, and reading it would
        open another connection to the inverter, which allows only a few (#411). Another port of the same address
        can be another device, e.g. behind a Modbus gateway (#417)."""
        inverter = self.runtime.config.inverter
        if not inverter.host or port != inverter.port:
            return False
        return bool(host_addresses(host.strip()) & host_addresses(inverter.host))

    def set_mode(self, wallbox_id: str, mode: str, source: str | None = None, current_a=None) -> NativeWallbox:
        """current_a: the highest current for this mode, chosen together with it (#402)."""
        if mode not in MODES:
            raise ValueError("Unbekannter Lademodus.")
        wallboxes = self.wallboxes
        wb = next((w for w in wallboxes if w.id == wallbox_id), None)
        if wb is None:
            raise ValueError("Diese Wallbox gibt es nicht.")
        if mode not in MODELS[wb.model].modes:
            raise ValueError("Diese Wallbox zeigt OpenAmpere nur an, steuern kann es sie nicht.")
        if current_a is not None:
            try:
                current = int(current_a)
            except (TypeError, ValueError, OverflowError):  # e.g. a text or Infinity in the JSON
                raise ValueError("Ungültiger Ladestrom.") from None
            if mode not in CHARGING:
                raise ValueError("In diesem Lademodus lässt sich kein Ladestrom wählen.")
            if not 6 <= current <= wb.max_current_a:
                raise ValueError(f"Der Ladestrom muss zwischen 6 und {wb.max_current_a} A liegen.")
        old, wb.mode = wb.mode, mode
        before = wb.mode_current_a.get(mode, wb.max_current_a)
        if current_a is not None:
            wb.mode_current_a = {**wb.mode_current_a, mode: current}
        self._store(wallboxes)
        live = self.live.setdefault(wb.id, Live())
        live.want_on, live.short_since = 0, None
        live.plan.want_on, live.plan.short_since = 0, None
        if old != mode:
            self._log(wb, "mode", mode, False, "ok", source=source, before=old)
            if mode == "manual" and MODELS[wb.model].simulated:
                # ended here, not in step(): with the master switch off step() only runs in the simulated modes
                self._sim_release(wb, live, "Lademodus „von Hand“")
        if current_a is not None and current != before:
            self._log(wb, "mode_current", {"mode": mode, "current_a": current}, False, "ok", source=source,
                      before=before)
        return wb

    # ---- reading, in the background --------------------------------------------------

    def device(self, wb: NativeWallbox) -> tuya.TuyaDevice:
        """The Tuya connection, the only one that can write (to a model that is not read only)."""
        return tuya.TuyaDevice(wb.host, wb.local_key)

    def reader(self, wb: NativeWallbox) -> tuya.TuyaDevice | ModbusWallbox:
        """What a wallbox is read with: status() of either. A Modbus wallbox gets a connection that cannot write."""
        model = MODELS[wb.model]
        if model.protocol == "modbus":
            return ModbusWallbox(wb.host, wb.port or model.port, wb.unit or model.unit, model.ranges, model.optional)
        return self.device(wb)

    @contextlib.asynccontextmanager
    async def exclusive(self, host: str, port: int):
        """Holds the poll of every Modbus wallbox set up at host:port while another reader (the wallbox check, the
        test in the setup) uses it: the charger may allow only one connection, so both never connect at the same
        time. A poll that is running is waited for; the next one waits until the other reader is done."""
        wanted = await asyncio.to_thread(host_addresses, host.strip())
        locks = []
        for wb in self.wallboxes:
            model = MODELS[wb.model]
            if model.protocol != "modbus" or (wb.port or model.port) != port:
                continue
            if await asyncio.to_thread(host_addresses, wb.host) & wanted:
                locks.append((wb.id, self.live.setdefault(wb.id, Live()).lock))
        async with contextlib.AsyncExitStack() as stack:
            for _id, lock in sorted(locks, key=lambda item: item[0]):  # always the same order: no deadlock
                await stack.enter_async_context(lock)
            yield

    async def test(self, model_key: str, host: str, port: int | None, unit: int | None) -> Reading:
        """Reads a Modbus wallbox once with the details from the setup form (the Tuya one is read in api.py)."""
        model = MODELS.get(model_key)
        if model is None or model.protocol != "modbus":
            raise WallboxUnreachable("Diese Wallbox kennt OpenAmpere noch nicht.")
        host, port, unit = host.strip(), port or model.port, unit or model.unit
        if not HOST.fullmatch(host) or not in_home_network(host):
            raise WallboxUnreachable("Bitte die IP-Adresse der Wallbox im Heimnetz angeben.")
        if self.is_inverter(host, port):
            raise WallboxUnreachable("Das ist die Adresse deines Wechselrichters. Bitte gib die Adresse der Wallbox "
                                     "ein, du findest sie zum Beispiel in der Geräteliste deines Routers.")
        # a wallbox set up at this address is not read twice at the same time: it may allow only one connection
        async with self.exclusive(host, port):
            values = await ModbusWallbox(host, port, unit, model.ranges, model.optional).status()
        return model.interpret(values)

    def refresh(self, now: float | None = None) -> None:
        """Starts a read of every wallbox that is due; never waits for one (a wallbox may be unplugged)."""
        now = time.time() if now is None else now
        for wb in self.enabled():
            if key_missing(wb):
                continue  # its key has to be entered again
            live = self.live.setdefault(wb.id, Live())
            if live.task and not live.task.done():
                continue
            if now < live.retry_at or now - live.updated < POLL_S - 1:
                continue
            live.task = asyncio.create_task(self.read(wb, live))

    async def wait(self) -> None:
        """For tests and the shutdown: the reads that are running."""
        tasks = [live.task for live in self.live.values() if live.task and not live.task.done()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def read(self, wb: NativeWallbox, live: Live | None = None) -> Live:
        live = live or self.live.setdefault(wb.id, Live())
        try:
            async with live.lock:
                dps = await self.reader(wb).status()
        except UNREACHABLE as err:
            self._failed(wb, live, err, time.time())
            if wb.device_id:
                await self._search(wb, live, time.time())
            return live
        now = time.time()
        if live.online is False:
            log.info("wallbox %s answers again after %.0f minutes", wb.name, (now - live.offline_since) / 60)
            # plugged in again, maybe after a power cut: start, stop and test-mode notes count afresh
            live.start_sent = live.stop_sent = float("-inf")
            live.planned.clear()
        live.reading, live.updated = MODELS[wb.model].interpret(dps), now
        live.online, live.error, live.failures, live.retry_at = True, None, 0, 0.0
        return live

    def _failed(self, wb: NativeWallbox, live: Live, err: tuya.TuyaError | WallboxUnreachable, now: float) -> None:
        live.failures += 1
        live.retry_at = now + min(RETRY_MAX_S, RETRY_S * 2 ** (live.failures - 1))
        live.error = err.message
        if live.online is not False:
            live.online, live.offline_since = False, now
            log.info("wallbox %s not reachable: %s", wb.name, err.message)

    async def _search(self, wb: NativeWallbox, live: Live, now: float) -> None:
        """A wallbox that has been away for a while may have a new address (the router gives it one)."""
        if ha_app.active() or now - live.offline_since < SEARCH_AFTER_S or now - live.last_search < SEARCH_EVERY_S:
            return  # in the Home Assistant app the announcements do not reach OpenAmpere
        live.last_search = now
        try:
            found = await tuya.search()
        except OSError:
            return
        match = next((d for d in found if d["device_id"] == wb.device_id), None)
        if match and match["host"] != wb.host and HOST.fullmatch(match["host"]) and in_home_network(match["host"]):
            wallboxes = self.wallboxes
            for w in wallboxes:
                if w.id == wb.id:
                    w.host = match["host"]
            self._store(wallboxes)
            live.retry_at = 0.0
            log.info("wallbox %s found at a new address", wb.name)

    @staticmethod
    def _fresh(live: Live | None, now: float) -> Reading | None:
        if live and live.online and live.reading and now - live.updated < FRESH_S:
            return live.reading
        return None

    def fresh(self, wb: NativeWallbox, now: float | None = None) -> Reading | None:
        return self._fresh(self.live.get(wb.id), time.time() if now is None else now)

    def power(self, wb: NativeWallbox, now: float | None = None) -> float:
        reading = self.fresh(wb, now)
        return float(reading.power_w) if reading else 0.0

    @staticmethod
    def controls(wb: NativeWallbox) -> bool:
        """OpenAmpere really controls it: its charging is surplus it may keep."""
        return wb.mode in CONTROLLED and not MODELS[wb.model].read_only

    @staticmethod
    def simulates(wb: NativeWallbox) -> bool:
        """Its control is only simulated (#440): nothing is written, its charging is not OpenAmpere's."""
        return wb.mode in CONTROLLED and MODELS[wb.model].simulated

    # ---- control ---------------------------------------------------------------------

    def _owned(self) -> dict:
        return self.runtime.storage.get_meta(OWNED) or {}

    def _own(self, wb: NativeWallbox, reading: Reading, **changes) -> None:
        """Before OpenAmpere writes for the first time: the owner's current, to put it back later. The wallbox is kept
        as it is written to now, e.g. with a key entered again after a restore."""
        owned = self._owned()
        entry = owned.get(wb.id) or {"current_a": reading.set_current_a, "started": False}
        owned[wb.id] = {**entry, "wallbox": self._sealed(asdict(wb)), **changes}
        try:
            self.runtime.storage.set_meta(OWNED, owned)
        except Exception as err:  # noqa: BLE001 - e.g. a full disk: control goes on
            log.warning("could not remember the wallbox %s: %s", wb.name, err)

    def _note_stop(self, wb: NativeWallbox, stopped: bool) -> None:
        """A stop by OpenAmpere reached the wallbox (True), or the car charged again or was unplugged since (False):
        only without such a stop is a complete wallbox a full car (#454)."""
        owned = self._owned()
        entry = owned.get(wb.id)
        if not entry or bool(entry.get("stopped")) == stopped:
            return
        owned[wb.id] = {**entry, "stopped": stopped}
        try:
            self.runtime.storage.set_meta(OWNED, owned)
        except Exception as err:  # noqa: BLE001 - e.g. a full disk: control goes on
            log.warning("could not remember the wallbox %s: %s", wb.name, err)

    def _disown(self, wallbox_id: str) -> None:
        owned = self._owned()
        if owned.pop(wallbox_id, None) is not None:
            self.runtime.storage.set_meta(OWNED, owned)

    def _log(self, wb: NativeWallbox, action: str, value, dry: bool, result: str, *, source: str | None = None,
             before=None) -> None:
        # the id: a renamed wallbox is still the same one (the charge notifications look for its stops, #451)
        details = {"from": {"loadpoint": wb.name, "id": wb.id, **({"value": before} if before is not None else {})},
                   "to": {"action": action, "value": value}, **({"source": source} if source else {})}
        try:
            self.runtime.storage.log_control("wallbox", details, dry, result)
        except Exception as err:  # noqa: BLE001 - control must go on
            log.warning("control log not written (%s): %s", result, err)

    async def _write(self, wb: NativeWallbox, live: Live, values: dict, action: str, value, reason: str,
                     *, safe: bool = False) -> bool:
        """Writes data points and reads them back. safe: letting go or stopping what OpenAmpere started, sent also
        in test mode."""
        if MODELS[wb.model].read_only:  # never reached (a simulation only logs, #440): a second guard
            raise RuntimeError(f"wallbox {wb.model} is read only")
        dry = self.runtime.config.control.dry_run
        if dry and not safe:
            self._log(wb, action, value, True, f"nicht gesendet (Testmodus): {reason}")
            return False
        try:
            async with live.lock:
                back = await self.device(wb).set(values, read_back=True)
        except tuya.TuyaError as err:
            self._failed(wb, live, err, time.time())
            self._log(wb, action, value, False, f"Fehler: {err.message}")
            return False
        live.reading, live.updated = MODELS[wb.model].interpret(back), time.time()
        # start and stop are not reported back, the state shows them with the next read
        differs = {k: back[k] for k in values if k in back and back[k] != values[k]}
        if dry:
            reason = f"trotz Testmodus gesendet, OpenAmpere gibt die Wallbox ab: {reason}"
        self._log(wb, action, value, False, f"Rücklesen abweichend: {differs}" if differs else reason)
        return not differs

    async def _set_current(self, wb: NativeWallbox, live: Live, reading: Reading, amps: int, reason: str,
                           now: float) -> None:
        dry = self.runtime.config.control.dry_run
        if reading.set_current_a == amps or (dry and live.planned.get("current") == amps):
            return
        if reading.set_current_a is not None and amps > reading.set_current_a and now - live.current_at < RAISE_EVERY_S:
            return
        if dry:
            live.planned["current"] = amps
        else:
            self._own(wb, reading)
            live.planned.clear()
        live.current_at = now
        await self._write(wb, live, MODELS[wb.model].set_current(amps), "current", amps, reason)

    async def _start(self, wb: NativeWallbox, live: Live, reading: Reading, on: bool, reason: str, now: float) -> None:
        dry = self.runtime.config.control.dry_run
        owned = self._owned().get(wb.id, {})
        started = bool(owned.get("started"))
        # the dé wallbox may report STOP ("complete") also after a stop by OpenAmpere (#454): only without such a stop
        # is a complete wallbox a full car; otherwise it is asked to start again, until a start reached it
        full = reading.state == "finished" and not owned.get("stopped")
        if dry and not (not on and started):
            if live.planned.get("start") == on:
                return  # logged once
            live.planned["start"] = on
        elif on and (full or now - live.start_sent < START_AGAIN_S):
            return  # a full car, or one that waits for its own timer: asked again later
        elif not on and now - live.stop_sent < RAISE_EVERY_S:
            return
        else:
            if not dry:
                self._own(wb, reading, started=on)
            if on:
                live.start_sent = now
            else:
                live.stop_sent = now
        live.since, live.want_on, live.short_since = now, 0, None
        sent = await self._write(wb, live, MODELS[wb.model].start(on), "start" if on else "stop", on, reason,
                                 safe=not on and started)
        if sent:  # also a stop sent in test mode (safe); a failed start keeps the note: the wallbox is asked again
            self._note_stop(wb, not on)

    async def step(self, wb: NativeWallbox, available: float, now: float | None = None) -> float:
        """One check of a wallbox; available: surplus for it, its own charging included.
        Returns the power it is meant to charge with (for a simulated control: the planned power, #440)."""
        now = time.time() if now is None else now
        if key_missing(wb):
            return 0.0  # its key has to be entered again: neither read nor controlled
        live = self.live.setdefault(wb.id, Live())
        reading = self._fresh(live, now)
        model = MODELS[wb.model]
        if model.read_only and not model.simulated:
            return reading.power_w if reading else 0.0  # only shown: nothing to let go of, nothing to decide
        if wb.mode == "manual":
            if model.simulated:
                self._sim_release(wb, live, "Lademodus „von Hand“", now)
            else:
                await self.release(wb, "Lademodus „von Hand“", stop_started=False)
            return reading.power_w if reading else 0.0
        if reading is None:
            if model.simulated:
                live.plan.state = "unknown"  # the plan stays: it goes on once the charger answers again
            return 0.0  # not reachable: nothing to decide, the wallbox keeps what it does
        if not reading.connected:
            if model.simulated:
                self._sim_flush(wb, live.plan, now, force=True)
                live.plan = Plan(state="no_car")  # unplugged: a planned charge ends by itself, nothing to stop
                return 0.0
            live.want_on, live.short_since = 0, None
            live.start_sent = float("-inf")
            live.planned.pop("start", None)
            self._note_stop(wb, False)
            return 0.0
        if reading.charging and not model.simulated:
            self._note_stop(wb, False)
        plan = live.plan if model.simulated else None
        if plan is not None:
            if not plan.synced:  # a car plugged in, or a simulated mode chosen: from what the charger does now
                plan.synced, plan.on, plan.started = True, reading.charging, False
                plan.current_a, plan.phases = None, (reading.active_phases or None) if reading.charging else None
            if plan.on and reading.state == "finished":
                self._sim_flush(wb, plan, now, force=True)
                plan.on, plan.started = False, False  # the car is full: the charger stops by itself
            self._sim_flush(wb, plan, now)  # changes collected earlier, once SIM_LOG_EVERY_S is over
            reading = plan.reading(reading)
        ctl = plan if plan is not None else live  # the timers of the decision: the plan's in a simulation
        phases = self._sim_phases(wb, plan, reading, available, now) if plan is not None else reading.phases()
        limit = min(wb.max_current_a, reading.max_current_a or wb.max_current_a,
                    wb.mode_current_a.get(wb.mode, wb.max_current_a))
        steps = [s for s in reading.steps_a if s <= limit] or [min(reading.steps_a)]
        if plan is not None and phases < reading.installed_phases:
            # one phase of a three-phase charger: only up to just below its lowest power on three phases, above that
            # it would switch to three phases
            three = min(reading.steps_a) * VOLTAGE * reading.installed_phases
            steps = [s for s in steps if s * VOLTAGE * phases < three] or steps[:1]
        lowest = steps[0] * VOLTAGE * phases
        fits = [s for s in steps if s * VOLTAGE * phases <= available - (0 if reading.charging else MARGIN_W)]

        async def start(on: bool, why: str) -> None:
            if plan is not None:
                self._sim_start(wb, plan, reading, on, phases, why, now)
            else:
                await self._start(wb, live, reading, on, why, now)

        if wb.mode == "off":
            if reading.charging:
                await start(False, "Lademodus „Aus“")
            if plan is not None:
                plan.state = "off"
            return 0.0
        target = fits[-1] if fits else steps[0]  # without enough surplus: the lowest step
        if wb.mode == "now":
            target, on, reason = steps[-1], True, "Lademodus „Schnell“"
        elif wb.mode == "minpv":
            on, reason = True, f"Überschuss {available:.0f} W, mindestens {lowest:.0f} W"
        elif reading.charging:
            on, reason = True, f"Überschuss {available:.0f} W"
            if fits:
                ctl.short_since = None
            else:
                ctl.short_since = now if ctl.short_since is None else ctl.short_since
                # a charge the wallbox started by itself (many start as soon as a car is plugged in) is stopped at
                # once: the waiting and minimum time protect only a charge OpenAmpere started for the sun; that one
                # goes on with the lowest step through clouds for the owner's waiting time (#402)
                started = plan.started if plan is not None else bool(self._owned().get(wb.id, {}).get("started"))
                waited = now - ctl.short_since >= wb.stop_after_min * 60 and now - ctl.since >= MIN_ON_S
                if not started or waited:
                    on, reason = False, f"zu wenig Überschuss ({available:.0f} W)"
        else:
            on, reason = False, f"Überschuss {available:.0f} W"
            if fits:
                ctl.want_on += 1
                on = ctl.want_on >= CONFIRM_TICKS and now - ctl.since >= MIN_OFF_S
            else:
                ctl.want_on = 0
        if not on:
            if reading.charging:
                await start(False, reason)
            if plan is not None:
                plan.state = "finished" if reading.state == "finished" else "waiting" if plan.want_on else "no_surplus"
            # a car about to start keeps its surplus, so a wallbox after it does not start at the same time
            return target * VOLTAGE * phases if ctl.want_on else 0.0
        if plan is not None:
            self._sim_current(wb, plan, target, phases, reason, now)
        else:
            await self._set_current(wb, live, reading, target, reason, now)
        if not reading.charging:
            await start(True, reason)
        if plan is not None:
            plan.state = "charging" if plan.on else "finished" if reading.state == "finished" else "waiting"
            return plan.power_w()
        return target * VOLTAGE * phases

    # ---- simulated control (#440): decided like a real one, only logged ---------------------------------------------

    @staticmethod
    def _sim_phases(wb: NativeWallbox, plan: Plan, reading: Reading, available: float, now: float) -> int:
        """The phases a simulated charge would use. A three-phase charger with automatic 1/3-phase switching follows
        its power setpoint (docs/devices/foxess/ac-charger.md): one phase below its lowest power on three phases,
        three from there, switched at most once per its own switch interval (0x300B)."""
        installed = reading.installed_phases
        if installed < 3 or not reading.phase_switching:
            return installed
        three = min(reading.steps_a) * VOLTAGE * installed
        enough = wb.mode == "now" or available - (0 if plan.on else MARGIN_W) >= three
        wanted = installed if enough else 1
        if not plan.on or not plan.phases:
            return wanted  # chosen with the start
        interval = (reading.phase_switch_min or PHASE_SWITCH_MIN) * 60
        if wanted != plan.phases and now - plan.phases_at >= interval:
            return wanted
        return plan.phases

    def _sim_log(self, wb: NativeWallbox, action: str, value, reason: str, before=None) -> None:
        """A simulated write: in the control log as not sent and marked as a simulation; never written."""
        details = {"from": {"loadpoint": wb.name, **({"value": before} if before is not None else {})},
                   "to": {"action": action, "value": value}, "simulation": True}
        try:
            self.runtime.storage.log_control("wallbox", details, True, f"Simulation, nicht gesendet: {reason}")
        except Exception as err:  # noqa: BLE001 - the simulation must go on
            log.warning("control log not written (simulation): %s", err)

    def _sim_current(self, wb: NativeWallbox, plan: Plan, amps: int, phases: int, reason: str, now: float) -> None:
        """Like _set_current(): up at most every RAISE_EVERY_S, down right away. While the plan charges a change is
        collected and logged at most every SIM_LOG_EVERY_S (_sim_flush); a planned start says the current it starts
        with."""
        if plan.current_a == amps and plan.phases == phases:
            return
        if plan.on and plan.current_a is not None and plan.phases and amps * phases > plan.current_a * plan.phases \
                and now - plan.current_at < RAISE_EVERY_S:
            return
        before = plan.current_a
        if plan.on and plan.phases and phases != plan.phases:
            plan.phases_at = now
        plan.current_a, plan.phases, plan.current_at = amps, phases, now
        if plan.on:
            seen = [a for a in (before, amps) if a is not None]
            if plan.pending:
                seen += [plan.pending["low_a"], plan.pending["high_a"]]
            plan.pending = {"low_a": min(seen), "high_a": max(seen), "reason": reason}
            self._sim_flush(wb, plan, now)

    def _sim_flush(self, wb: NativeWallbox, plan: Plan, now: float, *, force: bool = False) -> None:
        """The collected changes of the current as one entry: the current now and the range since the last entry.
        At most every SIM_LOG_EVERY_S, so that a simulation (which runs even with the master switch off) does not
        bury real changes in the control log; force: before a planned stop or the end of the plan, so that no change
        is lost. Nothing when nothing changed."""
        pending = plan.pending
        if not pending or not (force or now - plan.logged_at >= SIM_LOG_EVERY_S):
            return
        value = {"current_a": plan.current_a, "phases": plan.phases, "low_a": pending["low_a"],
                 "high_a": pending["high_a"]}
        if plan.logged_at != float("-inf"):  # since the last entry of this plan
            value["minutes"] = max(1, round((now - plan.logged_at) / 60))
        self._sim_log(wb, "current", value, pending["reason"])
        plan.pending, plan.logged_at = None, now

    def _sim_start(self, wb: NativeWallbox, plan: Plan, reading: Reading, on: bool, phases: int, reason: str,
                   now: float) -> None:
        """Like _start(): a planned start or stop, always logged (once); a stop after the changes collected so far."""
        if plan.on == on or (on and reading.state == "finished"):
            return  # a full car does not start
        if not on:
            self._sim_flush(wb, plan, now, force=True)
        plan.on, plan.started = on, on
        plan.since, plan.want_on, plan.short_since, plan.logged_at = now, 0, None, now
        if on:
            plan.phases, plan.phases_at = phases, now
            self._sim_log(wb, "start", {"current_a": plan.current_a, "phases": phases}, reason)
        else:
            self._sim_log(wb, "stop", False, reason)

    def _sim_release(self, wb: NativeWallbox, live: Live, reason: str, now: float | None = None) -> None:
        """The simulation ends: a charge it started would be stopped explicitly, as real control would do when it lets
        go (#318); a charge the charger started by itself would be left alone. The next simulation starts afresh."""
        plan = live.plan
        self._sim_flush(wb, plan, time.time() if now is None else now, force=True)
        if plan.on and plan.started:
            self._sim_log(wb, "stop", False, f"{reason}, OpenAmpere gäbe die Wallbox ab")
        live.plan = Plan()

    async def release(self, wb: NativeWallbox, reason: str, *, stop_started: bool = True,
                      live: Live | None = None) -> bool:
        """Lets go of a wallbox: the owner's current back, and a charge OpenAmpere started stopped (stop_started).
        Sent also with the master switch off and in test mode, it is the safe direction. True when done."""
        entry = self._owned().get(wb.id)
        if not entry:
            return True
        if MODELS[wb.model].read_only:  # never written, so never owned: nothing to put back
            self._disown(wb.id)
            return True
        live = live or self.live.setdefault(wb.id, Live())
        reading = self._fresh(live, time.time())
        if reading is None:
            return False  # not reachable: again when it answers
        model = MODELS[wb.model]
        values: dict = {}
        if stop_started and entry.get("started") and reading.charging:
            values.update(model.start(False))
        owner = entry.get("current_a")
        if isinstance(owner, int) and owner != reading.set_current_a:
            values.update(model.set_current(owner))
        if values and not await self._write(wb, live, values, "release", owner, reason, safe=True) \
                and live.online is False:
            return False
        self._disown(wb.id)
        live.want_on, live.short_since = 0, None
        live.planned.clear()
        return True

    async def release_all(self, reason: str) -> None:
        await asyncio.gather(*(self.release(wb, reason) for wb in self.wallboxes))

    async def retire(self, now: float | None = None) -> None:
        """Removed, switched off or changed wallboxes OpenAmpere controlled: let go of them once they answer, with
        the address and key they were controlled with; given up after a day without an answer."""
        now = time.time() if now is None else now
        owned = self._owned()
        for wb_id, wb in list(self._retired.items()):
            live = self._retired_live.setdefault(wb_id, Live(offline_since=now))
            if wb_id not in owned:
                self._retired.pop(wb_id, None)
                self._retired_live.pop(wb_id, None)
                continue
            if self._fresh(live, now) is None and now >= live.retry_at and not key_missing(wb):
                await self.read(wb, live)
            if self._fresh(live, now) is not None:
                done = await self.release(wb, "Wallbox entfernt, ausgeschaltet oder geändert", live=live)
            elif live.offline_since and now - live.offline_since > GIVE_UP_S:
                self._log(wb, "release", None, False, "seit einem Tag nicht erreichbar, OpenAmpere gibt sie ab")
                self._disown(wb_id)
                done = True
            else:
                done = False
            if done:
                self._retired.pop(wb_id, None)
                self._retired_live.pop(wb_id, None)

    def restore(self) -> None:
        """After a restart: wallboxes OpenAmpere controlled that are no longer set up (or switched off) are let go
        of, too."""
        if self._restored:
            return
        self._restored = True
        current = {wb.id for wb in self.enabled()}
        for wb_id, entry in self._owned().items():
            if wb_id in current or wb_id in self._retired or not isinstance(entry, dict):
                continue
            try:
                self._retired[wb_id] = NativeWallbox(**self._opened(entry.get("wallbox") or {}))
            except TypeError:
                pass

    # ---- view --------------------------------------------------------------------

    def view(self, now: float | None = None, *, hide_device_id: bool = False) -> dict:
        """Never the local key; without a login not the device ID either."""
        now = time.time() if now is None else now
        items = []
        for wb in self.wallboxes:
            live = self.live.get(wb.id) or Live()
            reading = self.fresh(wb, now)
            items.append({
                **{k: v for k, v in asdict(wb).items() if k != "local_key"}, "local_key_set": not key_missing(wb),
                **({"device_id": None} if hide_device_id else {}),
                "label": MODELS[wb.model].label, "read_only": MODELS[wb.model].read_only,
                "modes": list(MODELS[wb.model].modes),
                "online": live.online, "offline_since": live.offline_since if live.online is False else None,
                "updated": live.updated or None, "error": live.error,
                "reading": None if reading is None else {k: v for k, v in asdict(reading).items()
                                                         if k != "lifetime_kwh"},
                "controlled": wb.id in self._owned(),
                "simulated": MODELS[wb.model].simulated,
                "simulation": self._plan_view(live.plan) if self.simulates(wb) else None,
            })
        return {"wallboxes": items, "models": [model_info(m) for m in MODELS.values()],
                "max": MAX_WALLBOXES, "search": not ha_app.active()}

    def report(self) -> list[dict]:
        """For the diagnostics report: no address, device ID, key or energy counter."""
        out = []
        for wb in self.wallboxes:
            live = self.live.get(wb.id) or Live()
            reading = live.reading
            out.append({"model": wb.model, "mode": wb.mode, "enabled": wb.enabled, "local_key_set": not key_missing(wb),
                        "online": live.online,
                        "offline_min": round((time.time() - live.offline_since) / 60) if live.online is False else None,
                        "failures": live.failures, "with_device_id": bool(wb.device_id),
                        **({"port": wb.port, "unit": wb.unit} if wb.port is not None else {}),
                        "reading": None if reading is None else {
                            k: v for k, v in asdict(reading).items() if k not in ("lifetime_kwh", "session_kwh")},
                        **({"simulation": self._plan_view(live.plan)} if self.simulates(wb) else {})})
        return out

    @staticmethod
    def _plan_view(plan: Plan) -> dict:
        """What a simulated control would do now (#440), for the app and the report."""
        return {"state": plan.state, "current_a": plan.current_a if plan.on else None,
                "phases": plan.phases if plan.on else None, "power_w": round(plan.power_w())}


def model_info(model) -> dict:
    """A model for the setup in the app: what it needs (a local key, or port and unit id) and what it offers."""
    return {"key": model.key, "label": model.label, "protocol": model.protocol, "read_only": model.read_only,
            "simulated": model.simulated, "modes": list(model.modes), "sold_by": list(getattr(model, "sold_by", ())),
            **({"port": model.port, "unit": model.unit} if model.protocol == "modbus" else {})}


def report_markdown(wallbox: dict) -> list[str]:
    """One wallbox in the diagnostics report as text for an issue (German, like the rest of the report)."""
    reachable = {True: "ja", False: f"nein, seit {wallbox.get('offline_min')} Minuten", None: "noch nicht gefragt"}
    lines = [f"- Modell: {wallbox['model']}, Lademodus: {wallbox['mode']}, eingeschaltet: "
             f"{'ja' if wallbox['enabled'] else 'nein'}, Geräte-ID eingetragen: "
             f"{'ja' if wallbox['with_device_id'] else 'nein'}"
             + (f", Port {wallbox['port']}, Geräteadresse {wallbox['unit']}" if wallbox.get("port") else "")
             + ("" if wallbox.get("local_key_set", True) else ", Local Key: nicht lesbar, neu eintragen"),
             f"- Erreichbar: {reachable.get(wallbox['online'], wallbox['online'])}, Fehlversuche: {wallbox['failures']}"]
    reading = wallbox.get("reading")
    if reading:
        lines.append("- Zuletzt gelesen: " + ", ".join(f"{k} {v}" for k, v in reading.items() if v is not None))
    plan = wallbox.get("simulation")
    if plan:
        lines.append("- Simulation (nichts geschrieben): " + ", ".join(f"{k} {v}" for k, v in plan.items()
                                                                      if v is not None))
    return lines
