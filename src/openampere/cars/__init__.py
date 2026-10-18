# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Cars and what their manufacturer says about them (#401): state of charge, range and odometer.

An AC wallbox does not know how full the car is, only the car does. Its manufacturer offers the data to the owner,
for the Volkswagen group through the EU Data Act portal (vw_euda.py). Every source is a module (see base.Source)
listed in SOURCES. OpenAmpere only reads: nothing is sent to the car and the wallbox control does not use the values.

- A car may be linked to a wallbox OpenAmpere controls (wallbox/native.py). While the car is plugged in there, the
  wallbox shows its state of charge, and every plug-in becomes a charge with the energy the wallbox counted.
- The portal delivers about every 15 minutes, some 15 minutes late. Values for a charge (state of charge at plug-in,
  odometer) are taken from the stored deliveries when they are shown, so a late delivery still counts.
- The login is kept, the password encrypted with secret.key. A login the source refused is not tried again by itself
  (the account could be locked) until it is changed or the owner asks for the data.
"""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
import threading
import time
from collections.abc import Collection
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from .base import CarData, Delivery, Source, SourceAuthError, SourceError, SourceSetupError
from .vw_euda import VwEuDataAct

if TYPE_CHECKING:
    from ..runtime import Runtime
    from ..wallbox.native import NativeWallboxes

log = logging.getLogger(__name__)

SOURCES: dict[str, type[Source]] = {s.key: s for s in (VwEuDataAct,)}
MAX_CARS = 3
CARS = "cars"  # meta: the cars, passwords encrypted
NEXT_DELIVERY_S = 16 * 60  # the portal stores a dataset every 15 minutes, a minute later it can be read
MIN_WAIT_S = 120
LATE_WAIT_S = 10 * 60  # the next dataset is overdue: looked for this often
FIRST_DATA_WAIT_S = 30 * 60  # the first dataset can take hours after the data request was started
SETUP_WAIT_S = 3600  # the owner still has to do something in the portal
RETRY_S, RETRY_MAX_S = 60.0, 1800.0
WALLBOX_GONE_S = 30 * 60  # a charge ends when its wallbox stays unreachable this long (a portable one was unplugged)
SHOWN_AT_WALLBOX_S = 48 * 3600  # older values are not shown at the wallbox
PLUG_IN_BEFORE_S = 12 * 3600  # a parked car keeps its state of charge: the last value before the plug-in counts
PLUG_IN_AFTER_S = 45 * 60  # otherwise the first value after it (the portal delivers late)
ODOMETER_BEFORE_S = 7 * 86400
SAVE_KWH_STEP = 0.05
EMAIL = re.compile(r"[^@\s]{1,128}@[^@\s]{1,125}")
VIN = re.compile(r"[A-Z0-9]{11,17}")

__all__ = ["SOURCES", "Car", "CarData", "Cars", "Delivery", "Source", "SourceAuthError", "SourceError",
           "SourceSetupError", "validate"]


@dataclass
class Car:
    name: str
    source: str = VwEuDataAct.key
    brand: str = "cupra"
    username: str = ""
    password: str = ""
    vin: str = ""  # empty: chosen from the cars of the account after the first login
    wallbox_id: str = ""  # a wallbox OpenAmpere controls where the car charges at home
    enabled: bool = True
    id: str = field(default_factory=lambda: secrets.token_hex(4))


@dataclass
class Live:
    status: str | None = None  # None: not asked yet; ok, waiting (no data yet), setup, auth, error
    error: str | None = None
    checked: float = 0.0
    next_at: float = 0.0
    failures: int = 0
    after: float | None = None  # the newest delivery read
    refused: tuple | None = None  # the login the source refused
    task: asyncio.Task | None = None


@dataclass
class Charge:
    """A charge that is running: the stored row plus the wallbox counter it was last seen with. The counter is stored
    with the energy, so after a restart the energy charged meanwhile is counted, too."""
    id: int
    wallbox: str
    kwh: float
    saved_kwh: float
    counter: float | None = None
    saved_counter: float | None = None


def validate(raw: list, keyless: Collection[str] = ()) -> list[Car]:
    """keyless: ids of stored cars whose password could not be read; they may stay without one."""
    if not isinstance(raw, list) or len(raw) > MAX_CARS:
        raise ValueError(f"Höchstens {MAX_CARS} Autos.")
    cars, ids = [], set()
    for item in raw:
        try:
            car = Car(**{k: v for k, v in item.items() if k in Car.__dataclass_fields__})
            car.name, car.username = str(car.name).strip()[:40], str(car.username).strip()
            car.password, car.vin = str(car.password or ""), str(car.vin or "").strip().upper()
            car.source, car.brand, car.wallbox_id = str(car.source), str(car.brand), str(car.wallbox_id or "")
            car.enabled, car.id = bool(car.enabled), str(car.id)
        except (TypeError, ValueError, AttributeError):
            raise ValueError("Ungültige Angaben bei einem Auto.") from None
        if not car.name:
            raise ValueError("Bitte jedem Auto einen Namen geben.")
        source = SOURCES.get(car.source)
        if source is None:
            raise ValueError(f"{car.name}: Diese Datenquelle kennt OpenAmpere nicht.")
        if car.brand not in {b["key"] for b in source.info()["brands"]}:
            raise ValueError(f"{car.name}: Diese Marke kennt OpenAmpere noch nicht.")
        if not EMAIL.fullmatch(car.username):
            raise ValueError(f"{car.name}: Bitte die E-Mail-Adresse des Kontos der Marke angeben.")
        if not car.password and car.id not in keyless or len(car.password) > 200:
            raise ValueError(f"{car.name}: Bitte das Passwort des Kontos der Marke angeben.")
        if car.vin and not VIN.fullmatch(car.vin):
            raise ValueError(f"{car.name}: Die Fahrzeug-Identifizierungsnummer ist ungültig.")
        if car.wallbox_id and not re.fullmatch(r"[0-9a-f]{8}", car.wallbox_id):
            raise ValueError(f"{car.name}: Unbekannte Wallbox.")
        if not re.fullmatch(r"[0-9a-f]{8}", car.id) or car.id in ids:
            car.id = secrets.token_hex(4)
        ids.add(car.id)
        cars.append(car)
    return cars


def _iso(ts: float | None) -> str | None:
    return None if ts is None else datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


class Cars:
    def __init__(self, runtime: Runtime, wallboxes: NativeWallboxes | None = None,
                 sources: dict[str, type[Source]] | None = None) -> None:
        self.runtime = runtime
        self.wallboxes = wallboxes
        self.sources = sources or SOURCES
        self.live: dict[str, Live] = {}
        self._clients: dict[tuple, tuple[Source, threading.Lock]] = {}
        self._charges: dict[str, Charge] | None = None  # car id -> running charge; read from the database once
        self._unreadable: set[str] = set()

    # ---- settings ------------------------------------------------------------------

    def _sealed(self, car: dict) -> dict:
        box, password = self.runtime.secrets, car.get("password") or ""
        return {**car, "password": password if not password or box.is_encrypted(password)
                else box.encrypt("car.password", password)}

    def _opened(self, car: dict) -> dict:
        """A password that cannot be read (another secret.key) becomes empty: the car waits for a new one."""
        password = car.get("password") or ""
        if not password or password in self._unreadable:
            return {**car, "password": ""}
        opened = self.runtime.secrets.decrypt("car.password", password)
        if not opened:
            self._unreadable.add(password)
            log.warning("car %s: the password cannot be read, it has to be entered again", car.get("name"))
        return {**car, "password": opened}

    @property
    def cars(self) -> list[Car]:
        """Checked like a change in the app also when read: the list may come from a restored backup."""
        found = []
        for raw in self.runtime.storage.get_meta(CARS) or []:
            try:
                found += validate([self._opened(raw)], keyless=(str(raw.get("id")),))
            except (ValueError, AttributeError, TypeError) as err:
                log.warning("stored car ignored: %s", err)
        return found

    def enabled(self) -> list[Car]:
        return [car for car in self.cars if car.enabled]

    def save(self, raw: list) -> list[Car]:
        """A password or VIN sent empty keeps the stored one (the app never gets the password back)."""
        stored = {car.id: car for car in self.cars}
        if not isinstance(raw, list):
            raise ValueError("Ungültige Angaben bei einem Auto.")
        items = []
        for item in raw:
            if isinstance(item, dict) and item.get("id") in stored:
                old = stored[item["id"]]
                item = {**item, "password": item.get("password") or old.password, "vin": item.get("vin") or old.vin}
            items.append(item)
        cars = validate(items, keyless={car.id for car in stored.values() if not car.password})
        wallboxes = {wb.id for wb in self.wallboxes.wallboxes} if self.wallboxes else set()
        for car in cars:
            if car.wallbox_id and car.wallbox_id not in wallboxes:
                if car.id in stored and stored[car.id].wallbox_id == car.wallbox_id:
                    car.wallbox_id = ""  # its wallbox was removed meanwhile: the link goes, the car stays
                else:
                    raise ValueError(f"{car.name}: Diese Wallbox gibt es nicht.")
        linked = [car.wallbox_id for car in cars if car.wallbox_id]
        if len(linked) != len(set(linked)):
            raise ValueError("An einer Wallbox kann nur ein Auto eingetragen sein.")
        new = {car.id: car for car in cars}
        for old in stored.values():
            now = new.get(old.id)
            if now is None:
                self.runtime.storage.delete_car(old.id)
            if now is None or (now.source, now.brand, now.username, now.password, now.vin) != \
                    (old.source, old.brand, old.username, old.password, old.vin):
                self.live.pop(old.id, None)  # asked afresh, also a login that was refused
        self.runtime.storage.set_meta(CARS, [self._sealed(asdict(car)) for car in cars])
        return cars

    # ---- the source ------------------------------------------------------------------

    def _client(self, source: str, brand: str, username: str, password: str) -> tuple[Source, threading.Lock]:
        """One client per login: several cars of one account share its session."""
        login = (source, brand, username, password)
        if login not in self._clients:
            self._clients = {k: v for k, v in self._clients.items() if k[:3] != login[:3]}  # an old password
            self._clients[login] = (self.sources[source](brand, username, password), threading.Lock())
        return self._clients[login]

    async def vehicles(self, source: str, brand: str, username: str, password: str, car_id: str | None = None) -> list[dict]:
        """The cars of an account, for choosing one in the app; a stored password if none is given."""
        stored = next((car for car in self.cars if car.id == car_id), None) if car_id else None
        password = password or (stored.password if stored else "")
        if source not in self.sources:
            raise ValueError("Diese Datenquelle kennt OpenAmpere nicht.")
        if not EMAIL.fullmatch(username or "") or not password:
            raise ValueError("Bitte E-Mail-Adresse und Passwort des Kontos der Marke angeben.")
        client, lock = self._client(source, brand, username, password)

        def ask() -> list[dict]:
            with lock:
                return client.vehicles()
        return await asyncio.to_thread(ask)

    # ---- reading, in the background ------------------------------------------------------

    def tick(self, now: float | None = None) -> None:
        """Every few seconds: the charges at the wallboxes, and a look at the source of every car that is due (in
        the background, a slow portal never holds up the loop)."""
        now = time.time() if now is None else now
        self.track(now)
        for car in self.enabled():
            live = self.live.setdefault(car.id, Live())
            if live.task and not live.task.done():
                continue
            login = (car.source, car.brand, car.username, car.password)
            if not car.vin or not car.password or now < live.next_at or live.refused == login:
                continue
            live.task = asyncio.create_task(self.poll(car, live))

    async def wait(self) -> None:
        """For tests and the shutdown: the looks that are running."""
        tasks = [live.task for live in self.live.values() if live.task and not live.task.done()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def refresh(self, car_id: str) -> None:
        """The owner asks for the data now: also after a refused login."""
        car = next((c for c in self.cars if c.id == car_id), None)
        if car is None:
            raise ValueError("Dieses Auto gibt es nicht.")
        if not car.vin:
            raise ValueError("Bitte zuerst das Auto aus dem Konto auswählen.")
        live = self.live.setdefault(car.id, Live())
        if live.task and not live.task.done():
            await asyncio.gather(live.task, return_exceptions=True)
            return
        live.refused = None
        # a task like the background look, so a tick meanwhile does not start a second one
        live.task = asyncio.create_task(self.poll(car, live))
        await asyncio.gather(live.task, return_exceptions=True)

    async def poll(self, car: Car, live: Live, now: float | None = None) -> Live:
        if live.after is None:
            live.after = self.runtime.storage.car_last_ts(car.id) or 0.0
        client, lock = self._client(car.source, car.brand, car.username, car.password)

        def look() -> Delivery:
            with lock:
                return client.fetch(car.vin, live.after or 0.0)
        try:
            delivery = await asyncio.to_thread(look)
        except SourceAuthError as err:
            self._failed(car, live, "auth", err.message, time.time())
            live.refused = (car.source, car.brand, car.username, car.password)
            return live
        except SourceSetupError as err:
            self._failed(car, live, "setup", err.message, time.time())
            live.next_at = time.time() + SETUP_WAIT_S
            return live
        except SourceError as err:
            self._failed(car, live, "error", err.message, time.time())
            return live
        except Exception as err:  # noqa: BLE001 - an unexpected answer: shown, and tried again later
            log.warning("car %s: reading the data failed: %r", car.name, err)
            self._failed(car, live, "error", "Unerwartete Antwort der Datenquelle. Bitte später nochmal versuchen.",
                         time.time())
            return live
        now = time.time() if now is None else now
        rows = [asdict(d) for d in delivery.data if not d.empty()]
        if rows:
            self.runtime.storage.add_car_points(car.id, rows)
        if delivery.data:
            live.after = max(live.after or 0.0, *(d.ts for d in delivery.data))
        if live.status in ("auth", "setup", "error"):
            log.info("car %s: data from the source again", car.name)
        has_data = bool(rows) or self.runtime.storage.car_last_ts(car.id) is not None
        live.status, live.error, live.failures, live.checked = ("ok" if has_data else "waiting"), None, 0, now
        if delivery.newest is None:
            live.next_at = now + FIRST_DATA_WAIT_S
        else:
            due = delivery.newest + NEXT_DELIVERY_S
            live.next_at = max(now + MIN_WAIT_S, due) if due > now else now + LATE_WAIT_S
        return live

    def _failed(self, car: Car, live: Live, status: str, message: str, now: float) -> None:
        if live.status != status or live.error != message:
            log.info("car %s: %s", car.name, message)
        live.failures += 1
        live.status, live.error, live.checked = status, message, now
        live.next_at = now + min(RETRY_MAX_S, RETRY_S * 2 ** (live.failures - 1))

    # ---- charges at a wallbox OpenAmpere controls ---------------------------------------------

    def _running(self) -> dict[str, Charge]:
        if self._charges is None:
            self._charges = {}
            for row in self.runtime.storage.car_sessions(open_only=True):
                if row["car"] in self._charges:  # only one can run: an older one is ended where it began
                    self.runtime.storage.update_car_session(row["id"], row["kwh"], row["start"])
                else:
                    self._charges[row["car"]] = Charge(row["id"], row["wallbox"], row["kwh"], row["kwh"],
                                                       row["counter"], row["counter"])
        return self._charges

    def track(self, now: float) -> None:
        """A charge begins when the car is plugged in at its wallbox and ends when it is unplugged (or the wallbox
        stays unreachable). The energy is what the wallbox counts meanwhile."""
        running = self._running()
        cars = {car.id: car for car in self.enabled()}
        wallboxes = {wb.id: wb for wb in self.wallboxes.enabled()} if self.wallboxes else {}
        for car_id, charge in list(running.items()):
            car = cars.get(car_id)
            if car is None or car.wallbox_id != charge.wallbox or charge.wallbox not in wallboxes:
                self._end(car_id, charge, now)
        for car in cars.values():
            wb = wallboxes.get(car.wallbox_id)
            if wb is None:
                continue
            reading = self.wallboxes.fresh(wb, now)
            charge = running.get(car.id)
            if reading is None:
                live = self.wallboxes.live.get(wb.id)
                if charge and live and live.online is False and now - live.offline_since > WALLBOX_GONE_S:
                    self._end(car.id, charge, live.offline_since)
                continue
            if not reading.connected:
                if charge:
                    self._end(car.id, charge, now)
                continue
            if charge is None:
                charge = running[car.id] = Charge(self.runtime.storage.open_car_session(car.id, wb.id, now), wb.id,
                                                  0.0, 0.0)
                log.info("car %s plugged in at wallbox %s", car.name, wb.name)
            counter = reading.lifetime_kwh if reading.lifetime_kwh is not None else reading.session_kwh
            if counter is not None:
                if charge.counter is not None:
                    # a counter that went back started anew (the wallbox's session counter)
                    charge.kwh += counter - charge.counter if counter >= charge.counter else counter
                charge.counter = counter
            if charge.kwh - charge.saved_kwh >= SAVE_KWH_STEP or (charge.saved_counter is None
                                                                  and charge.counter is not None):
                self.runtime.storage.update_car_session(charge.id, round(charge.kwh, 3), counter=charge.counter)
                charge.saved_kwh, charge.saved_counter = charge.kwh, charge.counter

    def _end(self, car_id: str, charge: Charge, end: float) -> None:
        self.runtime.storage.update_car_session(charge.id, round(charge.kwh, 3), end, counter=charge.counter)
        (self._charges or {}).pop(car_id, None)

    # ---- values ----------------------------------------------------------------------

    def latest(self, car_id: str) -> dict:
        """The newest value of everything the source delivered, each with the time of its delivery."""
        storage, out = self.runtime.storage, {}
        for name in storage.CAR_FIELDS:
            found = storage.car_value(car_id, name)
            value = found["value"] if found else None
            out[name] = bool(value) if name in ("plugged", "charging") and value is not None else value
            out[f"{name}_ts"] = found["ts"] if found else None
        out["ts"] = storage.car_last_ts(car_id)
        return out

    def _at_plug_in(self, car_id: str, start: float, name: str, before_s: float = PLUG_IN_BEFORE_S) -> float | None:
        storage = self.runtime.storage
        found = storage.car_value(car_id, name, before=start + 60, after=start - before_s) \
            or storage.car_value(car_id, name, after=start, before=start + PLUG_IN_AFTER_S, newest=False)
        return found["value"] if found else None

    def _at_end(self, car_id: str, start: float, end: float | None, name: str) -> float | None:
        if end is None:
            return None
        found = self.runtime.storage.car_value(car_id, name, after=start, before=end + PLUG_IN_AFTER_S)
        return found["value"] if found else None

    def for_wallbox(self, wallbox_id: str, now: float | None = None) -> dict | None:
        """State of charge and range of the car linked to a wallbox, for the overview; None without one or without
        recent values."""
        now = time.time() if now is None else now
        car = next((c for c in self.enabled() if c.wallbox_id == wallbox_id), None)
        if car is None:
            return None
        values = self.latest(car.id)
        if values["soc_ts"] is None or now - values["soc_ts"] > SHOWN_AT_WALLBOX_S:
            return None
        return {"car": car.name, "soc": values["soc"], "range_km": values["range_km"], "soc_ts": values["soc_ts"]}

    def _charge_view(self, car: Car, row: dict, before: dict | None, odometer_now: float | None) -> dict:
        start, end = row["start"], row["end"]
        odometer = self._at_plug_in(car.id, start, "odometer_km", ODOMETER_BEFORE_S)
        previous = self._at_plug_in(car.id, before["start"], "odometer_km", ODOMETER_BEFORE_S) if before else None
        return {"start": start, "end": end, "kwh": row["kwh"], "soc_start": self._at_plug_in(car.id, start, "soc"),
                "soc_end": self._at_end(car.id, start, end, "soc"), "odometer_km": odometer,
                # driven between the charge before and this one, and since this one
                "km_before": odometer - previous if odometer is not None and previous is not None
                and odometer >= previous else None,
                "km_since": odometer_now - odometer if end is not None and odometer is not None
                and odometer_now is not None and odometer_now >= odometer else None}

    def view(self, *, logged_in: bool = True, now: float | None = None) -> dict:
        """Never the password; the VIN only as its end, the login and the odometer only after login."""
        now = time.time() if now is None else now
        wallboxes = {wb.id: wb.name for wb in self.wallboxes.wallboxes} if self.wallboxes else {}
        items = []
        for car in self.cars:
            live = self.live.get(car.id) or Live()
            values = self.latest(car.id)
            charges = self.runtime.storage.car_sessions(car.id, limit=2)
            charge = self._charge_view(car, charges[0], charges[1] if len(charges) > 1 else None,
                                       values["odometer_km"]) if charges and logged_in else None
            item = {"id": car.id, "name": car.name, "source": car.source, "brand": car.brand, "enabled": car.enabled,
                    "wallbox_id": car.wallbox_id or None, "wallbox": wallboxes.get(car.wallbox_id),
                    "password_set": bool(car.password), "vin_set": bool(car.vin),
                    "status": live.status, "error": live.error, "checked": live.checked or None,
                    "values": values if logged_in else {k: v for k, v in values.items()
                                                       if not k.startswith("odometer")},
                    "charge": charge}
            if logged_in:
                item.update(username=car.username, vin_end=car.vin[-4:] if car.vin else None)
            items.append(item)
        return {"cars": items, "sources": [s.info() for s in self.sources.values()], "max": MAX_CARS}

    def sessions(self, limit: int = 500) -> list[dict]:
        """The charges in the form of evcc's sessions, so the analysis sums them up the same way."""
        cars = {car.id: car for car in self.cars}
        wallboxes = {wb.id: wb.name for wb in self.wallboxes.wallboxes} if self.wallboxes else {}
        out = []
        for row in self.runtime.storage.car_sessions(limit=limit):
            car = cars.get(row["car"])
            if car is None:
                continue
            start, end = row["start"], row["end"]
            out.append({"created": _iso(start), "finished": _iso(end), "loadpoint": wallboxes.get(row["wallbox"]),
                        "vehicle": car.name, "car_id": car.id, "energy_kwh": row["kwh"],
                        "duration_s": end - start if end is not None else None, "solar_pct": None, "price": None,
                        "odometer_km": self._at_plug_in(car.id, start, "odometer_km", ODOMETER_BEFORE_S),
                        "soc_start": self._at_plug_in(car.id, start, "soc"),
                        "soc_end": self._at_end(car.id, start, end, "soc"), "added_range_km": None,
                        "cost_eur": None, "grid_cost_eur": None})
        return out

    def report(self, now: float | None = None) -> list[dict]:
        """For the diagnostics report: no name, login or VIN, times as durations."""
        now = time.time() if now is None else now
        out = []
        for car in self.cars:
            live = self.live.get(car.id) or Live()
            last = self.runtime.storage.car_last_ts(car.id)
            out.append({"source": car.source, "brand": car.brand, "enabled": car.enabled,
                        "password_set": bool(car.password), "vin_set": bool(car.vin),
                        "with_wallbox": bool(car.wallbox_id), "status": live.status, "error": live.error,
                        "failures": live.failures,
                        "last_data_min": round((now - last) / 60) if last else None,
                        "charges": len(self.runtime.storage.car_sessions(car.id, limit=10_000))})
        return out


def report_markdown(car: dict) -> list[str]:
    """One car in the diagnostics report as text for an issue (German, like the rest of the report)."""
    status = {"ok": "Daten kommen an", "waiting": "wartet auf die ersten Daten", "setup": "Einrichtung im Portal fehlt",
              "auth": "Anmeldung abgelehnt", "error": "Fehler", None: "noch nicht abgefragt"}
    lines = [f"- Quelle: {car['source']}, Marke: {car['brand']}, eingeschaltet: {'ja' if car['enabled'] else 'nein'}, "
             f"Passwort: {'ja' if car['password_set'] else 'nicht lesbar, neu eintragen'}, Auto ausgewählt: "
             f"{'ja' if car['vin_set'] else 'nein'}, mit Wallbox: {'ja' if car['with_wallbox'] else 'nein'}",
             f"- Stand: {status.get(car['status'], car['status'])}, Fehlversuche: {car['failures']}"
             + (f", letzte Daten vor {car['last_data_min']} Minuten" if car.get("last_data_min") is not None else "")
             + f", Ladevorgänge: {car['charges']}"]
    if car.get("error"):
        lines.append(f"- Meldung: {car['error']}")
    return lines
