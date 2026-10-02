"""Surplus consumers: switch a heating rod, a heat pump (SG-Ready contact) or any other load on when there
is solar surplus, by priority, with minimum on and off times. Switching uses Shelly relays (Gen1 and Gen2+)
or two plain HTTP URLs. Respects the control switch and the test mode; every switch is logged."""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field

from .runtime import Runtime

log = logging.getLogger(__name__)

MARGIN_W = 200  # surplus needed on top of the consumer's power before switching on
IMPORT_LIMIT_W = 150  # grid import above this means: not enough sun any more
CONFIRM_TICKS = 2  # a condition must hold this many checks in a row (clouds!)
MAX_CONSUMERS = 8


@dataclass
class Consumer:
    name: str
    kind: str = "shelly2"  # shelly1 | shelly2 | http
    host: str = ""
    channel: int = 0
    url_on: str = ""
    url_off: str = ""
    power_w: int = 2000
    min_on_min: int = 10
    min_off_min: int = 5
    battery_min_soc: int = 0  # battery first: only use surplus once the battery has this state of charge
    enabled: bool = True
    id: str = field(default_factory=lambda: secrets.token_hex(4))


@dataclass
class State:
    on: bool | None = None  # unknown until we switched it once
    since: float = 0.0
    want_on: int = 0
    want_off: int = 0
    error: str | None = None


def _check_url(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError("Bitte eine vollständige Adresse mit http:// angeben.")
    return url


def validate(raw: list) -> list[Consumer]:
    if not isinstance(raw, list) or len(raw) > MAX_CONSUMERS:
        raise ValueError(f"Höchstens {MAX_CONSUMERS} Verbraucher.")
    consumers = []
    for item in raw:
        try:
            c = Consumer(**{k: v for k, v in item.items() if k in Consumer.__dataclass_fields__})
            c.name = str(c.name).strip()[:40]
            c.channel, c.power_w, c.min_on_min, c.min_off_min, c.battery_min_soc = (
                int(v) for v in (c.channel, c.power_w, c.min_on_min, c.min_off_min, c.battery_min_soc))
            c.enabled = bool(c.enabled)
        except (TypeError, ValueError):
            raise ValueError("Ungültige Angaben bei einem Verbraucher.") from None
        if not c.name:
            raise ValueError("Bitte jedem Verbraucher einen Namen geben.")
        if c.kind not in ("shelly1", "shelly2", "http"):
            raise ValueError("Unbekannte Schaltart.")
        if c.kind == "http":
            _check_url(c.url_on), _check_url(c.url_off)
        elif not c.host.strip():
            raise ValueError(f"{c.name}: Bitte die IP-Adresse des Shelly angeben.")
        if not 50 <= c.power_w <= 30_000 or not 0 <= c.battery_min_soc <= 100:
            raise ValueError(f"{c.name}: Leistung 50–30000 W, Speicher-Vorrang 0–100 %.")
        if not 0 <= c.min_on_min <= 240 or not 0 <= c.min_off_min <= 240:
            raise ValueError(f"{c.name}: Mindestzeiten 0–240 Minuten.")
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


class SurplusControl:
    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime
        self.states: dict[str, State] = {}

    @property
    def consumers(self) -> list[Consumer]:
        return [Consumer(**c) for c in self.runtime.storage.get_meta("consumers") or []]

    def save(self, raw: list) -> list[Consumer]:
        consumers = validate(raw)
        self.runtime.storage.set_meta("consumers", [asdict(c) for c in consumers])
        return consumers

    def view(self) -> dict:
        return {"consumers": [{**asdict(c), "state": asdict(self.states.get(c.id, State()))} for c in self.consumers]}

    async def switch(self, c: Consumer, on: bool, reason: str, now: float) -> None:
        state = self.states.setdefault(c.id, State())
        dry = self.runtime.config.control.dry_run
        details = {"from": {"consumer": c.name, "on": state.on}, "to": {"consumer": c.name, "on": on}}
        if dry:
            self.runtime.storage.log_control("consumer", details, True, f"nicht geschaltet (Testmodus): {reason}")
        else:
            try:
                await asyncio.to_thread(_call, switch_url(c, on))
                state.error = None
            except Exception as err:  # noqa: BLE001
                state.error = f"Nicht erreichbar: {err}"
                self.runtime.storage.log_control("consumer", details, False, f"Fehler: {err}")
                return
            self.runtime.storage.log_control("consumer", details, False, reason)
        state.on, state.since, state.want_on, state.want_off = on, now, 0, 0

    async def tick(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        runtime = self.runtime
        snap = runtime.collector.latest
        consumers = [c for c in self.consumers if c.enabled]
        if not consumers or not runtime.config.control.enabled:
            return
        if snap is None or runtime.collector.stale or snap.grid_power is None:
            # no current readings: switch everything we turned on off again (safe state)
            for c in consumers:
                if self.states.get(c.id, State()).on:
                    await self.switch(c, False, "keine aktuellen Messwerte", now)
            return
        soc = snap.battery_soc or 0
        charging = max(0.0, -(snap.battery_power or 0))  # battery charging power that could be diverted
        export = max(0.0, -snap.grid_power)
        grid_import = max(0.0, snap.grid_power)

        # too little sun: switch off the lowest priority consumer that may be switched off
        if grid_import > IMPORT_LIMIT_W:
            for c in reversed(consumers):
                state = self.states.get(c.id, State())
                if state.on and now - state.since >= c.min_on_min * 60:
                    state.want_off += 1
                    if state.want_off >= CONFIRM_TICKS:
                        await self.switch(c, False, f"Netzbezug {grid_import:.0f} W", now)
                    return
            return
        for c in consumers:
            self.states.get(c.id, State()).want_off = 0

        # surplus: switch on the highest priority consumer that is off and fits
        for c in consumers:
            state = self.states.setdefault(c.id, State())
            if state.on:
                continue
            available = export + (charging if soc >= c.battery_min_soc else 0)
            if soc < c.battery_min_soc or available < c.power_w + MARGIN_W:
                state.want_on = 0
                break  # keep the priority order: lower ones wait
            if state.since and now - state.since < c.min_off_min * 60:
                break
            state.want_on += 1
            if state.want_on >= CONFIRM_TICKS:
                await self.switch(c, True, f"Überschuss {available:.0f} W", now)
            break
