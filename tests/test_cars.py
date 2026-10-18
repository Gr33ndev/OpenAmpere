# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Cars and their manufacturer's data (#401): the EU Data Act client against a fake portal, the cars in the settings,
the background look at the source and the charges at a wallbox OpenAmpere controls.

The fake portal behaves like the real one as evcc describes it: a form login at the identity service that ends on the
portal's user page with a session cookie, now and then a marketing consent page, a consent page until the owner
agreed in a browser, 404 until a data request was started or a first dataset delivered, placeholders while the car
sleeps, and zip archives with a JSON document of data points.
"""

import asyncio
import io
import json
import sqlite3
import threading
import zipfile
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlparse

import pytest
from fastapi.testclient import TestClient

from openampere import cars as cars_module
from openampere import privacy
from openampere.api import create_app
from openampere.cars import Cars, CarData, Delivery, Source, SourceAuthError, SourceSetupError, validate
from openampere.cars.vw_euda import VwEuDataAct, credentials_page, interpret, points, read_dataset
from openampere.devices import Devices
from openampere.runtime import Runtime
from openampere.storage import Storage

from conftest import login

VIN = "TESTVIN0000000001"
EMAIL = "owner@example.org"
PASSWORD = "richtig"
T0 = datetime(2026, 7, 1, 8, 0, tzinfo=timezone.utc).timestamp()


def dataset(values: dict, *, vin: str = VIN) -> bytes:
    """A dataset as the portal delivers it: a zip archive with one JSON document."""
    data = [{"key": k if "-" in k else "", "dataFieldName": "" if "-" in k else k, "value": str(v),
             "timestampUtc": "2026-07-01T07:00:00Z"} for k, v in values.items()]
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as packed:
        packed.writestr("data.json", json.dumps({"vin": vin, "Data": data}))
    return out.getvalue()


def stamp(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y%m%d%H%M%S")


class Portal:
    """Fake of the EU Data Act portal (served as 127.0.0.1) and the identity service (served as localhost)."""

    def __init__(self) -> None:
        self.marketing = False
        self.consent = True  # the owner agreed in the browser
        self.request = True  # and started a data request
        self.files: list[tuple[float, str, bytes]] = []
        self.sessions: set[str] = set()
        self.logins = 0
        self.downloads: list[str] = []
        self.requested: list[str] = []
        self.foreign_redirect = False
        self.form_action = "/signin-service/v1/c1/login/identifier"
        self.post_action = "login/authenticate"
        self.identifier_url = "login/identifier"
        portal = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status, body=b"", headers=None, kind="text/html"):
                body = json.dumps(body).encode() if kind == "json" else body.encode() if isinstance(body, str) else body
                self.send_response(status)
                self.send_header("Content-Type", "application/json" if kind == "json" else kind)
                for name, value in (headers or {}).items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _sid(self):
                cookies = dict(c.strip().split("=", 1) for c in (self.headers.get("Cookie") or "").split(";") if "=" in c)
                return cookies.get("session")

            def _form(self):
                length = int(self.headers.get("Content-Length") or 0)
                return {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode()).items()}

            def do_GET(self):
                host, url = self.headers["Host"].split(":")[0], urlparse(self.path)
                query = {k: v[0] for k, v in parse_qs(url.query).items()}
                portal.requested.append(f"{host}{url.path}")
                if host == "localhost":
                    if url.path == "/oidc/v1/authorize":
                        assert query["redirect_uri"] == f"{portal.base}/login" and query["prompt"] == "login"
                        return self._send(302, headers={"Location": f"{portal.identity}/signin-service/v1/signin/c1"})
                    if url.path == "/signin-service/v1/signin/c1":
                        return self._send(200, '<html><form id="other" action="/x"></form>'
                                               f'<form id="emailPasswordForm" action="{portal.form_action}">'
                                               '<input type="hidden" name="_csrf" value="CSRF1">'
                                               '<input type="hidden" name="relayState" value="R1">'
                                               '<input type="hidden" name="hmac" value="H1">'
                                               '<input type="email" name="email"></form></html>')
                    if url.path == "/oidc/v1/oauth/client/callback":
                        return self._send(302, headers={"Location": f"{portal.base}/login?code=C2"})
                    if url.path.startswith("/consent/marketing/"):
                        raise AssertionError("the marketing page must not be fetched")
                    return self._send(404)
                if url.path == "/login" and query.get("code") in ("C1", "C2"):
                    if not portal.consent:
                        return self._send(302, headers={"Location": f"{portal.base}/consent"})
                    sid = f"s{portal.logins}"
                    portal.sessions.add(sid)
                    return self._send(302, headers={"Location": "/content/euda/de/de/user.html",
                                                    "Set-Cookie": f"session={sid}; Path=/"})
                if url.path.startswith("/content/euda/"):
                    raise AssertionError("the user page must not be fetched")
                if url.path == "/consent":
                    return self._send(200, "<html>consent</html>")
                if self._sid() not in portal.sessions:
                    return self._send(401, {"error": "unauthorized"}, kind="json")
                if url.path == "/proxy_api/consent/me/vehicles":
                    return self._send(200, {"vehicles": [{"vin": VIN, "nickName": "Born"}, {"vin": "kurz"}]}, kind="json")
                prefix = f"/proxy_api/euda-apim/datarequest/vehicles/{VIN}/metadata/partial"
                if url.path == prefix:
                    return self._send(200, {"Identifier": "REQ1"}, kind="json") if portal.request \
                        else self._send(404, {"error": "none"}, kind="json")
                delivery = f"/proxy_api/euda-apim/datadelivery/vehicles/{VIN}/REQ1/"
                if url.path == delivery + "list":
                    assert self.headers.get("type") == "partial"
                    if not portal.files:
                        return self._send(404, {"error": "No files available for this request"}, kind="json")
                    return self._send(200, [{"name": name, "createdOn": datetime.fromtimestamp(ts, timezone.utc)
                                             .isoformat().replace("+00:00", "Z")} for ts, name, _ in portal.files],
                                      kind="json")
                if url.path == delivery + "download":
                    name = self.headers.get("filename")
                    portal.downloads.append(name)
                    found = next((body for _, n, body in portal.files if n == name), None)
                    return self._send(200, found, kind="application/zip") if found else self._send(404)
                return self._send(404)

            def do_POST(self):
                host, url = self.headers["Host"].split(":")[0], urlparse(self.path)
                form = self._form()
                if host == "localhost" and url.path == "/signin-service/v1/c1/login/identifier":
                    assert form == {"_csrf": "CSRF1", "relayState": "R1", "hmac": "H1", "email": EMAIL}
                    error = "" if form["email"] == EMAIL else "unknown"
                    return self._send(200, "<script>window._IDK = {\n  csrf_token: 'CSRF2',\n  templateModel: "
                                           f"{{identifierUrl: '{portal.identifier_url}', postAction: '{portal.post_action}', "
                                           f"relayState: 'R2', hmac: 'H2', error: '{error}',}},\n}};</script>")
                if host == "localhost" and url.path == "/signin-service/v1/c1/login/authenticate":
                    assert form["_csrf"] == "CSRF2" and form["relayState"] == "R2" and form["hmac"] == "H2"
                    if form["password"] != PASSWORD:
                        return self._send(200, "<html>wrong password</html>")
                    portal.logins += 1
                    if portal.foreign_redirect:
                        return self._send(302, headers={"Location": "http://example.invalid/steal"})
                    if portal.marketing:
                        callback = f"{portal.identity}/oidc/v1/oauth/client/callback?scopes=openid cars"
                        return self._send(302, headers={"Location": f"{portal.identity}/consent/marketing/x?"
                                                                    + urlencode({"callback": callback})})
                    return self._send(302, headers={"Location": f"{portal.base}/login?code=C1"})
                return self._send(404)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        port = self.server.server_address[1]
        self.base, self.identity = f"http://127.0.0.1:{port}", f"http://localhost:{port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def add(self, ts: float, values: dict | None) -> None:
        """A dataset delivered at ts; None: the placeholder while the car sleeps."""
        name = f"{stamp(ts)}_{VIN}" + ("_no_content_found.zip" if values is None else ".zip")
        self.files.append((ts, name, b"" if values is None else dataset(values)))

    def client(self, password: str = PASSWORD) -> VwEuDataAct:
        return VwEuDataAct("cupra", EMAIL, password, portal_url=self.base, identity_url=self.identity, timeout=5)


@pytest.fixture
def portal():
    p = Portal()
    yield p
    p.server.shutdown()


# ---- the EU Data Act client ---------------------------------------------------------------------------------------

def test_login_lists_the_cars_of_the_account_without_opening_the_user_page(portal):
    client = portal.client()
    assert client.vehicles() == [{"vin": VIN, "name": "Born"}]  # the entry without a valid VIN is left out
    assert client.logged_in and portal.logins == 1


def test_the_marketing_page_is_skipped_without_consenting(portal):
    portal.marketing = True
    client = portal.client()
    assert client.vehicles()[0]["vin"] == VIN
    assert not any("/consent/marketing" in r for r in portal.requested)


def test_a_wrong_password_is_a_refused_login(portal):
    with pytest.raises(SourceAuthError, match="E-Mail und Passwort"):
        portal.client("falsch").vehicles()


def test_consent_and_data_request_missing_in_the_portal_are_named(portal):
    portal.consent = False
    with pytest.raises(SourceSetupError, match="zustimmen"):
        portal.client().vehicles()
    portal.consent, portal.request = True, False
    with pytest.raises(SourceSetupError, match="keine Datenanfrage"):
        portal.client().fetch(VIN, 0)


def test_a_redirect_to_another_host_is_refused(portal):
    portal.foreign_redirect = True
    with pytest.raises(cars_module.SourceError, match="unerwartete Adresse"):
        portal.client().vehicles()


@pytest.mark.parametrize("changes", [{"form_action": "@example.invalid/x"}, {"form_action": "//example.invalid/x"},
                                     # the password step replaces a part of the address: here the host
                                     {"identifier_url": "localhost", "post_action": "example.invalid"}])
def test_an_address_on_the_login_page_that_names_another_host_is_refused(portal, changes):
    for field, value in changes.items():
        setattr(portal, field, value)
    with pytest.raises(cars_module.SourceError, match="unerwartete Adresse"):
        portal.client().vehicles()
    assert portal.logins == 0  # the password was never sent


def test_fetch_reads_only_new_datasets_and_skips_the_placeholders(portal):
    client = portal.client()
    assert client.fetch(VIN, 0) == Delivery(data=[], newest=None)  # no dataset yet: 404
    for i in range(12):
        portal.add(T0 + i * 900, {"state_of_charge": 40 + i, "mileage": 1000})
    portal.add(T0 + 12 * 900, None)
    first = client.fetch(VIN, 0)
    assert [d.soc for d in first.data] == [44 + i for i in range(8)]  # the latest eight at the first look
    assert first.newest == T0 + 12 * 900  # the placeholder counts for planning the next look
    portal.add(T0 + 13 * 900, {"state_of_charge": 60})
    portal.downloads.clear()
    later = client.fetch(VIN, first.data[-1].ts)
    assert [d.soc for d in later.data] == [60] and len(portal.downloads) == 1


def test_a_dataset_that_cannot_be_read_is_skipped_and_does_not_hold_up_the_next(portal):
    client = portal.client()
    portal.add(T0, {"state_of_charge": 40})
    portal.files.append((T0 + 900, f"{stamp(T0 + 900)}_{VIN}.zip", b"truncated"))
    portal.add(T0 + 1800, {"state_of_charge": 45})
    delivery = client.fetch(VIN, T0)
    assert [(d.ts, d.soc) for d in delivery.data] == [(T0 + 900, None), (T0 + 1800, 45)]
    portal.downloads.clear()
    assert client.fetch(VIN, delivery.data[-1].ts).data == [] and portal.downloads == []  # not tried again


def test_an_ended_session_logs_in_again_once(portal):
    client = portal.client()
    client.vehicles()
    portal.sessions.clear()
    assert client.vehicles()[0]["vin"] == VIN
    assert portal.logins == 2


def test_the_values_of_a_dataset():
    values = points({"Data": [
        {"key": "", "dataFieldName": "state_of_charge", "value": "55", "timestampUtc": "2026-07-01T07:00:00Z"},
        {"key": "", "dataFieldName": "state_of_charge", "value": "50", "timestampUtc": "2026-07-01T06:00:00Z"},
        {"key": "", "dataFieldName": "battery_level_HV.value", "value": "57"},
        {"key": "", "dataFieldName": "cruising_range_primary_engine", "value": "301.5"},
        {"key": "", "dataFieldName": "mileage", "value": "12345"},
        {"key": "", "dataFieldName": "charging_state_report.charging_scenario", "value": "CHARGING_SCENARIO_ACTIVE"},
        {"key": "", "dataFieldName": "settings.target_soc", "value": "80"},
        {"key": "", "dataFieldName": "empty", "value": ""},
    ]})
    assert values["state_of_charge"] == "55"  # the newer of two
    data = interpret(values, T0)
    assert (data.soc, data.range_km, data.odometer_km) == (57, 301.5, 12345)  # the HV battery level comes first
    assert data.plugged and data.charging and data.target_soc == 80
    finished = interpret({"plug_state": "connected", "charging_state": "readyForCharging",
                          "charging_state_report.charging_scenario": "X_FINISHED"}, T0)
    assert finished.plugged and finished.charging is False
    # the battery care threshold is the target only while the care mode is on
    assert interpret({"battery_care_mode.charge_bcam_threshold": "70"}, T0).target_soc is None
    assert interpret({"battery_care_mode.charge_bcam_threshold": "70",
                      "setting.bcam_activation": "BCAM_ACTIVATION_ACTIVATED"}, T0).target_soc == 70
    assert interpret({"state_of_charge": "180"}, T0).soc is None
    assert interpret({"506cb83e-f99f-3af3-bbeb-0429b69a78d9": "33"}, T0).soc == 33  # ID.3: by its GUID


def test_the_password_page_written_like_javascript():
    page = credentials_page("<script>window._IDK = {\n csrf_token: 'a',\n templateModel: {hmac: 'h', "
                            "postAction: 'login/authenticate', identifierUrl: 'login/identifier',},\n};</script>")
    assert page["csrf_token"] == "a" and page["templateModel"]["postAction"] == "login/authenticate"
    with pytest.raises(cars_module.SourceError, match="geändert"):
        credentials_page("<html>no script</html>")


def test_a_dataset_that_unpacks_too_large_is_refused(monkeypatch):
    monkeypatch.setattr("openampere.cars.vw_euda.MAX_DOCUMENT_BYTES", 100)
    with pytest.raises(cars_module.SourceError, match="groß"):
        read_dataset(dataset({f"field{i}": i for i in range(50)}))
    with pytest.raises(cars_module.SourceError):
        read_dataset(b"not a zip")


# ---- the cars in the settings ---------------------------------------------------------------------------------------

GOOD = {"name": "Born", "brand": "cupra", "username": EMAIL, "password": PASSWORD, "vin": VIN}


def test_validation():
    assert validate([GOOD])[0].source == "vw_euda"
    for bad, message in (({"name": ""}, "Namen"), ({"brand": "tesla"}, "Marke"), ({"username": "nobody"}, "E-Mail"),
                         ({"password": ""}, "Passwort"), ({"vin": "WVW?"}, "Fahrzeug"),
                         ({"source": "cloud"}, "Datenquelle"), ({"wallbox_id": "../x"}, "Wallbox")):
        with pytest.raises(ValueError, match=message):
            validate([{**GOOD, **bad}])
    with pytest.raises(ValueError, match="Höchstens"):
        validate([GOOD] * 4)
    assert validate([{**GOOD, "vin": "", "password": "", "id": "ab12cd34"}], keyless=("ab12cd34",))[0].password == ""


class Wallboxes:
    """Stands in for NativeWallboxes: one wallbox and what it read last."""

    def __init__(self) -> None:
        self.wb = SimpleNamespace(id="aa11bb22", name="Garage", enabled=True)
        self.wallboxes = [self.wb]
        self.reading = None
        self.live = {}

    def enabled(self):
        return self.wallboxes

    def fresh(self, wb, now=None):
        return self.reading


def setup_cars(tmp_path, sources=None) -> tuple[Runtime, Cars, Wallboxes]:
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    wallboxes = Wallboxes()
    return runtime, Cars(runtime, wallboxes, sources), wallboxes


def test_the_password_is_stored_encrypted_and_never_shown(tmp_path):
    runtime, cars, _ = setup_cars(tmp_path)
    saved = cars.save([GOOD])
    stored = runtime.storage.get_meta("cars")[0]
    assert PASSWORD not in json.dumps(stored) and stored["password"].startswith("enc:")
    view = cars.view()["cars"][0]
    assert "password" not in view and view["password_set"] and view["vin_end"] == "0001" and "vin" not in view
    public = cars.view(logged_in=False)["cars"][0]
    assert "username" not in public and "vin_end" not in public and "odometer_km" not in public["values"]
    # the app sends password and VIN empty: the stored ones stay
    cars.save([{**GOOD, "id": saved[0].id, "name": "Born 2", "password": "", "vin": ""}])
    assert (cars.cars[0].password, cars.cars[0].vin, cars.cars[0].name) == (PASSWORD, VIN, "Born 2")


def test_another_account_has_to_choose_its_car_again(tmp_path):
    _, cars, _ = setup_cars(tmp_path)
    car = cars.save([GOOD])[0]
    cars.save([{**GOOD, "id": car.id, "password": "", "vin": ""}])
    assert cars.cars[0].vin == VIN  # same account: the car stays
    cars.save([{**GOOD, "id": car.id, "username": "other@example.org", "password": "", "vin": ""}])
    assert cars.cars[0].vin == "" and cars.view()["cars"][0]["vin_set"] is False


def test_one_car_per_wallbox_and_only_wallboxes_that_exist(tmp_path):
    _, cars, _ = setup_cars(tmp_path)
    with pytest.raises(ValueError, match="gibt es nicht"):
        cars.save([{**GOOD, "wallbox_id": "cc33dd44"}])
    with pytest.raises(ValueError, match="nur ein Auto"):
        cars.save([{**GOOD, "wallbox_id": "aa11bb22"}, {**GOOD, "name": "Zweites", "wallbox_id": "aa11bb22"}])


def test_a_car_whose_wallbox_was_removed_can_still_be_saved(tmp_path):
    _, cars, wallboxes = setup_cars(tmp_path)
    car = cars.save([{**GOOD, "wallbox_id": "aa11bb22"}])[0]
    wallboxes.wallboxes = []
    saved = cars.save([{**GOOD, "id": car.id, "name": "Born 2", "wallbox_id": "aa11bb22"}])[0]
    assert (saved.name, saved.wallbox_id) == ("Born 2", "")


def test_a_removed_car_takes_its_data_along(tmp_path):
    runtime, cars, _ = setup_cars(tmp_path)
    car = cars.save([GOOD])[0]
    runtime.storage.add_car_points(car.id, [{"ts": T0, "soc": 50}])
    cars.save([])
    assert runtime.storage.car_last_ts(car.id) is None


# ---- looking at the source in the background ----------------------------------------------------------------------

class FakeSource(Source):
    key, label = "vw_euda", "Fake"
    answers: list = []
    asked: list = []

    def vehicles(self):
        return [{"vin": VIN, "name": "Born"}]

    def fetch(self, vin, after):
        FakeSource.asked.append(after)
        answer = FakeSource.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    @classmethod
    def info(cls):
        return VwEuDataAct.info()


@pytest.fixture
def fake():
    FakeSource.answers, FakeSource.asked = [], []
    return FakeSource


async def test_the_data_is_stored_and_the_next_look_waits_for_the_next_delivery(tmp_path, fake, monkeypatch):
    runtime, cars, _ = setup_cars(tmp_path, {"vw_euda": fake})
    car = cars.save([GOOD])[0]
    now = T0 + 3600
    monkeypatch.setattr("openampere.cars.time.time", lambda: now)
    fake.answers = [Delivery(data=[CarData(ts=T0, soc=50, range_km=250), CarData(ts=T0 + 900)], newest=now - 300)]
    live = await cars.poll(car, cars.live.setdefault(car.id, cars_module.Live()), now)
    assert live.status == "ok" and live.after == T0 + 900
    assert live.next_at == now - 300 + cars_module.NEXT_DELIVERY_S
    assert cars.latest(car.id)["soc"] == 50 and cars.latest(car.id)["soc_ts"] == T0
    # an overdue delivery: looked for again in ten minutes
    fake.answers = [Delivery(data=[], newest=now - 3600)]
    live = await cars.poll(car, live, now)
    assert live.next_at == now + cars_module.LATE_WAIT_S and fake.asked[-1] == T0 + 900


async def test_a_refused_login_is_not_tried_again_by_itself(tmp_path, fake):
    _, cars, _ = setup_cars(tmp_path, {"vw_euda": fake})
    car = cars.save([GOOD])[0]
    fake.answers = [SourceAuthError("Die Anmeldung wurde abgelehnt.")]
    cars.tick(T0)
    await cars.wait()
    live = cars.live[car.id]
    assert live.status == "auth" and live.refused
    live.next_at = 0
    cars.tick(T0 + 86400)
    assert live.task.done() and fake.asked == [0.0]  # no second look
    fake.answers = [Delivery(data=[], newest=None)]
    await cars.refresh(car.id)  # the owner asks: tried again
    assert live.status == "waiting" and live.next_at > T0


async def test_asking_now_while_the_background_look_runs_does_not_start_a_second_one(tmp_path, fake, monkeypatch):
    _, cars, _ = setup_cars(tmp_path, {"vw_euda": fake})
    car = cars.save([GOOD])[0]
    release = asyncio.Event()
    started = []

    async def slow_poll(car, live, now=None):
        started.append(car.id)
        await release.wait()
        return live
    monkeypatch.setattr(cars, "poll", slow_poll)
    refresh = asyncio.create_task(cars.refresh(car.id))
    await asyncio.sleep(0)
    cars.tick(T0)  # due, but the owner's look is running
    release.set()
    await refresh
    assert started == [car.id]


async def test_missing_setup_is_asked_again_less_often(tmp_path, fake, monkeypatch):
    _, cars, _ = setup_cars(tmp_path, {"vw_euda": fake})
    car = cars.save([GOOD])[0]
    monkeypatch.setattr("openampere.cars.time.time", lambda: T0)
    fake.answers = [SourceSetupError("Bitte im Portal zustimmen.")]
    live = await cars.poll(car, cars.live.setdefault(car.id, cars_module.Live()))
    assert live.status == "setup" and live.next_at == T0 + cars_module.SETUP_WAIT_S
    assert cars.view()["cars"][0]["error"] == "Bitte im Portal zustimmen."


def test_a_car_without_a_chosen_vin_is_not_asked(tmp_path, fake):
    _, cars, _ = setup_cars(tmp_path, {"vw_euda": fake})
    cars.save([{**GOOD, "vin": ""}])
    cars.tick(T0)
    assert all(live.task is None for live in cars.live.values())


# ---- charges at the wallbox ------------------------------------------------------------------------------------------

def reading(connected: bool, kwh: float | None = None, lifetime: float | None = None):
    return SimpleNamespace(connected=connected, session_kwh=kwh, lifetime_kwh=lifetime)


def test_a_charge_with_the_state_of_charge_at_plug_in_and_the_distance(tmp_path):
    runtime, cars, wallboxes = setup_cars(tmp_path)
    car = cars.save([{**GOOD, "wallbox_id": "aa11bb22"}])[0]
    storage = runtime.storage
    # a charge two days earlier, then driving
    storage.add_car_points(car.id, [{"ts": T0 - 2 * 86400, "soc": 30, "odometer_km": 1000}])
    earlier = storage.open_car_session(car.id, "aa11bb22", T0 - 2 * 86400 + 600)
    storage.update_car_session(earlier, 20.0, T0 - 2 * 86400 + 7200)
    storage.add_car_points(car.id, [{"ts": T0 - 3600, "soc": 42, "odometer_km": 1180, "plugged": 0}])
    wallboxes.reading = reading(True, 0.0)
    cars.track(T0)
    for i, kwh in enumerate((1.0, 2.5, 0.4, 1.4), 1):  # the session counter starts anew once
        wallboxes.reading = reading(True, kwh)
        cars.track(T0 + i * 60)
    # the portal delivers late: the state of charge after the charge arrives after the unplugging
    wallboxes.reading = reading(False, 1.4)
    cars.track(T0 + 3 * 3600)
    storage.add_car_points(car.id, [{"ts": T0 + 3 * 3600 + 900, "soc": 70, "odometer_km": 1180}])
    view = cars.view(now=T0 + 4 * 3600)["cars"][0]["charge"]
    assert view["kwh"] == pytest.approx(3.9) and view["end"] == T0 + 3 * 3600
    assert (view["soc_start"], view["soc_end"], view["km_before"], view["km_since"]) == (42, 70, 180, 0)
    sessions = cars.sessions()
    assert sessions[0]["soc_start"] == 42 and sessions[0]["odometer_km"] == 1180 and sessions[0]["vehicle"] == "Born"
    assert sessions[0]["created"].endswith("Z") and sessions[0]["duration_s"] == 3 * 3600
    assert sessions[1]["energy_kwh"] == 20.0 and sessions[1]["odometer_km"] == 1000


def test_the_state_of_charge_at_the_end_is_the_last_one_before_unplugging(tmp_path):
    runtime, cars, wallboxes = setup_cars(tmp_path)
    car = cars.save([{**GOOD, "wallbox_id": "aa11bb22"}])[0]
    wallboxes.reading = reading(True, 0.0)
    cars.track(T0)
    wallboxes.reading = reading(False, 10.0)
    cars.track(T0 + 3600)
    # 80 % just before unplugging, then the car drives away and a later delivery says 77 %
    runtime.storage.add_car_points(car.id, [{"ts": T0 + 3000, "soc": 80}, {"ts": T0 + 3600 + 900, "soc": 77}])
    assert cars.view()["cars"][0]["charge"]["soc_end"] == 80


def test_a_running_charge_is_left_out_of_the_statistics(tmp_path):
    runtime, cars, wallboxes = setup_cars(tmp_path)
    car = cars.save([{**GOOD, "wallbox_id": "aa11bb22"}])[0]
    ended = runtime.storage.open_car_session(car.id, "aa11bb22", T0 - 86400)
    runtime.storage.update_car_session(ended, 20.0, T0 - 80000)
    wallboxes.reading = reading(True, 0.0)
    cars.track(T0)
    wallboxes.reading = reading(True, 3.0)
    cars.track(T0 + 600)
    assert [s["energy_kwh"] for s in cars.sessions()] == [20.0]  # the running one only on the card
    assert cars.view()["cars"][0]["charge"]["end"] is None


def test_ended_charges_keep_their_values_when_old_deliveries_are_pruned(tmp_path):
    runtime, cars, wallboxes = setup_cars(tmp_path)
    car = cars.save([{**GOOD, "wallbox_id": "aa11bb22"}])[0]
    storage = runtime.storage
    storage.add_car_points(car.id, [{"ts": T0 - 3600, "soc": 40, "odometer_km": 5000},
                                    {"ts": T0 + 3000, "soc": 80, "odometer_km": 5000}])
    charge = storage.open_car_session(car.id, "aa11bb22", T0)
    storage.update_car_session(charge, 23.0, T0 + 3600)
    cars.prune(T0 + 3600 + 600)  # too early: late deliveries may still come
    assert storage.car_sessions(car.id)[0]["settled"] == 0
    later = T0 + cars_module.KEEP_POINTS_S + 86400
    storage.add_car_points(car.id, [{"ts": later - 600, "soc": 61, "odometer_km": 7000}])
    cars.prune(later)
    assert storage.car_last_ts(car.id) == later - 600  # old deliveries gone, recent ones kept
    session = cars.sessions()[0]
    assert (session["soc_start"], session["soc_end"], session["odometer_km"]) == (40, 80, 5000)


def test_data_of_cars_that_are_no_longer_set_up_is_removed(tmp_path):
    runtime, cars, _ = setup_cars(tmp_path)
    car = cars.save([GOOD])[0]
    runtime.storage.add_car_points("dead0001", [{"ts": T0, "soc": 50}])  # an entry the settings could not read
    runtime.storage.add_car_points(car.id, [{"ts": T0, "soc": 51}])
    cars.save([{**GOOD, "id": car.id, "password": ""}])
    assert runtime.storage.car_last_ts("dead0001") is None and runtime.storage.car_last_ts(car.id) == T0


def test_a_charge_ends_when_the_wallbox_stays_away(tmp_path):
    runtime, cars, wallboxes = setup_cars(tmp_path)
    car = cars.save([{**GOOD, "wallbox_id": "aa11bb22"}])[0]
    wallboxes.reading = reading(True, lifetime=100.0)
    cars.track(T0)
    wallboxes.reading = reading(True, lifetime=103.0)
    cars.track(T0 + 60)
    wallboxes.reading = None
    wallboxes.live["aa11bb22"] = SimpleNamespace(online=False, offline_since=T0 + 120)
    cars.track(T0 + 600)
    assert runtime.storage.car_sessions(car.id)[0]["end"] is None  # not yet: it may answer again
    cars.track(T0 + 120 + cars_module.WALLBOX_GONE_S + 1)
    row = runtime.storage.car_sessions(car.id)[0]
    assert row["end"] == T0 + 120 and row["kwh"] == 3.0


def test_a_running_charge_goes_on_after_a_restart(tmp_path):
    runtime, cars, wallboxes = setup_cars(tmp_path)
    car = cars.save([{**GOOD, "wallbox_id": "aa11bb22"}])[0]
    wallboxes.reading = reading(True, 0.5)
    cars.track(T0)
    wallboxes.reading = reading(True, 2.0)
    cars.track(T0 + 60)
    again = Cars(runtime, wallboxes)  # a restart: what was charged meanwhile still counts
    wallboxes.reading = reading(True, 2.5)
    again.track(T0 + 120)
    again.track(T0 + 180)
    rows = runtime.storage.car_sessions(car.id)
    assert len(rows) == 1 and rows[0]["end"] is None and rows[0]["kwh"] == 2.0 and rows[0]["counter"] == 2.5


def test_the_wallbox_shows_the_state_of_charge_only_while_the_car_is_plugged_in(tmp_path, monkeypatch):
    runtime, cars, wallboxes = setup_cars(tmp_path)
    car = cars.save([{**GOOD, "wallbox_id": "aa11bb22"}])[0]
    runtime.storage.add_car_points(car.id, [{"ts": T0, "soc": 64, "range_km": 280}])
    assert cars.for_wallbox("aa11bb22", T0 + 600) == {"car": "Born", "soc": 64, "range_km": 280, "soc_ts": T0}
    assert cars.for_wallbox("aa11bb22", T0 + 3 * 86400) is None  # too old to show
    assert cars.for_wallbox("cc33dd44", T0) is None

    wb = SimpleNamespace(id="aa11bb22", name="Garage", enabled=True)
    wallboxes.wallboxes = [wb]
    wallboxes.reading = SimpleNamespace(connected=True, charging=True, power_w=7000.0)
    devices = Devices(runtime, SimpleNamespace(consumers=[]), SimpleNamespace(fresh=lambda: None), wallboxes, cars)
    monkeypatch.setattr("openampere.cars.time.time", lambda: T0 + 600)
    item = next(d for d in devices.live() if d["key"] == "w:aa11bb22")
    assert (item["soc"], item["range_km"], item["soc_ts"]) == (64, 280, T0)
    wallboxes.reading = SimpleNamespace(connected=False, charging=False, power_w=0.0)
    item = next(d for d in devices.live() if d["key"] == "w:aa11bb22")
    assert item["soc"] is None


# ---- database, API and report --------------------------------------------------------------------------------------

def test_the_car_tables_come_with_the_upgrade_of_an_existing_database(tmp_path):
    path = tmp_path / "t.db"
    Storage(path).close()
    db = sqlite3.connect(path)
    db.execute("DROP TABLE car_points")
    db.execute("DROP TABLE car_sessions")
    db.execute("PRAGMA user_version=4")
    db.commit()
    db.close()
    storage = Storage(path)
    storage.add_car_points("ab12cd34", [{"ts": T0, "soc": 50}])
    assert storage.car_value("ab12cd34", "soc")["value"] == 50
    assert storage._db.execute("PRAGMA user_version").fetchone()[0] == 5


def test_the_api_needs_a_login_for_changes_and_the_charges(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAMPERE_DATA_DIR", str(tmp_path))
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    client = TestClient(create_app(runtime))
    client.headers.update({"x-openampere": "1"})
    assert client.get("/api/cars").json()["cars"] == []
    login(client)
    assert client.put("/api/cars", json={"cars": [GOOD]}).status_code == 200
    assert client.put("/api/cars", json={"cars": [{**GOOD, "brand": "x"}]}).status_code == 400
    assert client.get("/api/cars/sessions").json() == {"sessions": []}
    assert client.post("/api/cars/vehicles", json={"source": "vw_euda", "brand": "cupra", "username": "x",
                                                   "password": "y"}).status_code == 400
    client.post("/api/auth/logout")
    assert client.get("/api/cars/sessions").status_code == 401
    assert client.put("/api/cars", json={"cars": []}).status_code == 401
    assert "username" not in client.get("/api/cars").json()["cars"][0]


def test_the_log_in_a_report_has_no_name_login_or_vin_of_a_car(tmp_path):
    runtime, cars, _ = setup_cars(tmp_path)
    cars.save([GOOD])
    pseudonyms = privacy.for_report(runtime)
    text = pseudonyms.text(f"car Born: login {EMAIL} for /vehicles/{VIN}/list refused")
    assert "Born" not in text and EMAIL not in text and VIN not in text and "<Fahrgestellnummer>" in text


def test_the_report_has_no_name_login_or_vin(tmp_path):
    runtime, cars, _ = setup_cars(tmp_path)
    cars.save([GOOD])
    text = json.dumps(cars.report())
    assert VIN not in text and EMAIL not in text and "Born" not in text and PASSWORD not in text
    assert "\n".join(cars_module.report_markdown(cars.report()[0])).startswith("- Quelle: vw_euda")
