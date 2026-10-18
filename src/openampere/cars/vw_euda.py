# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Cars of the Volkswagen group (Volkswagen, Audi, Skoda, Seat, CUPRA) through the EU Data Act portal (#401).

Since June 2026 the apps' own interfaces refuse every client but the manufacturer's apps. Under the EU Data Act the
group offers owners their car's data on eu-data-act.drivesomethinggreater.com. It is not a live interface: once the
owner has given consent in a browser, linked the car and started a continuous data request, the portal stores a
dataset about every 15 minutes, a zip archive with one JSON document of data points. Reading the car means
downloading the datasets that are new and taking the values out.

The login is the web login of the brand's account (a form, then the portal's session cookies), there is no token
that lasts, so the password is kept (encrypted). The flow, the client ids per brand and the names of the data points
follow evcc's vehicle/vw/eudataact (MIT, itself based on ioBroker.vw-connect, MIT; see NOTICE), no code taken over.

What is known about the portal (evcc, #401):
  - while the car sleeps the portal stores "…_no_content_found.zip" placeholders
  - the list answers 404 until the first dataset was delivered, the data request 404 until the owner started one
  - the time stamps of single data points are not reliable; the time of the dataset counts
  - VW interjects a marketing consent page after the login now and then; it can be skipped without consenting
"""

from __future__ import annotations

import io
import json
import logging
import re
import secrets
import zipfile
from datetime import datetime, timezone
from html.parser import HTMLParser
from http.cookiejar import CookieJar
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urlparse
from urllib.request import HTTPCookieProcessor, HTTPRedirectHandler, Request

from ..gridmeter.netze_bw import USER_AGENT
from ..outbound import fetch_within
from .base import CarData, Delivery, Source, SourceAuthError, SourceError, SourceSetupError

log = logging.getLogger(__name__)

PORTAL_URL = "https://eu-data-act.drivesomethinggreater.com"
IDENTITY_URL = "https://identity.vwgroup.io"
SCOPE = "openid cars profile"
# the identity client of the portal per brand and the brand's name in the login state (evcc, ioBroker.vw-connect)
BRANDS = {
    "volkswagen": ("Volkswagen", "9b58543e-1c15-4193-91d5-8a14145bebb0@apps_vw-dilab_com", "VOLKSWAGEN_PASSENGER_CARS"),
    "audi": ("Audi", "cc29b87a-5e9a-4362-aecf-5adea6b01bbb@apps_vw-dilab_com", "AUDI"),
    "skoda": ("Škoda", "3ea88bf9-1d4e-4a68-b3ad-4098c1f1d246@apps_vw-dilab_com", "SKODA"),
    "seat": ("SEAT", "f85e5b69-e3b2-43aa-9c0d-1b7d0e0b576f@apps_vw-dilab_com", "SEAT"),
    "cupra": ("CUPRA", "f85e5b69-e3b2-43aa-9c0d-1b7d0e0b576f@apps_vw-dilab_com", "CUPRA"),
}
BACKFILL = 8  # datasets read when OpenAmpere looks for the first time: the last two hours
MAX_PER_LOOK = 96  # after a long pause only the last day is caught up, to go easy on the portal
MAX_PAGE_BYTES = 2 * 1024 ** 2
MAX_DATASET_BYTES = 10 * 1024 ** 2  # a dataset is a few kB packed
MAX_DOCUMENT_BYTES = 20 * 1024 ** 2  # unpacked: a zip bomb is cut here
DEADLINE_S = 60

# data points: the first one present wins (evcc's order)
SOC = ("battery_level_HV.value", "state_of_charge", "hv_soc",
       "506cb83e-f99f-3af3-bbeb-0429b69a78d9",  # ID.3: battery state report
       "0a18a053-b4b0-3db1-be44-a6c5dba629b1")  # Enyaq
RANGE = ("cruising_range_secondary_engine", "cruising_range_primary_engine", "cruising_range_combined",
         "0ca40e18-0564-3eda-bcc0-7aee9ef44f04")  # ID.3
ODOMETER = ("mileage", "mileage.value")
PLUG = ("plug_state", "charging_plug1_connectionstate")
CHARGE_STATE = ("charging_state", "charging_state_report.current_charge_state")
SCENARIO = "charging_state_report.charging_scenario"
TARGET_SOC = "settings.target_soc"
CARE_MODE, CARE_MODE_ON, CARE_TARGET = ("setting.bcam_activation", "BCAM_ACTIVATION_ACTIVATED",
                                        "battery_care_mode.charge_bcam_threshold")
CHARGING_STATES = ("charging", "conservationcharging", "charge_state_conservation_charging")



class _Form(HTMLParser):
    """The login form of the identity page (id emailPasswordForm): its action and fields."""

    def __init__(self, html: str, form_id: str) -> None:
        super().__init__()
        self.form_id = form_id
        self.action: str | None = None
        self.fields: dict[str, str] = {}
        self._inside = False
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form" and attrs.get("id") == self.form_id and self.action is None:
            self._inside, self.action = True, attrs.get("action") or ""
        elif tag == "input" and self._inside and attrs.get("name"):
            self.fields[attrs["name"]] = attrs.get("value") or ""

    def handle_endtag(self, tag):
        if tag == "form":
            self._inside = False


def credentials_page(html: str) -> dict:
    """The script object window._IDK of the password page, written like JavaScript (single quotes, keys without
    quotes, a comma before the closing brace)."""
    match = re.search(r"window\._IDK\s*=\s*(.*?)[;<]", html, re.S)
    if not match:
        raise SourceError("Die Anmeldeseite hat sich geändert. Bitte ein Issue öffnen.")
    text = match.group(1).replace("'", '"')
    text = re.sub(r"(?<=[{,\s])(\w+)\s*:", r'"\1":', text)
    text = re.sub(r",\s*}", "}", text)
    try:
        page = json.loads(text)
    except ValueError:
        raise SourceError("Die Anmeldeseite hat sich geändert. Bitte ein Issue öffnen.") from None
    return page if isinstance(page, dict) else {}


class _Redirects(HTTPRedirectHandler):
    """Follows the redirects of the login only between the identity service and the portal and stops before the
    portal's user page and the marketing consent page (they are not needed, and consent is never given)."""

    def __init__(self, client: VwEuDataAct) -> None:
        self.client = client
        self.stopped: str | None = None

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001 - urllib's signature
        target = urlparse(newurl)
        if self.client.user_page(target) or self.client.marketing_page(target):
            self.stopped = newurl
            return None  # urllib raises an HTTPError with the redirect, the address is kept here
        if target.scheme != self.client.scheme or target.hostname not in self.client.login_hosts:
            log.info("EU Data Act login: redirect to an unexpected host refused")
            raise SourceError("Die Anmeldung leitet an eine unerwartete Adresse weiter. Bitte ein Issue öffnen.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _number(value) -> float | None:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _time(value) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)).timestamp()


def _name_time(name: str) -> float | None:
    """The delivery time at the start of a dataset's name, e.g. 20260531100000_…zip (UTC)."""
    match = re.match(r"(\d{14})", name)
    if not match:
        return None
    return datetime.strptime(match.group(1), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc).timestamp()


def points(document: dict) -> dict[str, str]:
    """The values of a dataset by GUID and by name; of several points with the same one the newest wins."""
    newest: dict[str, tuple[float, str]] = {}
    for point in document.get("Data") or [] if isinstance(document, dict) else []:
        if not isinstance(point, dict) or point.get("value") in (None, ""):
            continue
        ts = _time(point.get("timestampUtc")) or 0.0
        for name in (point.get("key"), point.get("dataFieldName")):
            if isinstance(name, str) and name and (name not in newest or newest[name][0] <= ts):
                newest[name] = (ts, str(point["value"]))
    return {name: value for name, (_, value) in newest.items()}


def interpret(values: dict[str, str], ts: float) -> CarData:
    """The values OpenAmpere uses from the data points of one dataset."""
    first = lambda names: next((values[n] for n in names if n in values), None)  # noqa: E731
    data = CarData(ts=ts, soc=_number(first(SOC)), range_km=_number(first(RANGE)), odometer_km=_number(first(ODOMETER)))
    if data.soc is not None and not 0 <= data.soc <= 100:
        data.soc = None
    plug = first(PLUG)
    if plug is not None:
        data.plugged = plug.strip().lower() == "connected"
    state = first(CHARGE_STATE)
    if state is not None:
        upper = state.strip().upper()
        data.charging = "CHARGING_HV" in upper or upper.lower() in CHARGING_STATES
    scenario = values.get(SCENARIO, "").strip().upper()
    if scenario.endswith("_ACTIVE"):
        data.charging = data.plugged = True
    elif scenario.endswith("_FINISHED"):
        data.plugged = True
        data.charging = False if data.charging is None else data.charging
    if data.charging:
        data.plugged = True
    target = values.get(TARGET_SOC)
    if target is None and values.get(CARE_MODE) == CARE_MODE_ON:
        target = values.get(CARE_TARGET)  # only the target while the battery care mode is on (evcc #32520)
    data.target_soc = _number(target)
    return data


def read_dataset(archive: bytes) -> dict[str, str]:
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as packed:
            inner = next((i for i in packed.infolist() if i.filename.lower().endswith(".json")), None)
            if inner is None:
                raise SourceError("Ein Datensatz des Portals enthält keine Daten.")
            with packed.open(inner) as file:
                raw = file.read(MAX_DOCUMENT_BYTES + 1)
    except (zipfile.BadZipFile, OSError, RuntimeError):  # RuntimeError: an encrypted entry
        raise SourceError("Ein Datensatz des Portals ließ sich nicht lesen.") from None
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise SourceError("Ein Datensatz des Portals ist unerwartet groß.")
    try:
        return points(json.loads(raw))
    except ValueError:
        raise SourceError("Ein Datensatz des Portals ließ sich nicht lesen.") from None


class VwEuDataAct(Source):
    key = "vw_euda"
    label = "EU-Data-Act-Portal (Volkswagen-Konzern)"

    def __init__(self, brand: str, username: str, password: str, portal_url: str = PORTAL_URL,
                 identity_url: str = IDENTITY_URL, timeout: float = 30) -> None:
        super().__init__(brand, username, password)
        if brand not in BRANDS:
            raise SourceAuthError("Diese Marke kennt OpenAmpere noch nicht.")
        self.portal_url, self.identity_url, self.timeout = portal_url.rstrip("/"), identity_url.rstrip("/"), timeout
        self.scheme = urlparse(self.portal_url).scheme
        self.portal_host = urlparse(self.portal_url).hostname
        self.login_hosts = {self.portal_host, urlparse(self.identity_url).hostname}
        self.cookies = CookieJar()
        self.logged_in = False
        self._requests: dict[str, str] = {}  # VIN -> identifier of its data request

    @classmethod
    def info(cls) -> dict:
        return {"key": cls.key, "label": cls.label,
                "brands": [{"key": key, "label": label} for key, (label, _, _) in BRANDS.items()]}

    # ---- addresses -------------------------------------------------------------

    def user_page(self, url) -> bool:
        """The portal's page after a successful login, e.g. /content/euda/de/de/user.html."""
        return (url.scheme == self.scheme and url.hostname == self.portal_host
                and bool(re.fullmatch(r"/content/euda/[^/]+/[^/]+/user\.html", url.path)))

    def marketing_page(self, url) -> bool:
        return url.hostname == urlparse(self.identity_url).hostname and "/consent/marketing/" in url.path

    # ---- HTTP ----------------------------------------------------------------

    def _open(self, url: str, data: dict | None = None, headers: dict | None = None, *, follow: bool = False,
              limit: int = MAX_PAGE_BYTES) -> tuple[int, str, bytes]:
        """status, the address of the answer (after redirects; a stopped redirect: its target) and the body."""
        body = urlencode(data).encode() if data is not None else None
        request = Request(url, data=body, headers={"User-Agent": USER_AGENT, **(headers or {}),
                                                   **({"Content-Type": "application/x-www-form-urlencoded"}
                                                      if body is not None else {})})
        redirects = _Redirects(self)
        try:
            status, final, _, answer = fetch_within(request, timeout=self.timeout, deadline=DEADLINE_S,
                                                    limit=limit + 1, redirect=follow,
                                                    handlers=(HTTPCookieProcessor(self.cookies),
                                                              *((redirects,) if follow else ())))
        except HTTPError as err:
            err.close()  # the text of an error answer is not needed
            if redirects.stopped:
                return err.code, redirects.stopped, b""
            location = err.headers.get("Location") if err.headers else None
            return err.code, location or err.geturl(), b""
        except (URLError, TimeoutError, OSError) as err:
            raise SourceError("Das EU-Data-Act-Portal ist gerade nicht erreichbar.") from err
        if len(answer) > limit:
            raise SourceError("Das EU-Data-Act-Portal hat eine unerwartet große Antwort geschickt.")
        return status, final, answer

    # ---- login ---------------------------------------------------------------

    def login(self) -> None:
        if not self.username or not self.password:
            raise SourceAuthError("Bitte E-Mail und Passwort des Kontos der Marke eintragen.")
        self.cookies = CookieJar()
        self.logged_in = False
        _, client_id, state = BRANDS[self.brand]
        query = {"client_id": client_id, "response_type": "code", "scope": SCOPE, "state": f"de__en__{state}",
                 "redirect_uri": f"{self.portal_url}/login", "prompt": "login", "nonce": secrets.token_urlsafe(32)}
        status, url, page = self._open(f"{self.identity_url}/oidc/v1/authorize?{urlencode(query)}", follow=True)
        form = _Form(page.decode("utf-8", "replace"), "emailPasswordForm")
        if status >= 400 or not form.action:
            log.info("EU Data Act login: HTTP %s without the login form", status)
            raise SourceError("Die Anmeldeseite hat sich geändert. Bitte ein Issue öffnen.")
        identifier = self.identity_url + form.action
        status, _, page = self._open(identifier, {
            "_csrf": form.fields.get("_csrf", ""), "relayState": form.fields.get("relayState", ""),
            "hmac": form.fields.get("hmac", ""), "email": self.username}, follow=True)
        if status >= 400:
            log.info("EU Data Act login: e-mail step HTTP %s", status)
            raise SourceAuthError("Die Anmeldung wurde abgelehnt. Bitte die E-Mail-Adresse prüfen.")
        idk = credentials_page(page.decode("utf-8", "replace"))
        model = idk.get("templateModel") if isinstance(idk.get("templateModel"), dict) else {}
        if model.get("error"):
            log.info("EU Data Act login: the e-mail step reports an error")
            raise SourceAuthError("Die Anmeldung wurde abgelehnt. Bitte die E-Mail-Adresse prüfen.")
        if not model.get("identifierUrl") or not model.get("postAction"):
            raise SourceError("Die Anmeldeseite hat sich geändert. Bitte ein Issue öffnen.")
        status, final, _ = self._open(identifier.replace(model["identifierUrl"], model["postAction"]), {
            "_csrf": idk.get("csrf_token", ""), "relayState": model.get("relayState", ""),
            "hmac": model.get("hmac", ""), "email": self.username, "password": self.password}, follow=True)
        target = urlparse(final)
        if self.marketing_page(target):  # skipped without consenting: straight to its callback
            callback = urlparse((parse_qs(target.query).get("callback") or [""])[0])
            if (callback.scheme != self.scheme or callback.hostname != urlparse(self.identity_url).hostname
                    or callback.path not in ("/oidc/v1/oauth/client/callback", "/oidc/v1/oauth/client/callback/success")):
                raise SourceError("Die Anmeldung leitet an eine unerwartete Adresse weiter. Bitte ein Issue öffnen.")
            status, final, _ = self._open(callback._replace(query=urlencode(parse_qs(callback.query), doseq=True))
                                          .geturl(), follow=True)
            target = urlparse(final)
        if status >= 400 and not self.user_page(target):
            log.info("EU Data Act login: password step HTTP %s", status)
            if status in (400, 401, 403):
                raise SourceAuthError("Die Anmeldung wurde abgelehnt. Bitte E-Mail und Passwort prüfen.")
            raise SourceError(f"Die Anmeldung ist gerade gestört (Fehler {status}).")
        if any(part in target.path for part in ("signin-service", "/consent", "/error", "/terms")):
            # the identity service asks for the password again or the portal for consent first
            if "signin-service" in target.path and "/consent" not in target.path:
                raise SourceAuthError("Die Anmeldung wurde abgelehnt. Bitte E-Mail und Passwort prüfen.")
            raise SourceSetupError("Bitte einmal im Browser auf eu-data-act.drivesomethinggreater.com anmelden, "
                                   "zustimmen, das Auto verknüpfen und eine fortlaufende Datenanfrage (alle 15 Minuten) "
                                   "starten.")
        if not self.user_page(target):
            log.info("EU Data Act login: ended on an unexpected page")
            raise SourceError("Die Anmeldung endete auf einer unerwarteten Seite. Bitte ein Issue öffnen.")
        self.logged_in = True
        log.info("logged in to the EU Data Act portal")

    # ---- data ----------------------------------------------------------------

    def _get(self, path: str, headers: dict | None = None, *, limit: int = MAX_PAGE_BYTES,
             retry: bool = True) -> tuple[int, bytes]:
        if not self.logged_in:
            self.login()
        status, _, body = self._open(self.portal_url + path, headers={"Accept": "application/json", **(headers or {})},
                                     limit=limit)
        if status in (301, 302, 303, 307, 308, 401, 403):  # the session ended: log in once more
            self.logged_in = False
            if retry:
                return self._get(path, headers, limit=limit, retry=False)
            log.info("EU Data Act portal: HTTP %s after a new login", status)
            raise SourceError(f"Das Portal gibt die Daten trotz Anmeldung nicht frei (Fehler {status}).")
        return status, body

    def _json(self, path: str, headers: dict | None = None):
        status, body = self._get(path, headers)
        if status == 404:
            return None
        if status >= 400:
            log.info("EU Data Act portal: HTTP %s", status)
            raise SourceError(f"Das EU-Data-Act-Portal meldet einen Fehler ({status}).")
        try:
            return json.loads(body)
        except ValueError:
            raise SourceError("Unerwartete Antwort vom EU-Data-Act-Portal.") from None

    def vehicles(self) -> list[dict]:
        answer = self._json("/proxy_api/consent/me/vehicles?viewPosition=FRONT_LEFT")
        if isinstance(answer, dict):
            answer = answer.get("vehicles")
        cars = []
        for item in answer if isinstance(answer, list) else []:
            if not isinstance(item, dict):
                continue
            vin = str(item.get("vin") or item.get("vehicleIdentificationNumber") or "").strip().upper()
            name = next((str(item[k]) for k in ("nickName", "vehicleNickname", "nickname", "modelName") if item.get(k)), "")
            if re.fullmatch(r"[A-Z0-9]{11,17}", vin):
                cars.append({"vin": vin, "name": name[:40]})
        if not cars:
            raise SourceSetupError("Im EU-Data-Act-Portal ist noch kein Auto verknüpft. Bitte dort im Browser das "
                                   "Auto verknüpfen und eine fortlaufende Datenanfrage (alle 15 Minuten) starten.")
        return cars

    def _request_id(self, vin: str) -> str:
        if vin not in self._requests:
            answer = self._json(f"/proxy_api/euda-apim/datarequest/vehicles/{quote(vin)}/metadata/partial")
            identifier = answer.get("Identifier") if isinstance(answer, dict) else None
            if not isinstance(identifier, str) or not re.fullmatch(r"[\w.-]{1,128}", identifier):
                raise SourceSetupError("Für dieses Auto läuft im EU-Data-Act-Portal keine Datenanfrage. Bitte dort im "
                                       "Browser eine fortlaufende Datenanfrage (alle 15 Minuten) starten.")
            self._requests[vin] = identifier
        return self._requests[vin]

    def fetch(self, vin: str, after: float) -> Delivery:
        identifier = self._request_id(vin)
        base = f"/proxy_api/euda-apim/datadelivery/vehicles/{quote(vin)}/{quote(identifier)}"
        listed = self._json(f"{base}/list", {"type": "partial"})  # 404 until the car delivered its first dataset
        if isinstance(listed, dict):
            listed = listed.get("files")
        files = []
        for item in listed if isinstance(listed, list) else []:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                continue
            ts = _time(item.get("createdOn")) or _name_time(item["name"])
            if ts is not None:
                files.append((ts, item["name"]))
        files.sort()
        newest = files[-1][0] if files else None
        content = [(ts, name) for ts, name in files if not name.lower().endswith("_no_content_found.zip")]
        pending = [f for f in content if f[0] > after][-MAX_PER_LOOK:] if after else content[-BACKFILL:]
        data = []
        for ts, name in pending:
            status, archive = self._get(f"{base}/download", {"filename": name, "type": "partial", "Accept": "*/*"},
                                        limit=MAX_DATASET_BYTES)
            if status == 404:
                continue  # gone in the meantime
            if status >= 400:
                raise SourceError(f"Das EU-Data-Act-Portal meldet einen Fehler ({status}).")
            if len(archive) > MAX_DATASET_BYTES:
                raise SourceError("Ein Datensatz des Portals ist unerwartet groß.")
            data.append(interpret(read_dataset(archive), ts))
        return Delivery(data=data, newest=newest)
