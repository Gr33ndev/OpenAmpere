"""Netze BW (Baden-Württemberg): daily values of the smart meters from the customer portal meine.netze-bw.de.

There is no documented API. The portal's web app reads JSON from its own backend (a "backend for frontend" behind
/bff), after a login through Auth0 with e-mail and password. The endpoints and the login flow follow the MIT-licensed
Home Assistant integration https://github.com/cygnusb/ha-netze-bw (facts only, no code taken over).
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, time, timedelta, timezone
from html.parser import HTMLParser
from http.cookiejar import CookieJar
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
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
PORTAL_TZ = ZoneInfo("Europe/Berlin")
VALUE_TYPES = {"import": "CONSUMPTION", "export": "FEEDIN"}
CHUNK_DAYS = 90  # daily values per request


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
                 timeout: float = 30) -> None:
        super().__init__(username, password)
        self.base_url, self.auth_url, self.timeout = base_url, auth_url, timeout
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

    def _json(self, path: str, params: dict | None = None, retry: bool = True):
        if not self.logged_in:
            self.login()
        url = self.base_url + path + (f"?{urlencode(params)}" if params else "")
        status, _, body = self._open(url, headers={"x-csrf": "1", "Accept": "application/json"})
        if status == 401 and retry:  # session expired
            self.logged_in = False
            return self._json(path, params, retry=False)
        if status >= 400:
            raise ProviderError(f"Das Kundenportal von Netze BW antwortet mit Fehler {status}.")
        try:
            return json.loads(body)
        except ValueError as err:
            raise ProviderError("Unerwartete Antwort vom Kundenportal von Netze BW.") from err

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
            raise ProviderAuthError("Die Anmeldung bei Netze BW hat nicht geklappt.")
        self.logged_in = True
        log.info("logged in to the Netze BW portal")

    # ---- data ----------------------------------------------------------------

    def meters(self) -> list[Meter]:
        data = self._json("/bff/api/kuposervice/v1/portal/installations")
        found: dict[str, tuple[str, Meter]] = {}
        for item in data.get("installations") or []:
            state = item.get("state")
            state = state.get("code") if isinstance(state, dict) else state
            if item.get("type") != "IMS" or state != "Active" or not isinstance(item.get("id"), str):
                continue  # only active smart meters ("intelligentes Messsystem") have daily values
            kinds = [kind for kind, value_type in VALUE_TYPES.items() if value_type in (item.get("valueTypes") or [])]
            if not kinds:
                continue
            physical = item.get("meterId") or item["id"]  # one meter may show up in several installations
            if physical in found and found[physical][0] < item["id"]:
                continue
            found[physical] = (item["id"], Meter(id=item["id"], name=item.get("friendlyName") or item.get("meterId")
                                                 or "Zähler", kinds=kinds))
        return sorted((m for _, m in found.values()), key=lambda m: m.name)

    def daily(self, meter: Meter, kind: str, first: date, last: date) -> dict[str, float]:
        result: dict[str, float] = {}
        chunk = first
        while chunk < last:
            end = min(last, chunk + timedelta(days=CHUNK_DAYS))
            data = self._json(f"/bff/api/imsservice/v1/meters/{quote(meter.id, safe='')}/measurements", {
                "valueType": VALUE_TYPES[kind], "startDate": _utc(chunk), "endDate": _utc(end), "filter": "1DAY"})
            for item in data.get("measurements") or []:
                value, when = item.get("value"), item.get("startDatetime") or item.get("date")
                if value is None or not when:
                    continue
                moment = datetime.fromisoformat(str(when).replace("Z", "+00:00"))
                day = (moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)).astimezone(PORTAL_TZ).date()
                if first <= day < last:
                    factor = 0.001 if str(item.get("unit") or "").lower() == "wh" else 1.0
                    result[day.isoformat()] = result.get(day.isoformat(), 0.0) + float(value) * factor
            chunk = end
        return result


def _utc(day: date) -> str:
    return datetime.combine(day, time.min, PORTAL_TZ).astimezone(timezone.utc).isoformat(timespec="milliseconds") \
        .replace("+00:00", "Z")
