# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Surplus consumers: heating rods, heat pumps (SG-Ready contact) and other loads that run on solar surplus.

Two kinds of devices:
- adjustable: my-PV heating rods (AC ELWA-E, AC ELWA 2, AC THOR) follow the surplus watt by watt,
- switched: Shelly relays (Gen1 and Gen2+) or two plain HTTP URLs, on or off with minimum on/off times.

The surplus is shared out in the order of the list. A wallbox controlled by evcc can come first (it gets
room to start charging) or last (it gets what is left). Optionally a device also runs on cheap grid power
(dynamic tariff below a price limit). Respects the control switch and the test mode; switching is logged.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime

from .drivers.mypv import MyPvHeatingRod
from .runtime import Runtime

log = logging.getLogger(__name__)

MARGIN_W = 200  # switched devices: surplus needed on top of their power before switching on
ADJUST_MARGIN_W = 100  # adjustable devices: leave a little export so the grid never has to deliver
ADJUST_STEP_W = 50
CONFIRM_TICKS = 2  # a condition must hold this many checks in a row (clouds!)
MAX_CONSUMERS = 8
KINDS = ("mypv", "shelly2", "shelly1", "http")


@dataclass
class Consumer:
    name: str
    kind: str = "shelly2"  # mypv | shelly2 | shelly1 | http
    host: str = ""
    port: int = 502  # mypv only
    unit: int = 1  # mypv only
    channel: int = 0  # shelly only
    url_on: str = ""
    url_off: str = ""
    power_w: int = 2000  # switched: power of the device; mypv: maximum power to use
    min_power_w: int = 500  # mypv: minimum surplus before the rod starts
    min_on_min: int = 10
    min_off_min: int = 5
    battery_min_soc: int = 0  # battery first: only use surplus once the battery has this state of charge
    price_limit_ct: float | None = None  # also run on grid power when the dynamic price is at or below this
    enabled: bool = True
    id: str = field(default_factory=lambda: secrets.token_hex(4))

    @property
    def adjustable(self) -> bool:
        return self.kind == "mypv"


# meta: id -> {"since", "consumer"}: devices OpenAmpere really switched on, with the configuration to switch them off
# again, also after the device was changed or removed or a backup was restored (#217, #243)
SWITCHED_ON = "consumer_switched_on"
RETRY_S, RETRY_MAX_S = 15, 600  # an unreachable device is tried again after 15 s, 30 s, … up to every 10 minutes
GIVE_UP_S = 24 * 3600  # a disabled or removed device that stays unreachable is given up after a day

@dataclass
class State:
    on: bool | None = None  # unknown until we switched it once (in test mode: what would have been switched)
    switched_on: bool = False  # really switched on by OpenAmpere (not in test mode), remembered over a restart (#217)
    written_w: int = 0  # power really written to an adjustable device (not in test mode), #243
    failures: int = 0  # failed commands in a row: tried again later, logged once (#243)
    failing_since: float = 0.0
    retry_at: float = 0.0
    power_w: int = 0  # current setpoint (adjustable) or power while on (switched)
    since: float = 0.0
    want_on: int = 0
    want_off: int = 0
    error: str | None = None
    temperature_c: float | None = None
    target_c: float | None = None
    status: str | None = None
    actual_w: int | None = None  # measured / reported consumption (adjustable devices)


def _check_url(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError("Bitte eine vollständige Adresse mit http:// angeben.")
    return url


def validate(raw: list) -> list[Consumer]:
    if not isinstance(raw, list) or len(raw) > MAX_CONSUMERS:
        raise ValueError(f"Höchstens {MAX_CONSUMERS} Geräte.")
    consumers = []
    for item in raw:
        try:
            c = Consumer(**{k: v for k, v in item.items() if k in Consumer.__dataclass_fields__})
            c.name = str(c.name).strip()[:40]
            c.channel, c.power_w, c.min_on_min, c.min_off_min, c.battery_min_soc, c.port, c.unit, c.min_power_w = (
                int(v) for v in (c.channel, c.power_w, c.min_on_min, c.min_off_min, c.battery_min_soc, c.port,
                                 c.unit, c.min_power_w))
            c.price_limit_ct = None if c.price_limit_ct in (None, "") else float(c.price_limit_ct)
            c.enabled = bool(c.enabled)
        except (TypeError, ValueError):
            raise ValueError("Ungültige Angaben bei einem Gerät.") from None
        if not c.name:
            raise ValueError("Bitte jedem Gerät einen Namen geben.")
        if c.kind not in KINDS:
            raise ValueError("Unbekannte Schaltart.")
        if c.kind == "http":
            _check_url(c.url_on), _check_url(c.url_off)
        elif not c.host.strip():
            raise ValueError(f"{c.name}: Bitte die IP-Adresse des Geräts angeben.")
        if not 50 <= c.power_w <= 30_000 or not 0 <= c.battery_min_soc <= 100:
            raise ValueError(f"{c.name}: Leistung 50–30000 W, Speicher-Vorrang 0–100 %.")
        if c.adjustable and not 50 <= c.min_power_w <= c.power_w:
            raise ValueError(f"{c.name}: Der Mindestüberschuss muss zwischen 50 W und der maximalen Leistung liegen.")
        if not 0 <= c.min_on_min <= 240 or not 0 <= c.min_off_min <= 240:
            raise ValueError(f"{c.name}: Mindestzeiten 0–240 Minuten.")
        if not 1 <= c.port <= 65535 or not 0 <= c.unit <= 255:
            raise ValueError(f"{c.name}: Ungültiger Port oder Geräteadresse.")
        if c.price_limit_ct is not None and not -100 <= c.price_limit_ct <= 200:
            raise ValueError(f"{c.name}: Preisgrenze bitte zwischen -100 und 200 ct/kWh.")
        consumers.append(c)
    return consumers


def switch_url(c: Consumer, on: bool) -> str:
    if c.kind == "http":
        return c.url_on if on else c.url_off
    host = c.host.strip()
    if c.kind == "shelly1":
        return f"http://{host}/relay/{c.channel}?turn={'on' if on else 'off'}"
    return f"http://{host}/rpc/Switch.Set?id={c.channel}&on={'true' if on else 'false'}"


def _call(url: str) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "OpenAmpere"})
    with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310 - user-configured LAN device
        response.read(1024)


def make_heating_rod(c: Consumer) -> MyPvHeatingRod:
    return MyPvHeatingRod(c.host.strip(), c.port, c.unit)



def _switching(c: Consumer) -> tuple:
    """What decides which device a command reaches."""
    return c.kind, c.host.strip(), c.channel, c.port, c.unit, c.url_on, c.url_off

class SurplusControl:
    def __init__(self, runtime: Runtime, evcc=None) -> None:
        self.runtime = runtime
        self.evcc = evcc
        self.states: dict[str, State] = {}
        self._rods: dict[str, tuple[tuple, MyPvHeatingRod]] = {}
        self._retired: dict[str, Consumer] = {}  # disabled, removed or changed: switched off on the next tick (#217)
        self._known: dict[str, Consumer] = {}  # configuration of devices remembered as switched on (#243)
        self._restored = False
        self._lock = asyncio.Lock()  # one tick at a time (fast loop, app and other apps), #243

    # ---- settings ------------------------------------------------------------

    @property
    def consumers(self) -> list[Consumer]:
        return [Consumer(**c) for c in self.runtime.storage.get_meta("consumers") or []]

    def save(self, raw: list) -> list[Consumer]:
        consumers = validate(raw)
        new = {c.id: c for c in consumers}
        enabled = {c.id for c in consumers if c.enabled}
        for c_id in enabled:
            self._retired.pop(c_id, None)
        for old in self.consumers:
            if old.id not in enabled:
                self._retired[old.id] = old  # keeps the address, to switch it off once more
            elif _switching(old) != _switching(new[old.id]):
                # switched another way now (address, channel, type): the old relay gets its "off" under an id of its
                # own, so it does not mix with the new one (#243)
                state = self.states.pop(old.id, None)
                self._forget(old.id)
                if state and (state.switched_on or state.written_w):
                    moved = replace(old, id=f"{old.id}~old")
                    self.states[moved.id], self._retired[moved.id] = state, moved
                    self._remember(moved, state)
        self.runtime.storage.set_meta("consumers", [asdict(c) for c in consumers])
        return consumers

    def _remember(self, c: Consumer, state: State) -> None:
        """Which switched devices OpenAmpere really switched on, with the way to switch them, so it can switch them
        off after a restart, a change of the device or a restored backup, too."""
        self._store(c.id, {"since": state.since, "consumer": asdict(c)} if state.switched_on else None)

    def _forget(self, c_id: str) -> None:
        self.states.pop(c_id, None)
        self._known.pop(c_id, None)
        self._store(c_id, None)

    def _store(self, c_id: str, entry: dict | None) -> None:
        try:
            saved = {k: v for k, v in (self.runtime.storage.get_meta(SWITCHED_ON) or {}).items() if k != c_id}
            if entry:
                saved[c_id] = entry
            self.runtime.storage.set_meta(SWITCHED_ON, saved)
        except Exception as err:  # noqa: BLE001 - e.g. a full disk (#170): switching goes on
            log.warning("could not remember the state of %s: %s", c_id, err)

    def _restore(self) -> None:
        """After a restart: whether a device OpenAmpere switched on is still on is not known (a power cut switches
        many relays off), so the first tick sends the state that is wanted (#243)."""
        if self._restored:
            return
        self._restored = True
        for c_id, entry in (self.runtime.storage.get_meta(SWITCHED_ON) or {}).items():
            state = self.states.setdefault(c_id, State())
            state.on, state.switched_on = None, True
            state.since = entry if isinstance(entry, (int, float)) else entry.get("since", 0.0)  # before #243: a number
            if isinstance(entry, dict) and entry.get("consumer"):
                try:
                    self._known[c_id] = Consumer(**entry["consumer"])
                except TypeError:
                    pass

    def _log(self, details: dict, dry_run: bool, result: str) -> None:
        try:
            self.runtime.storage.log_control("consumer", details, dry_run, result)
        except Exception as err:  # noqa: BLE001 - e.g. a full disk (#170): switching must go on (#243)
            log.warning("control log not written (%s): %s", result, err)

    @staticmethod
    def _failed(state: State, now: float) -> bool:
        """Counts a failed command; True for the first one (only that one is logged)."""
        state.failures += 1
        if state.failures == 1:
            state.failing_since = now
        state.retry_at = now + min(RETRY_MAX_S, RETRY_S * 2 ** (state.failures - 1))
        return state.failures == 1

    def view(self) -> dict:
        return {"consumers": [{**asdict(c), "state": asdict(self.states.get(c.id, State())), "override": self.override(c.id)}
                              for c in self.consumers]}

    # ---- order: who gets solar surplus first ------------------------------------

    def order(self) -> dict:
        """One list for the whole site: "battery", "wallbox" (evcc) and the own devices ("c:<id>").
        The battery takes surplus up to `battery_soc`; devices below it only get surplus above that."""
        saved = self.runtime.storage.get_meta("surplus_order") or {}
        consumers = self.consumers
        own = [f"c:{c.id}" for c in consumers]
        if saved.get("order"):
            order = [k for k in saved["order"] if k in ("battery", "wallbox") or k in own]
            for key in ["battery", "wallbox", *own]:  # newly added devices go to the end
                if key not in order:
                    order.insert(0 if key == "battery" else len(order), key)
            return {"order": order, "battery_soc": int(saved.get("battery_soc", 50))}
        # not saved yet: derived from the older settings (wallbox priority, "battery first" per device)
        wallbox_first = self.runtime.config.evcc.priority == "wallbox_first"
        order = ["battery", *(["wallbox"] if wallbox_first else []), *own, *([] if wallbox_first else ["wallbox"])]
        soc = max((c.battery_min_soc for c in consumers), default=50)
        return {"order": order, "battery_soc": int(soc)}

    def save_order(self, order: list, battery_soc: int) -> dict:
        own = [f"c:{c.id}" for c in self.consumers]
        if not isinstance(order, list) or sorted(order) != sorted(["battery", "wallbox", *own]):
            raise ValueError("Die Reihenfolge passt nicht zu den eingerichteten Geräten. Bitte die Seite neu laden.")
        battery_soc = int(battery_soc)
        if not 0 <= battery_soc <= 100:
            raise ValueError("Der Speicher-Vorrang muss zwischen 0 und 100 % liegen.")
        self.runtime.storage.set_meta("surplus_order", {"order": order, "battery_soc": battery_soc})
        # keep the device list in the same order
        by_key = {f"c:{c.id}": c for c in self.consumers}
        self.runtime.storage.set_meta("consumers", [asdict(by_key[k]) for k in order if k in by_key])
        return self.order()

    def battery_first_soc(self, c: Consumer) -> int:
        """State of charge the battery gets before this device sees surplus."""
        o = self.order()
        key = f"c:{c.id}"
        return o["battery_soc"] if key in o["order"] and o["order"].index("battery") < o["order"].index(key) else 0

    def wallbox_first(self) -> bool:
        o = self.order()["order"]
        own = [i for i, k in enumerate(o) if k.startswith("c:")]
        return not own or o.index("wallbox") < min(own)

    def evcc_priority_soc(self) -> int:
        """evcc's own setting: the battery is charged first up to this state of charge."""
        o = self.order()
        return o["battery_soc"] if o["order"].index("battery") < o["order"].index("wallbox") else 0

    # ---- manual override: off or full power for a while -------------------------

    def override(self, consumer_id: str, now: float | None = None) -> dict | None:
        now = time.time() if now is None else now
        entry = (self.runtime.storage.get_meta("consumer_overrides") or {}).get(consumer_id)
        if entry and (entry.get("until") is None or entry["until"] > now):
            return entry
        return None

    def set_override(self, consumer_id: str, mode: str, hours: float | None = None, now: float | None = None,
                     source: str | None = None) -> None:
        """mode: auto (follow the surplus), off (stay off), boost (full power, e.g. hot water now).
        source: who asked for it if not the web app, e.g. the name of an app's access token."""
        now = time.time() if now is None else now
        if consumer_id not in {c.id for c in self.consumers}:
            raise ValueError("Gerät nicht gefunden.")
        if mode not in ("auto", "off", "boost"):
            raise ValueError("Unbekannte Betriebsart.")
        if hours is not None and not 0 < float(hours) <= 48:
            raise ValueError("Bitte eine Dauer bis 48 Stunden wählen.")
        overrides = self.runtime.storage.get_meta("consumer_overrides") or {}
        if mode == "auto":
            overrides.pop(consumer_id, None)
        else:
            overrides[consumer_id] = {"mode": mode, "until": now + float(hours) * 3600 if hours else None}
        self.runtime.storage.set_meta("consumer_overrides", overrides)
        name = next(c.name for c in self.consumers if c.id == consumer_id)
        self.runtime.storage.log_control("consumer_mode", {"from": {"consumer": name}, "to": {"consumer": name, "mode": mode,
                                         "hours": hours}, **({"source": source} if source else {})}, False, "ok")

    def _rod(self, c: Consumer) -> MyPvHeatingRod:
        key = (c.host.strip(), c.port, c.unit)
        cached = self._rods.get(c.id)
        if cached is None or cached[0] != key:
            if cached:
                cached[1].close()
            cached = (key, make_heating_rod(c))
            self._rods[c.id] = cached
        return cached[1]

    # ---- actions ---------------------------------------------------------------

    async def switch(self, c: Consumer, on: bool, reason: str, now: float) -> None:
        """Switched devices: on/off. Adjustable devices: full power or off (manual test)."""
        if c.adjustable:
            await self.set_power(c, c.power_w if on else 0, reason, now, force_log=True)
            return
        state = self.states.setdefault(c.id, State())
        details = {"from": {"consumer": c.name, "on": state.on}, "to": {"consumer": c.name, "on": on}}
        dry = self.runtime.config.control.dry_run
        # switching off what OpenAmpere itself switched on is the safe direction: also in test mode, like the grid
        # charging, otherwise the device keeps running while OpenAmpere believes it is off (#217)
        if dry and (on or not state.switched_on):
            self._log(details, True, f"nicht geschaltet (Testmodus): {reason}")
        else:
            if state.failures and now < state.retry_at:
                return  # not reachable: tried again later, not on every tick (#243)
            try:
                await asyncio.to_thread(_call, switch_url(c, on))
            except Exception as err:  # noqa: BLE001
                state.error = f"Nicht erreichbar: {err}"
                if self._failed(state, now):
                    self._log(details, False, f"Fehler: {err}")
                return
            state.error, state.failures, state.switched_on = None, 0, on
            if dry:
                reason = f"trotz Testmodus ausgeschaltet, OpenAmpere hatte es eingeschaltet: {reason}"
            # the state first: a log that cannot be written must not hide a relay that is on (#243)
            state.on, state.power_w = on, c.power_w if on else 0
            state.since, state.want_on, state.want_off = now, 0, 0
            self._remember(c, state)
            self._log(details, False, reason)
            return
        state.on, state.power_w = on, c.power_w if on else 0
        state.since, state.want_on, state.want_off = now, 0, 0

    async def set_power(self, c: Consumer, watts: int, reason: str, now: float, *, force_log: bool = False) -> None:
        state = self.states.setdefault(c.id, State())
        watts = max(0, min(int(watts), c.power_w))
        starting, stopping = watts > 0 and not state.on, watts == 0 and bool(state.on)
        details = {"from": {"consumer": c.name, "power_w": state.power_w}, "to": {"consumer": c.name, "power_w": watts}}
        dry = self.runtime.config.control.dry_run
        # 0 W to a heater OpenAmpere really set to power is the safe direction, also in test mode (#243)
        if dry and not (watts == 0 and state.written_w > 0):
            if starting or stopping or force_log:
                self._log(details, True, f"nicht gesendet (Testmodus): {reason}")
        else:
            if state.failures and now < state.retry_at:
                return  # not reachable: tried again later (#243)
            try:
                await self._rod(c).set_power(watts)
            except Exception as err:  # noqa: BLE001
                state.error = str(err)
                if self._failed(state, now) and (starting or force_log):
                    self._log(details, False, f"Fehler: {err}")
                return
            state.error, state.failures, state.written_w = None, 0, watts
            if dry:
                reason = f"trotz Testmodus ausgeschaltet, OpenAmpere hatte es eingeschaltet: {reason}"
            if starting or stopping or force_log or dry:
                self._log(details, False, reason)
        if starting or stopping:
            state.since = now
        state.on, state.power_w = watts > 0, watts

    async def _read_rods(self, consumers: list[Consumer]) -> None:
        for c in consumers:
            if not c.adjustable:
                continue
            state = self.states.setdefault(c.id, State())
            try:
                reading = await self._rod(c).read()
            except Exception as err:  # noqa: BLE001
                state.error, state.actual_w = str(err), None
                continue
            state.error = None
            state.temperature_c, state.target_c = reading.temperature_c, reading.target_c
            state.status, state.actual_w = reading.status_text, reading.power_w

    def _price_now(self, now: float) -> float | None:
        tariffs = self.runtime.tariffs
        tariff = tariffs.at(datetime.fromtimestamp(now, self.runtime.tz).date().isoformat())
        if tariff.kind == "fixed":
            return None
        return tariffs.price_at(now, self.runtime.tz)

    def _wallbox_reserve(self) -> float:
        """Room for a waiting car when the wallbox comes first (evcc needs its minimum power to start)."""
        if self.evcc is None or not self.wallbox_first():
            return 0.0
        state = self.evcc.fresh()
        if not state:
            return 0.0
        from .evcc import min_power_w, wants_surplus
        return sum(min_power_w(lp) for lp in state["loadpoints"] if wants_surplus(lp) and not lp["charging"])

    async def _safe_off(self, consumers: list[Consumer], reason: str, now: float) -> None:
        for c in consumers:
            state = self.states.get(c.id)
            if state and (state.on or state.switched_on or state.written_w):
                if c.adjustable:
                    await self.set_power(c, 0, reason, now)
                else:
                    await self.switch(c, False, reason, now)

    async def tick(self, now: float | None = None) -> None:
        async with self._lock:
            await self._tick(time.time() if now is None else now)

    async def _tick(self, now: float) -> None:
        runtime = self.runtime
        self._restore()
        await self._retire(now)
        consumers = [c for c in self.consumers if c.enabled]
        if not consumers:
            return
        await self._read_rods(consumers)  # status and water temperature are shown even without control
        if not runtime.config.control.enabled:
            await self._safe_off(consumers, "Steuerung ausgeschaltet", now)
            return
        snap = runtime.collector.latest
        if snap is None or runtime.collector.stale or snap.grid_power is None:
            await self._safe_off(consumers, "keine aktuellen Messwerte", now)  # safe state
            return
        dry = runtime.config.control.dry_run
        soc = snap.battery_soc or 0
        battery = snap.battery_power or 0  # + = discharging
        grid = snap.grid_power  # + = import

        def own_power(c: Consumer) -> float:
            state = self.states.get(c.id, State())
            if dry:
                return 0.0  # nothing was really switched
            if c.adjustable:
                return float(state.actual_w if state.actual_w is not None else state.power_w)
            return float(c.power_w if state.on else 0)

        # what would be free if all our devices were off; battery charging is shared separately
        free = -grid - max(0.0, battery) + sum(own_power(c) for c in consumers) - self._wallbox_reserve()
        charge = max(0.0, -battery)
        price = self._price_now(now)

        for c in consumers:
            state = self.states.setdefault(c.id, State())
            available = free + (charge if soc >= self.battery_first_soc(c) else 0.0)
            cheap = c.price_limit_ct is not None and price is not None and price <= c.price_limit_ct
            override = self.override(c.id, now)
            if override:
                used = await self._manual(c, state, override["mode"], now)
            elif c.adjustable:
                used = await self._adjust(c, state, available, cheap, now)
            else:
                used = await self._switch_step(c, state, available, cheap, now)
            from_free = min(max(free, 0.0), used)
            free -= from_free
            charge = max(0.0, charge - (used - from_free))

    async def _retire(self, now: float) -> None:
        """Devices that were disabled or removed: switched off if OpenAmpere switched them on, then forgotten (#217)."""
        # remembered before a restart, or a backup was restored: switched off with the configuration it was switched
        # on with if it is not enabled any more (#243)
        configured = {c.id: c for c in self.consumers}
        for c_id in [k for k in self.states if not (k in configured and configured[k].enabled) and k not in self._retired]:
            known = configured.get(c_id) or self._known.get(c_id)
            if known is not None and self.states[c_id].switched_on:
                self._retired[c_id] = known
            else:
                self._forget(c_id)
        for c_id, c in list(self._retired.items()):
            state = self.states.get(c_id)
            if state and state.failures and now - state.failing_since > GIVE_UP_S:
                self._log({"from": {"consumer": c.name}, "to": {"consumer": c.name, "on": False}}, False,
                          "seit einem Tag nicht erreichbar, OpenAmpere schaltet es nicht mehr")
            elif state and c.adjustable and (state.power_w or state.written_w):
                await self.set_power(c, 0, "Gerät deaktiviert, entfernt oder geändert", now)
                if state.written_w:
                    continue  # not reachable: again later
            elif state and (state.on or state.switched_on):
                await self.switch(c, False, "Gerät deaktiviert, entfernt oder geändert", now)
                if state.switched_on:
                    continue
            self._retired.pop(c_id, None)  # also when it was enabled again meanwhile (#243)
            self._forget(c_id)

    async def _manual(self, c: Consumer, state: State, mode: str, now: float) -> float:
        on = mode == "boost"
        reason = "von Hand: volle Leistung" if on else "von Hand: aus"
        if c.adjustable:
            if state.power_w != (c.power_w if on else 0):
                await self.set_power(c, c.power_w if on else 0, reason, now)
            elif on:
                await self.set_power(c, c.power_w, reason, now)  # keep-alive within the device's control timeout
            return float(state.power_w)
        if state.on is None or bool(state.on) != on:  # also when unknown after a restart (#243)
            await self.switch(c, on, reason, now)
        return float(c.power_w if state.on else 0)

    async def _adjust(self, c: Consumer, state: State, available: float, cheap: bool, now: float) -> float:
        running = bool(state.on)
        if cheap:
            target, reason = c.power_w, "günstiger Strompreis"
        elif available - ADJUST_MARGIN_W >= (ADJUST_STEP_W if running else c.min_power_w):
            target, reason = min(c.power_w, available - ADJUST_MARGIN_W), f"Überschuss {available:.0f} W"
        else:
            target, reason = 0, f"kein Überschuss ({available:.0f} W)"
        if target and not running:
            state.want_on += 1
            if state.want_on < CONFIRM_TICKS or (state.since and now - state.since < c.min_off_min * 60):
                return 0.0
        state.want_on = 0
        if running and not target and now - state.since < c.min_on_min * 60:
            target = ADJUST_STEP_W  # keep the minimum run time with the smallest step
        if target > state.power_w:
            target = state.power_w + 0.7 * (target - state.power_w)  # ramp up gently, react fast downwards
        target = int(round(target / ADJUST_STEP_W) * ADJUST_STEP_W)
        await self.set_power(c, target, reason, now)
        return float(state.power_w)

    async def _switch_step(self, c: Consumer, state: State, available: float, cheap: bool, now: float) -> float:
        if state.on is None and state.switched_on:
            # after a restart it may be on or off (#243): send what is wanted now, once
            wanted = cheap or available >= c.power_w
            await self.switch(c, wanted, "nach dem Neustart abgeglichen", now)
            return float(c.power_w if state.on else 0)
        if state.on:
            if cheap or available >= c.power_w:
                state.want_off = 0
            else:
                state.want_off += 1
                if state.want_off >= CONFIRM_TICKS and now - state.since >= c.min_on_min * 60:
                    await self.switch(c, False, f"zu wenig Überschuss ({available:.0f} W)", now)
        else:
            if cheap or available >= c.power_w + MARGIN_W:
                if state.since and now - state.since < c.min_off_min * 60:
                    state.want_on = 0
                else:
                    state.want_on += 1
                    if state.want_on >= CONFIRM_TICKS:
                        await self.switch(c, True, "günstiger Strompreis" if cheap else f"Überschuss {available:.0f} W", now)
            else:
                state.want_on = 0
        return float(c.power_w if state.on else 0)

    async def stop(self) -> None:
        """On shutdown: adjustable devices to 0 (their own timeout would do it as well)."""
        for state in self.states.values():
            state.retry_at = 0.0  # the last try, whenever the one before was
        await self._safe_off([c for c in self.consumers if c.adjustable], "OpenAmpere wird beendet", time.time())
        for _key, rod in self._rods.values():
            rod.close()
