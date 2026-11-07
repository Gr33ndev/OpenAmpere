# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""BMW and MINI cars through BMW CarData (#595).

BMW's own app interface is closed to other clients. Under the EU Data Act BMW offers owners their car's data as
"BMW CarData": a REST interface with 50 requests a day per account and a live data stream (MQTT, not used here: it
would need a new dependency). The owner creates a client ID in the BMW or MINI web portal, requests access to the
CarData API and confirms OpenAmpere's login there with a code (OAuth 2.0 device authorization grant with PKCE). No
password goes through OpenAmpere.

The login gives an access token (1 hour) and a refresh token (2 weeks) that is renewed with every use. The refresh
token is what OpenAmpere keeps (encrypted, in the place of the password); a renewed one is stored right away
(cars/__init__.py). The data is read through a "container" of telematic keys that OpenAmpere creates once.

Endpoints, scopes and limits follow BMW's public CarData documentation; the names of the telematic keys and what their
values mean follow evcc's vehicle/bmw/cardata (MIT; see NOTICE), no code taken over.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import secrets
import time
from datetime import datetime, timezone
from http.client import HTTPException
from typing import NoReturn
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request

from ..gridmeter.netze_bw import USER_AGENT
from ..outbound import fetch_within
from .base import CarData, Delivery, Source, SourceAuthError, SourceError, SourceSetupError

log = logging.getLogger(__name__)

AUTH_URL = "https://customer.bmwgroup.com"
API_URL = "https://api-cardata.bmwgroup.com"
SCOPE = "authenticate_user openid cardata:api:read"
# the portal where the owner creates the client ID, per brand
BRANDS = {
    "bmw": ("BMW", "https://www.bmw.de/de-de/mybmw/vehicle-overview"),
    "mini": ("MINI", "https://www.mini.de/de-de/mymini/vehicle-overview"),
}
CLIENT_ID = re.compile(r"[A-Za-z0-9-]{8,64}")
CONTAINER_NAME, CONTAINER_PURPOSE = "OpenAmpere", "v1"
EVERY_S = 40 * 60  # 36 looks a day: room in BMW's 50 requests for the setup and for asking now
MIN_GAP_S = 60  # asking now more often than this answers from the last look
LIMIT_WAIT_S = 3 * 3600  # after BMW's daily limit was reached
UNMAPPED_WAIT_S = 6 * 3600  # the car is no longer mapped to the account: the owner has to act in the portal
MAX_ANSWER_BYTES = 1024 ** 2
DEADLINE_S = 60

SOC = ("vehicle.drivetrain.batteryManagement.header", "vehicle.powertrain.electric.battery.stateOfCharge.displayed")
TARGET_SOC = ("vehicle.powertrain.electric.battery.stateOfCharge.target",)
RANGE = ("vehicle.drivetrain.electricEngine.kombiRemainingElectricRange", "vehicle.drivetrain.lastRemainingRange")
ODOMETER = ("vehicle.vehicle.travelledDistance",)
PLUG = ("vehicle.body.chargingPort.status", "vehicle.body.chargingPort.combinedStatus")
CHARGE_STATE = ("vehicle.drivetrain.electricEngine.charging.status",
                "vehicle.drivetrain.electricEngine.charging.hvStatus")
CHARGING = {"CHARGINGACTIVE", "CHARGING"}
PLUGGED_NOT_CHARGING = {"INITIALIZATION", "CHARGINGPAUSED", "CHARGINGENDED", "WAITING_FOR_CHARGING",
                        "FINISHED_FULLY_CHARGED", "FINISHED_NOT_FULL"}
PLUGGED = CHARGING | PLUGGED_NOT_CHARGING
KEYS = (*SOC, *TARGET_SOC, *RANGE, *ODOMETER, *PLUG, *CHARGE_STATE)



def _relogin() -> NoReturn:
    raise SourceAuthError("Die Anmeldung bei BMW CarData ist abgelaufen oder wurde zurückgezogen. Bitte unter Mehr → "
                           "Verbindung → Auto beim Auto auf „Ändern“ tippen und die Anmeldung neu bestätigen.")


def _number(value) -> float | None:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _time(value, now: float) -> float | None:
    """A time stamp of BMW; one in the future (a clock that runs ahead) counts as now."""
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return min(now, (moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)).timestamp())


def token_of(stored: str) -> dict:
    """The kept login: {"refresh_token": …, "gcid": …}; empty if it cannot be read."""
    try:
        token = json.loads(stored) if stored else {}
    except ValueError:
        return {}
    return token if isinstance(token, dict) and isinstance(token.get("refresh_token"), str) else {}


def interpret(telematic: dict, after: float, now: float) -> tuple[list[CarData], float | None]:
    """The values of one answer that are newer than `after`, one delivery per time stamp (oldest first), and the
    newest time stamp of all values OpenAmpere uses."""
    found: dict[str, tuple[float, str]] = {}
    for key in KEYS:
        item = telematic.get(key) if isinstance(telematic, dict) else None
        if not isinstance(item, dict) or item.get("value") in (None, ""):
            continue
        ts = _time(item.get("timestamp"), now)
        if ts is not None:
            found[key] = (ts, str(item["value"]).strip())
    newest = max((ts for ts, _ in found.values()), default=None)
    deliveries: dict[float, CarData] = {}

    def first(names: tuple[str, ...]) -> tuple[float, str] | None:
        """The newest of the keys for one value (an old one a model sent once must not hide a newer one); with the
        same time stamp the first key."""
        best = None
        for value in (found[n] for n in names if n in found):
            if best is None or value[0] > best[0]:
                best = value
        return best

    def put(names: tuple[str, ...], field: str, convert) -> None:
        value = first(names)
        if value is None or value[0] <= after:
            return
        data = deliveries.setdefault(value[0], CarData(ts=value[0]))
        setattr(data, field, convert(value[1]))

    put(SOC, "soc", lambda v: (n if (n := _number(v)) is not None and 0 <= n <= 100 else None))
    put(TARGET_SOC, "target_soc", lambda v: (n if (n := _number(v)) is not None and 0 < n <= 100 else None))
    put(RANGE, "range_km", lambda v: (n if (n := _number(v)) is not None and n >= 0 else None))
    put(ODOMETER, "odometer_km", lambda v: (n if (n := _number(v)) is not None and n >= 0 else None))
    put(PLUG, "plugged", lambda v: v.upper() == "CONNECTED")
    # the newest of the two charge keys (an older known value must not hide a newer one); with the same time stamp
    # the first known value. A value not known (e.g. NOCHARGING, CHARGINGERROR) only says "not charging".
    states = [found[n] for n in CHARGE_STATE if n in found]
    state, plug = None, first(PLUG)
    if states:
        latest = max(ts for ts, _ in states)
        same = [v for v in states if v[0] == latest]
        state = next((v for v in same if v[1].upper() in PLUGGED), same[0])
        if state[0] > after:
            data = deliveries.setdefault(state[0], CarData(ts=state[0]))
            data.charging = state[1].upper() in CHARGING
            if data.charging and (plug is None or plug[0] < state[0]):
                data.plugged = True  # charging: the cable is in. Otherwise the plug key says it (a "finished" state
                # may be sent again after unplugging)
    if (plug is not None and plug[0] > after and plug[1].upper() != "CONNECTED"
            and (state is None or state[0] < plug[0])):
        deliveries[plug[0]].charging = False  # unplugged after the last charge state: no longer charging
    return [deliveries[ts] for ts in sorted(deliveries) if not deliveries[ts].empty()], newest


def _verifier() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:86]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _send(method: str, url: str, *, form: dict | None = None, body: dict | None = None,
          headers: dict | None = None, timeout: float = 30) -> tuple[int, dict | list | None]:
    """status and the JSON answer (None if there is none); an error answer is read as well, it names the reason. A
    redirect is refused (the token stays at the address it was sent to)."""
    data, kind = None, {}
    if form is not None:
        data, kind = urlencode(form).encode(), {"Content-Type": "application/x-www-form-urlencoded"}
    elif body is not None:
        data, kind = json.dumps(body).encode(), {"Content-Type": "application/json"}
    request = Request(url, data=data, method=method,
                      headers={"User-Agent": USER_AGENT, "Accept": "application/json", **kind, **(headers or {})})
    try:
        status, _, _, raw = fetch_within(request, timeout=timeout, deadline=DEADLINE_S, limit=MAX_ANSWER_BYTES + 1,
                                         redirect=False)
    except HTTPError as err:
        status = err.code
        try:
            raw = err.read(MAX_ANSWER_BYTES + 1) if 400 <= err.code < 600 else b""
        except OSError:
            raw = b""
        finally:
            err.close()
    except (URLError, TimeoutError, OSError, HTTPException) as err:  # also an answer cut off (IncompleteRead)
        raise SourceError("BMW CarData ist gerade nicht erreichbar.") from err
    if len(raw) > MAX_ANSWER_BYTES:
        raise SourceError("BMW CarData hat eine unerwartet große Antwort geschickt.")
    try:
        return status, json.loads(raw) if raw.strip() else None
    except ValueError:
        return status, None


def _code(answer) -> str:
    """The error code of an answer: "authorization_pending", "CU-429", …"""
    if not isinstance(answer, dict):
        return ""
    for key in ("error", "exveErrorId", "errorCode", "code"):
        if isinstance(answer.get(key), str):
            return answer[key]
    fault = answer.get("fault")
    detail = fault.get("detail") if isinstance(fault, dict) else None
    return str(detail.get("errorcode") or "") if isinstance(detail, dict) else ""


class BmwCarData(Source):
    key = "bmw_cardata"
    label = "BMW CarData (BMW, MINI)"
    login_kind = "device"
    every_s = EVERY_S

    def __init__(self, brand: str, username: str, password: str, timeout: float = 30) -> None:
        super().__init__(brand, username, password)
        if brand not in BRANDS:
            raise SourceAuthError("Diese Marke kennt OpenAmpere noch nicht.")
        self.timeout = timeout
        self._access: str | None = None
        self._access_until = 0.0
        self._container: str | None = None
        self._last_look: dict[str, tuple[float, Delivery]] = {}  # VIN -> when and what was read last

    @classmethod
    def info(cls) -> dict:
        return {"key": cls.key, "label": cls.label, "login": cls.login_kind,
                "brands": [{"key": key, "label": label, "portal": portal} for key, (label, portal) in BRANDS.items()]}

    @classmethod
    def valid_username(cls, username: str) -> bool:
        return bool(CLIENT_ID.fullmatch(username))

    # ---- the login in the owner's browser (device authorization grant) ---------------------------

    @classmethod
    def start_login(cls, brand: str, username: str) -> dict:
        """Asks BMW for a code the owner confirms in the browser. The result is kept by OpenAmpere until the login
        is finished: the address and the code for the owner, the rest for asking for the token."""
        if brand not in BRANDS or not CLIENT_ID.fullmatch(username):
            raise SourceAuthError("Bitte die Client-ID aus dem Portal von BMW oder MINI eintragen.")
        verifier, challenge = _verifier()
        status, answer = _send("POST", f"{AUTH_URL}/gcdm/oauth/device/code", form={
            "client_id": username, "response_type": "device_code", "scope": SCOPE,
            "code_challenge": challenge, "code_challenge_method": "S256"})
        if status in (400, 401, 403):
            log.info("BMW CarData login: device code refused, HTTP %s %s", status, _code(answer)[:40])
            raise SourceAuthError("BMW kennt diese Client-ID nicht oder sie hat keinen Zugang zur CarData API. Bitte "
                                  "im Portal den Zugang zur CarData API anfragen, dann eine neue Client-ID erstellen "
                                  "und hier eintragen.")
        if status >= 400 or not isinstance(answer, dict):
            raise SourceError(f"Die Anmeldung bei BMW ist gerade gestört (Fehler {status}).")
        url = str(answer.get("verification_uri_complete") or answer.get("verification_uri") or "")
        code, device = str(answer.get("user_code") or ""), str(answer.get("device_code") or "")
        if urlparse(url).scheme != "https" or not code or not device:
            raise SourceError("Unerwartete Antwort von BMW. Bitte ein Issue öffnen.")
        expires = _number(answer.get("expires_in")) or 300
        interval = _number(answer.get("interval")) or 5
        return {"url": url, "code": code[:32], "expires_at": time.time() + min(expires, 3600),
                "interval": min(max(interval, 2), 60), "device_code": device, "verifier": verifier}

    @classmethod
    def finish_login(cls, brand: str, username: str, pending: dict) -> str | None:
        """Asks once whether the owner confirmed the code: the login to keep, or None while BMW still waits."""
        if time.time() > pending["expires_at"]:
            raise SourceAuthError("Der Code ist abgelaufen. Bitte die Anmeldung neu starten.")
        status, answer = _send("POST", f"{AUTH_URL}/gcdm/oauth/token", form={
            "client_id": username, "device_code": pending["device_code"], "code_verifier": pending["verifier"],
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code"})
        code = _code(answer)
        if status == 200 and isinstance(answer, dict) and isinstance(answer.get("refresh_token"), str):
            log.info("BMW CarData login confirmed")
            return json.dumps({"refresh_token": answer["refresh_token"], "gcid": str(answer.get("gcid") or "")})
        if code in ("authorization_pending", "slow_down"):
            return None
        if code == "expired_token":
            raise SourceAuthError("Der Code ist abgelaufen. Bitte die Anmeldung neu starten.")
        if code == "access_denied":
            raise SourceAuthError("Die Anmeldung wurde im Browser abgelehnt.")
        log.info("BMW CarData login: token refused, HTTP %s %s", status, code[:60])
        if "invalid_access_token" in code:
            raise SourceSetupError("BMW hat die Anmeldung angenommen, gibt aber keinen Zugang heraus. Meist hilft: "
                                   "im Portal die Client-ID löschen, eine neue erstellen, den Zugang zur CarData API "
                                   "anfragen und die neue Client-ID hier eintragen.")
        if status in (400, 401, 403):
            raise SourceAuthError("BMW hat die Anmeldung abgelehnt. Bitte die Anmeldung neu starten.")
        raise SourceError(f"Die Anmeldung bei BMW ist gerade gestört (Fehler {status}).")

    # ---- tokens and requests ----------------------------------------------------------------

    def _token(self) -> str:
        if self._access and time.time() < self._access_until - 60:
            return self._access
        token = token_of(self.password)
        if not token:
            _relogin()
        status, answer = _send("POST", f"{AUTH_URL}/gcdm/oauth/token", form={
            "grant_type": "refresh_token", "refresh_token": token["refresh_token"], "client_id": self.username},
            timeout=self.timeout)
        if status in (400, 401, 403):
            log.info("BMW CarData: the refresh token was refused (%s)", _code(answer)[:40])
            _relogin()
        if status >= 400 or not isinstance(answer, dict) or not isinstance(answer.get("access_token"), str):
            raise SourceError(f"Die Anmeldung bei BMW ist gerade gestört (Fehler {status}).")
        self._access = answer["access_token"]
        self._access_until = time.time() + (_number(answer.get("expires_in")) or 3600)
        if isinstance(answer.get("refresh_token"), str):
            # BMW renews the refresh token with every use: Cars stores the new one (the old one may be void now)
            self.password = json.dumps({"refresh_token": answer["refresh_token"],
                                        "gcid": str(answer.get("gcid") or token.get("gcid") or "")})
        return self._access

    def _api(self, method: str, path: str, body: dict | None = None, *, retry: bool = True):
        status, answer = _send(method, API_URL + path, body=body, timeout=self.timeout,
                               headers={"Authorization": f"Bearer {self._token()}", "x-version": "v1"})
        code = _code(answer)
        if status == 401 or code == "CU-102":  # the access token ended early: a new one, once
            self._access = None
            if retry:
                return self._api(method, path, body, retry=False)
            _relogin()
        if status == 429 or code == "CU-429":
            log.info("BMW CarData: daily limit reached")
            raise SourceError("BMW lässt für heute keine weiteren Abfragen zu (50 am Tag für das ganze Konto). "
                              "OpenAmpere versucht es in ein paar Stunden wieder.", wait_s=LIMIT_WAIT_S)
        if status == 403:
            log.info("BMW CarData: HTTP 403 %s", code[:40])
            raise SourceSetupError("BMW gibt die Daten nicht frei. Bitte im Portal prüfen, ob der Zugang zur CarData "
                                   "API freigeschaltet ist und das Konto Hauptnutzer des Autos ist.")
        if status == 404:
            return None
        if status >= 300:  # also a redirect: it is not followed with the token
            log.info("BMW CarData: HTTP %s %s", status, code[:40])
            raise SourceError(f"BMW CarData meldet einen Fehler ({status}).")
        return answer

    # ---- data ---------------------------------------------------------------------------

    def vehicles(self) -> list[dict]:
        answer = self._api("GET", "/customers/vehicles/mappings")
        if isinstance(answer, dict):
            answer = answer.get("mappings") or answer.get("vehicles")
        cars = []
        for item in answer if isinstance(answer, list) else []:
            if not isinstance(item, dict) or str(item.get("mappingType") or "").upper() != "PRIMARY":
                continue
            vin = str(item.get("vin") or "").strip().upper()
            if re.fullmatch(r"[A-Z0-9]{17}", vin):
                cars.append({"vin": vin, "name": ""})
        if not cars:
            raise SourceSetupError("Im Konto ist kein Auto, dessen Hauptnutzer du bist. Bitte im Portal von BMW oder "
                                   "MINI das Auto mit dem Konto verknüpfen; nur der Hauptnutzer bekommt die Daten.")
        return cars

    def _container_id(self) -> str:
        if self._container:
            return self._container
        answer = self._api("GET", "/customers/containers")
        listed = answer.get("containers") if isinstance(answer, dict) else answer
        for item in listed if isinstance(listed, list) else []:
            if (isinstance(item, dict) and item.get("name") == CONTAINER_NAME
                    and item.get("purpose") == CONTAINER_PURPOSE
                    and str(item.get("state") or "ACTIVE").upper() == "ACTIVE"
                    and isinstance(item.get("containerId"), str)):
                self._container = item["containerId"]
                return self._container
        created = self._api("POST", "/customers/containers", {
            "name": CONTAINER_NAME, "purpose": CONTAINER_PURPOSE, "technicalDescriptors": list(KEYS)})
        container = created.get("containerId") if isinstance(created, dict) else None
        if not isinstance(container, str) or not re.fullmatch(r"[\w-]{1,64}", container):
            raise SourceError("BMW CarData hat keinen Datencontainer angelegt. Bitte später nochmal versuchen.")
        log.info("BMW CarData: data container created")
        self._container = container
        return container

    def fetch(self, vin: str, after: float) -> Delivery:
        now = time.time()
        last = self._last_look.get(vin)
        if last and now - last[0] < MIN_GAP_S:  # asked again right away: BMW's requests are few
            return Delivery(data=[], newest=last[1].newest)
        kept = self._container
        container = self._container_id()
        answer = self._api("GET", f"/customers/vehicles/{quote(vin)}/telematicData?containerId={quote(container)}")
        if answer is None and kept:  # the kept container may be gone (deleted in the portal): looked up once more
            self._container = None
            container = self._container_id()
            answer = self._api("GET", f"/customers/vehicles/{quote(vin)}/telematicData?containerId={quote(container)}")
        if answer is None:  # also with a container just looked up: the car is not (or no longer) in the account
            raise SourceSetupError("BMW kennt das Auto in diesem Konto nicht mehr. Bitte im Portal von BMW oder MINI "
                                   "prüfen, ob das Auto noch mit dem Konto verknüpft ist und du sein Hauptnutzer bist.",
                                   wait_s=UNMAPPED_WAIT_S)
        telematic = answer.get("telematicData") if isinstance(answer, dict) else None
        data, newest = interpret(telematic if isinstance(telematic, dict) else {}, after, now)
        delivery = Delivery(data=data, newest=newest)
        self._last_look[vin] = (now, delivery)
        return delivery
