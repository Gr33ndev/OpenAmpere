"""Netze BW (Baden-Württemberg): smart-meter values from the customer portal meine.netze-bw.de.

There is no documented API. The portal's web app reads its data from its own backend (a "backend for frontend" behind
/bff), after a login through Auth0 with e-mail and password. The login flow and the list of installations follow the
MIT-licensed Home Assistant integration https://github.com/cygnusb/ha-netze-bw, the CSV download of the quarter-hour
values follows a working importer a user shared in #67 (facts only, no code taken over from either).

What real accounts showed (#67):
  - grid power (1.8.0) and feed-in (2.8.0) of one smart meter are two installations with their own ids and the same
    meter number
  - the portal session can still be valid (/bff/auth/user 200) while the meter values answer 401: then the session is
    dropped and the login done from scratch
  - values exist only since the smart meter was installed, and a day is published hours after it ended (the overview
    shows 0 kWh meanwhile). A day counts only when all its quarter hours are there; a missing value is unknown, not 0.
  - CSV: ";" separated, "Datum" (dd.mm.yyyy) and "Uhrzeit" (hh:mm) in German time, the first row is the meter reading at
    the start, every further row the energy (kWh, decimal comma) of the quarter hour that ends at its time
"""

from __future__ import annotations

import csv
import io
import json
import logging
import time as clock
from datetime import date, datetime, time, timedelta, timezone
from html.parser import HTMLParser
from http.cookiejar import CookieJar
from importlib.metadata import PackageNotFoundError, version
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlparse
from urllib.request import HTTPCookieProcessor, Request, build_opener
from zoneinfo import ZoneInfo

from .base import Meter, Provider, ProviderAuthError, ProviderError

log = logging.getLogger(__name__)

BASE_URL = "https://meine.netze-bw.de"
AUTH_URL = "https://login.netze-bw.de"
# identifies the login widget to Auth0, the login page sends the same value
AUTH0_CLIENT = "eyJuYW1lIjoibG9jay5qcy11bHAiLCJ2ZXJzaW9uIjoiMTEuMTcuMyIsImVudiI6eyJhdXRoMC5qcy11bHAiOiI5LjExLjIifX0="
PORTAL_TZ = ZoneInfo("Europe/Berlin")
VALUE_TYPES = {"import": "CONSUMPTION", "export": "FEEDIN"}
ENERGY_COLUMN = {"import": "verbrauch", "export": "einspeisung"}
DIRECTION = {"import": "Bezug", "export": "Einspeisung"}
CHUNK_DAYS = 7  # quarter hours of a week per request
PAUSE_S = 0.3  # between requests while catching up, to go easy on the portal
QUARTER = timedelta(minutes=15)


def _user_agent() -> str:
    """An honest user agent: the import is OpenAmpere logging in for the owner, not a browser (#174)."""
    try:
        release = version("openampere")
    except PackageNotFoundError:  # running from source
        release = "dev"
    return f"Mozilla/5.0 (compatible; OpenAmpere/{release}; +https://github.com/Gr33ndev/OpenAmpere)"


USER_AGENT = _user_agent()


class _HttpError(ProviderError):
    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.status = status


class _Form(HTMLParser):
    """The first form of a page: its action and hidden fields."""

    def __init__(self, html: str) -> None:
        super().__init__()
        self.action: str | None = None
        self.hidden: dict[str, str] = {}
        self._inside = False
        self._done = False
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form" and not self._done:
            self._inside, self.action = True, attrs.get("action")
        elif tag == "input" and self._inside and attrs.get("type") == "hidden" and attrs.get("name"):
            self.hidden[attrs["name"]] = attrs.get("value") or ""

    def handle_endtag(self, tag):
        if tag == "form" and self._inside:
            self._inside, self._done = False, True


class NetzeBw(Provider):
    key = "netze_bw"
    label = "Netze BW"
    portal = "meine.netze-bw.de"
    region = "Baden-Württemberg"

    def __init__(self, username: str, password: str, base_url: str = BASE_URL, auth_url: str = AUTH_URL,
                 timeout: float = 30, pause_s: float = PAUSE_S) -> None:
        super().__init__(username, password)
        self.base_url, self.auth_url, self.timeout, self.pause_s = base_url, auth_url, timeout, pause_s
        self._new_session()

    def _new_session(self) -> None:
        self.cookies = CookieJar()
        self.opener = build_opener(HTTPCookieProcessor(self.cookies))
        self.logged_in = False

    # ---- HTTP ----------------------------------------------------------------

    def _open(self, url: str, data: bytes | None = None, headers: dict | None = None) -> tuple[int, str, bytes]:
        request = Request(url, data=data, headers={"User-Agent": USER_AGENT, **(headers or {})})
        try:
            with self.opener.open(request, timeout=self.timeout) as resp:
                return resp.status, resp.geturl(), resp.read()
        except HTTPError as err:
            return err.code, err.geturl(), err.read()
        except (URLError, TimeoutError, OSError) as err:
            raise ProviderError("Das Kundenportal von Netze BW ist gerade nicht erreichbar.") from err

    def _get(self, path: str, params: dict | None, step: str, accept: str, retry: bool = True) -> bytes:
        if not self.logged_in:
            self.login()
        url = self.base_url + path + (f"?{urlencode(params)}" if params else "")
        status, _, body = self._open(url, headers={"x-csrf": "1", "Accept": accept})
        if status in (401, 403):
            log.info("Netze BW %s: HTTP %s%s", step, status, ", logging in again" if retry else "")
            if retry:  # the portal session may still be valid while this service is not: start from scratch
                self._new_session()
                return self._get(path, params, step, accept, retry=False)
            raise ProviderError(f"Netze BW gibt die {step} trotz Anmeldung nicht frei (Fehler {status}). "
                                "OpenAmpere versucht es später nochmal.")
        if status >= 400:
            log.info("Netze BW %s: HTTP %s", step, status)
            raise _HttpError(f"Netze BW, {step}: Fehler {status}.", status)
        return body

    def _json(self, path: str, params: dict | None, step: str):
        try:
            return json.loads(self._get(path, params, step, "application/json"))
        except ValueError as err:
            raise ProviderError(f"Netze BW, {step}: unerwartete Antwort.") from err

    # ---- login ---------------------------------------------------------------

    def login(self) -> None:
        if not self.username or not self.password:
            raise ProviderAuthError("Bitte E-Mail und Passwort für das Kundenportal eintragen.")
        _, url, body = self._open(f"{self.base_url}/bff/auth/login?returnUrl=/signin?target=%2F")
        host = urlparse(url).hostname
        if host == urlparse(self.base_url).hostname:  # the session is still valid
            self.logged_in = True
            return
        if host != urlparse(self.auth_url).hostname:
            raise ProviderError("Die Anmeldeseite von Netze BW hat sich geändert. Bitte ein Issue öffnen.")

        page = _Form(body.decode("utf-8", "replace"))
        csrf = page.hidden.get("_csrf") or next((c.value for c in self.cookies if c.name == "_csrf"), "")
        query = parse_qs(urlparse(url).query)
        one = lambda key: (query.get(key) or [""])[0]  # noqa: E731
        payload = {
            "client_id": one("client"), "redirect_uri": one("redirect_uri"), "tenant": "netze-bw",
            "response_type": one("response_type"), "scope": one("scope"), "state": one("state"), "nonce": one("nonce"),
            "connection": "Username-Password-Authentication", "username": self.username, "password": self.password,
            "popup_options": {}, "sso": True, "_intstate": "deprecated", "_csrf": csrf, "audience": one("audience"),
            "code_challenge_method": one("code_challenge_method"), "code_challenge": one("code_challenge"),
            "protocol": one("protocol"),
        }
        status, answer_url, answer = self._open(
            f"{self.auth_url}/usernamepassword/login", data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "Accept": "*/*", "Auth0-Client": AUTH0_CLIENT,
                     "Origin": self.auth_url, "Referer": url})
        form = _Form(answer.decode("utf-8", "replace"))
        if not form.action or "wresult" not in form.hidden:
            log.info("Netze BW login: HTTP %s without the expected form", status)
            if status >= 500:
                raise ProviderError(f"Die Anmeldung bei Netze BW ist gerade gestört (Fehler {status}).")
            if status in (400, 401, 403):
                raise ProviderAuthError("Netze BW lehnt die Anmeldung ab. Bitte E-Mail und Passwort prüfen.")
            raise ProviderAuthError("Netze BW verlangt bei der Anmeldung einen weiteren Schritt (z. B. Zwei-Faktor oder "
                                    "Captcha). Das kann OpenAmpere nicht.")
        # hand the login back to the portal, it redirects to itself and sets its session cookie
        self._open(urljoin(answer_url, form.action), data=urlencode(form.hidden).encode(),
                   headers={"Content-Type": "application/x-www-form-urlencoded", "Origin": self.auth_url,
                            "Referer": answer_url})
        status, _, _ = self._open(f"{self.base_url}/bff/auth/user", headers={"x-csrf": "1"})
        if status != 200:
            log.info("Netze BW login: session check HTTP %s", status)
            raise ProviderAuthError(f"Die Anmeldung bei Netze BW hat nicht geklappt (Fehler {status}).")
        self.logged_in = True
        log.info("logged in to the Netze BW portal")

    # ---- data ----------------------------------------------------------------

    def meters(self) -> list[Meter]:
        data = self._json("/bff/api/kuposervice/v1/portal/installations", None, "Zählerliste")
        # one entry per physical meter and direction; the same pair may show up in several installations
        chosen: dict[tuple[str, str], dict] = {}
        for item in data.get("installations") or []:
            state = item.get("state")
            state = state.get("code") if isinstance(state, dict) else state
            if item.get("type") != "IMS" or state != "Active" or not isinstance(item.get("id"), str):
                continue  # only active smart meters ("intelligentes Messsystem") have quarter-hour values
            for kind, value_type in VALUE_TYPES.items():
                if value_type in (item.get("valueTypes") or []):
                    key = (item.get("meterId") or item["id"], kind)
                    if key not in chosen or item["id"] < chosen[key]["id"]:
                        chosen[key] = item
        meters: dict[str, Meter] = {}
        for (_, kind), item in sorted(chosen.items(), key=lambda entry: entry[1]["id"]):
            meter = meters.setdefault(item["id"], Meter(id=item["id"], name=item.get("friendlyName") or item.get("meterId")
                                                        or "Smart Meter", kinds=[]))
            meter.kinds.append(kind)
        for meter in meters.values():
            if len(meter.kinds) == 1:  # grid power and feed-in as separate entries: say which one it is
                meter.name = f"{meter.name} · {DIRECTION[meter.kinds[0]]}"
        return sorted(meters.values(), key=lambda m: (m.name, m.id))

    def daily(self, meter: Meter, kind: str, first: date, last: date) -> dict[str, float]:
        """Complete days only, newest first in weekly steps until there are no values (before the smart meter)."""
        result: dict[str, float] = {}
        empty = 0
        end = last
        while end > first:
            start = max(first, end - timedelta(days=CHUNK_DAYS))
            if result and self.pause_s:
                clock.sleep(self.pause_s)
            try:
                days = self._complete_days(meter, kind, start, end)
            except _HttpError as err:
                if err.status not in (400, 404, 422):
                    raise
                days = {}  # the portal refuses a time before the smart meter existed
            if not days:
                empty += 1
                if result or empty >= 2:
                    break  # nothing older: the smart meter was installed after this week
            result.update(days)
            end = start
        return result

    def _complete_days(self, meter: Meter, kind: str, first: date, last: date) -> dict[str, float]:
        start, end = _local_midnight(first), _local_midnight(last)
        body = self._get(f"/bff/api/imsservice/v1/meters/{quote(meter.id, safe='')}/measurements/download",
                         {"startDate": _utc(start), "endDate": _utc(end)}, "Messwerte", "text/csv,*/*")
        quarters = parse_csv(_decode(body), kind)
        result = {}
        day = first
        while day < last:
            begin, finish = _local_midnight(day), _local_midnight(day + timedelta(days=1))
            expected = int((finish - begin).total_seconds() // 900)  # 92, 96 or 100 with the clock change
            values = [kwh for ts, kwh in quarters.items() if begin <= ts < finish]
            if len(values) == expected:
                result[day.isoformat()] = round(sum(values), 4)
            day += timedelta(days=1)
        return result


def parse_csv(text: str, kind: str) -> dict[datetime, float]:
    """kWh per quarter hour (key: its start, UTC) from the portal's CSV download."""
    reader = csv.DictReader(io.StringIO(text), delimiter=";")
    if not reader.fieldnames:
        return {}  # nothing published for this time
    fields = {n.strip().lower(): n for n in reader.fieldnames or [] if n}
    if "datum" not in fields or "uhrzeit" not in fields:
        raise ProviderError("Netze BW, Messwerte: die Datei hat ein unbekanntes Format. Bitte ein Issue öffnen.")
    energy = [orig for low, orig in fields.items() if low not in ("datum", "uhrzeit") and "zaehlerstand" not in low
              and "zählerstand" not in low and "status" not in low]
    energy = [n for n in energy if n.lower().startswith(ENERGY_COLUMN[kind])] or energy
    if not energy:
        raise ProviderError("Netze BW, Messwerte: keine Spalte mit Energiewerten gefunden. Bitte ein Issue öffnen.")
    column = energy[0]
    factor = 0.001 if "(wh)" in column.lower() or "[wh]" in column.lower() else 1.0

    result: dict[datetime, float] = {}
    previous: datetime | None = None
    for row in reader:
        try:
            local = datetime.strptime(f"{row[fields['datum']].strip()} {row[fields['uhrzeit']].strip()}", "%d.%m.%Y %H:%M")
        except (ValueError, AttributeError):
            continue
        moment = _to_utc(local, previous)
        value = _number(row.get(column))
        # the first row is the reading at the start, every later row the energy of the quarter hour up to its time
        if previous is not None and value is not None and moment - previous == QUARTER:
            result[previous] = value * factor
        previous = moment
    return result


def _to_utc(local: datetime, previous: datetime | None) -> datetime:
    """German wall time to UTC; in the hour that repeats when the clock goes back, the one after the previous row."""
    options = sorted({local.replace(tzinfo=PORTAL_TZ, fold=f).astimezone(timezone.utc) for f in (0, 1)})
    if previous is not None:
        later = [o for o in options if o > previous]
        if later:
            return later[0]
    return options[0]


def _number(value) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def _decode(body: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return body.decode(encoding)
        except UnicodeDecodeError:
            continue
    return body.decode("utf-8", "replace")


def _local_midnight(day: date) -> datetime:
    return datetime.combine(day, time.min, PORTAL_TZ).astimezone(timezone.utc)


def _utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
