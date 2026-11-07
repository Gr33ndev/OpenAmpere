# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""BMW and MINI cars through BMW CarData (#595): the client against a fake CarData service, the login the owner
confirms in a browser, the renewed token stored with the car, and BMW's daily limit.

The fake service behaves like BMW's documentation describes it: a device code with PKCE, "authorization_pending"
until the owner confirmed it, a refresh token that is void once it was used, a container of telematic keys and the
last value of each key with its time stamp.
"""

import base64
import hashlib
import json
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from openampere import cars as cars_module
from openampere.api import create_app
from openampere.cars import Cars, SourceAuthError, SourceError, SourceSetupError, validate
from openampere.cars import bmw_cardata
from openampere.cars.bmw_cardata import BmwCarData, interpret
from openampere.runtime import Runtime
from openampere.storage import Storage

from conftest import login

VIN = "WBATESTVIN0000001"
CLIENT_ID = "0a1b2c3d-4e5f-4a5b-8c7d-0123456789ab"
T0 = datetime(2026, 7, 1, 8, 0, tzinfo=timezone.utc).timestamp()


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


class CarDataService:
    """Fake of BMW's login (customer.bmwgroup.com) and the CarData API on one local server."""

    def __init__(self) -> None:
        self.confirmed = False
        self.challenge = ""
        self.refresh = ""  # the one refresh token that is valid
        self.issued = 0
        self.containers: list[dict] = []
        self.telematic: dict = {}
        self.api_calls: list[str] = []
        self.limit_reached = False
        self.token_fault = False
        service = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status, body=None, headers=None):
                raw = json.dumps(body).encode() if body is not None else b""
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                for name, value in (headers or {}).items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _body(self) -> bytes:
                return self.rfile.read(int(self.headers.get("Content-Length") or 0))

            def _tokens(self):
                service.issued += 1
                service.refresh = f"R{service.issued}"
                return self._send(200, {"access_token": f"A{service.issued}", "token_type": "Bearer",
                                        "expires_in": 3600, "refresh_token": service.refresh, "id_token": "I",
                                        "gcid": "G1", "scope": bmw_cardata.SCOPE})

            def do_POST(self):
                path = urlparse(self.path).path
                if path.startswith("/gcdm/"):
                    form = {k: v[0] for k, v in parse_qs(self._body().decode()).items()}
                    assert form["client_id"] == CLIENT_ID
                    if path == "/gcdm/oauth/device/code":
                        assert form["code_challenge_method"] == "S256" and form["response_type"] == "device_code"
                        assert form["scope"] == "authenticate_user openid cardata:api:read"
                        service.challenge = form["code_challenge"]
                        return self._send(200, {"user_code": "ABCD-EFGH", "device_code": "D1", "interval": 5,
                                                "expires_in": 300,
                                                "verification_uri": "https://customer.example.org/oauth/device",
                                                "verification_uri_complete":
                                                    "https://customer.example.org/oauth/device?user_code=ABCD-EFGH"})
                    if form["grant_type"] == "urn:ietf:params:oauth:grant-type:device_code":
                        digest = hashlib.sha256(form["code_verifier"].encode()).digest()
                        assert base64.urlsafe_b64encode(digest).rstrip(b"=").decode() == service.challenge
                        if service.token_fault:
                            return self._send(500, {"fault": {"faultstring": "Invalid Access Token", "detail": {
                                "errorcode": "keymanagement.service.invalid_access_token"}}})
                        if not service.confirmed:
                            return self._send(400, {"error": "authorization_pending"})
                        return self._tokens()
                    if form["grant_type"] == "refresh_token":
                        if form["refresh_token"] != service.refresh:
                            return self._send(400, {"error": "invalid_grant"})
                        return self._tokens()
                    return self._send(400, {"error": "unsupported_grant_type"})
                if not self._api():
                    return None
                if path == "/customers/containers":
                    body = json.loads(self._body())
                    assert body["name"] == "OpenAmpere" and VIN not in json.dumps(body)
                    container = {"containerId": f"C{len(service.containers):012d}", "name": body["name"],
                                 "purpose": body["purpose"], "state": "ACTIVE"}
                    service.containers.append(container)
                    return self._send(201, container)
                return self._send(404)

            def _api(self) -> bool:
                service.api_calls.append(urlparse(self.path).path)
                assert self.headers.get("x-version") == "v1"
                if self.headers.get("Authorization") != f"Bearer A{service.issued}":
                    self._send(401, {"exveErrorId": "CU-102"})
                    return False
                if service.limit_reached:
                    self._send(429, {"exveErrorId": "CU-429"})
                    return False
                return True

            def do_GET(self):
                url = urlparse(self.path)
                if not self._api():
                    return None
                if url.path == "/customers/vehicles/mappings":
                    return self._send(200, [{"vin": VIN, "mappingType": "PRIMARY"},
                                            {"vin": "WBASECONDARY00002", "mappingType": "SECONDARY"}])
                if url.path == "/customers/containers":
                    return self._send(200, {"containers": service.containers})
                if url.path == f"/customers/vehicles/{VIN}/telematicData":
                    container = parse_qs(url.query)["containerId"][0]
                    if container not in {c["containerId"] for c in service.containers}:
                        return self._send(404, {"exveErrorId": "CU-121"})
                    return self._send(200, {"telematicData": service.telematic})
                if url.path == "/redirect":
                    return self._send(302, headers={"Location": "http://elsewhere.example.org/"})
                return self._send(404)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def values(self, ts: float, **values) -> None:
        keys = {"soc": "vehicle.drivetrain.batteryManagement.header",
                "range": "vehicle.drivetrain.electricEngine.kombiRemainingElectricRange",
                "odometer": "vehicle.vehicle.travelledDistance", "port": "vehicle.body.chargingPort.status",
                "status": "vehicle.drivetrain.electricEngine.charging.status",
                "target": "vehicle.powertrain.electric.battery.stateOfCharge.target"}
        for name, value in values.items():
            self.telematic[keys[name]] = {"timestamp": iso(ts), "unit": "", "value": str(value)}

    def login(self) -> str:
        """A login as OpenAmpere keeps it, as if the owner had confirmed it just now."""
        self.issued += 1
        self.refresh = f"R{self.issued}"
        return json.dumps({"refresh_token": self.refresh, "gcid": "G1"})


@pytest.fixture
def bmw(monkeypatch):
    service = CarDataService()
    monkeypatch.setattr(bmw_cardata, "AUTH_URL", service.base)
    monkeypatch.setattr(bmw_cardata, "API_URL", service.base)
    yield service
    service.server.shutdown()


def setup_cars(tmp_path) -> tuple[Runtime, Cars]:
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    return runtime, Cars(runtime)


CAR = {"name": "Cooper", "source": "bmw_cardata", "brand": "mini", "username": CLIENT_ID, "vin": VIN}


# ---- the values -----------------------------------------------------------------------------------------------

def test_the_values_newer_than_the_last_look_make_one_delivery_per_time_stamp():
    telematic = {
        "vehicle.drivetrain.batteryManagement.header": {"timestamp": iso(T0 + 600), "value": "64"},
        "vehicle.powertrain.electric.battery.stateOfCharge.displayed": {"timestamp": iso(T0 + 900), "value": "70"},
        "vehicle.drivetrain.electricEngine.kombiRemainingElectricRange": {"timestamp": iso(T0 + 600), "value": "180"},
        "vehicle.vehicle.travelledDistance": {"timestamp": iso(T0 - 600), "value": "12000"},
        "vehicle.body.chargingPort.status": {"timestamp": iso(T0 + 600), "value": "DISCONNECTED"},
        "vehicle.drivetrain.electricEngine.charging.status": {"timestamp": iso(T0 + 1200), "value": "CHARGINGACTIVE"},
        "vehicle.powertrain.electric.battery.stateOfCharge.target": {"timestamp": iso(T0 + 600), "value": "nan"},
    }
    data, newest = interpret(telematic, after=T0, now=T0 + 3600)
    assert newest == T0 + 1200
    # the header wins over "displayed"; the odometer is older than the last look; the charge state has its own time
    assert [(d.ts, d.soc, d.range_km, d.plugged, d.charging, d.odometer_km, d.target_soc) for d in data] == [
        (T0 + 600, 64, 180, False, None, None, None), (T0 + 1200, None, None, True, True, None, None)]
    # nothing new: nothing stored again
    assert interpret(telematic, after=T0 + 1200, now=T0 + 3600) == ([], T0 + 1200)


def test_a_time_stamp_in_the_future_counts_as_now_and_an_unknown_state_is_not_charging():
    telematic = {"vehicle.drivetrain.batteryManagement.header": {"timestamp": iso(T0 + 7200), "value": "101"},
                 "vehicle.drivetrain.lastRemainingRange": {"timestamp": iso(T0), "value": "90"},
                 "vehicle.drivetrain.electricEngine.charging.hvStatus": {"timestamp": iso(T0), "value": "SOMETHING"},
                 "vehicle.body.chargingPort.combinedStatus": {"timestamp": "kaputt", "value": "CONNECTED"}}
    data, newest = interpret(telematic, after=0, now=T0 + 60)
    assert newest == T0 + 60
    assert [(d.ts, d.soc, d.range_km, d.plugged, d.charging) for d in data] == [(T0, None, 90, None, False)]


def test_unplugging_after_a_charge_ends_the_charge():
    def at(ts, status, port):
        return {"vehicle.drivetrain.electricEngine.charging.status": {"timestamp": iso(ts), "value": status},
                "vehicle.body.chargingPort.status": {"timestamp": iso(ts), "value": port}}
    charging, _ = interpret(at(T0, "CHARGINGACTIVE", "CONNECTED"), after=0, now=T0 + 60)
    assert [(d.plugged, d.charging) for d in charging] == [(True, True)]
    unplugged, _ = interpret(at(T0 + 600, "NOCHARGING", "DISCONNECTED"), after=T0, now=T0 + 660)
    assert [(d.plugged, d.charging) for d in unplugged] == [(False, False)]


def test_an_older_known_hv_status_does_not_hide_a_newer_unknown_status():
    status, hv = "vehicle.drivetrain.electricEngine.charging.status", "vehicle.drivetrain.electricEngine.charging.hvStatus"
    charging = {status: {"timestamp": iso(T0), "value": "CHARGINGACTIVE"}, hv: {"timestamp": iso(T0), "value": "CHARGING"}}
    data, _ = interpret(charging, after=0, now=T0 + 60)
    assert [d.charging for d in data] == [True]
    unplugged = {**charging, status: {"timestamp": iso(T0 + 600), "value": "NOCHARGING"}}
    data, _ = interpret(unplugged, after=T0, now=T0 + 660)
    assert [(d.ts, d.plugged, d.charging) for d in data] == [(T0 + 600, None, False)]


def test_an_unknown_charging_status_falls_back_to_the_hv_status():
    telematic = {"vehicle.drivetrain.electricEngine.charging.status": {"timestamp": iso(T0), "value": "NEWVALUE"},
                 "vehicle.drivetrain.electricEngine.charging.hvStatus": {"timestamp": iso(T0), "value": "CHARGING"}}
    data, _ = interpret(telematic, after=0, now=T0 + 60)
    assert [(d.plugged, d.charging) for d in data] == [(True, True)]


def test_validation_of_the_client_id():
    assert validate([{**CAR, "password": "x"}])[0].username == CLIENT_ID
    with pytest.raises(ValueError, match="Client-ID"):
        validate([{**CAR, "username": "owner@example.org", "password": "x"}])
    with pytest.raises(ValueError, match="Anmeldung im Browser"):
        validate([{**CAR, "password": ""}])


# ---- the login in the owner's browser ----------------------------------------------------------------------------

async def test_the_login_confirmed_in_the_browser_is_kept_and_its_renewal_stored(tmp_path, bmw, monkeypatch):
    runtime, cars = setup_cars(tmp_path)
    started = await cars.start_login("bmw_cardata", "mini", CLIENT_ID)
    assert started["code"] == "ABCD-EFGH" and started["url"].startswith("https://")
    assert "device_code" not in started and "verifier" not in started
    pending = cars._logins[started["login"]]
    pending.next_at = 0
    assert await cars.finish_login(started["login"]) is False  # not yet confirmed
    assert await cars.finish_login(started["login"]) is False  # asked too soon: BMW is not asked again
    bmw.confirmed = True
    pending.next_at = 0
    assert await cars.finish_login(started["login"]) is True
    # choosing the car renews the token; the renewed one is stored with the car
    assert await cars.vehicles("bmw_cardata", "mini", CLIENT_ID, "", login=started["login"]) == [
        {"vin": VIN, "name": ""}]
    car = cars.save([{**CAR, "login": started["login"], "password": "from the app"}])[0]
    assert car.password == json.dumps({"refresh_token": bmw.refresh, "gcid": "G1"})
    assert bmw.refresh not in json.dumps(runtime.storage.get_meta("cars"))
    assert started["login"] not in cars._logins
    # a look after the access token ended: renewed again, the new one is stored, the old one is void at BMW
    bmw.values(T0, soc=55)
    monkeypatch.setattr(bmw_cardata.time, "time", lambda: T0 + 7200)
    live = await cars.poll(car, cars.live.setdefault(car.id, cars_module.Live()), T0 + 7200)
    client = cars._clients[("bmw_cardata", "mini", CLIENT_ID, cars.cars[0].password)][0]
    client._access = None
    client._last_look.clear()
    live = await cars.poll(cars.cars[0], live, T0 + 7200)
    assert live.status == "ok" and cars.latest(car.id)["soc"] == 55
    assert json.loads(cars.cars[0].password)["refresh_token"] == bmw.refresh
    assert live.next_at == T0 + 7200 + bmw_cardata.EVERY_S


async def test_a_password_from_the_app_is_never_taken_as_the_login(tmp_path, bmw):
    _, cars = setup_cars(tmp_path)
    with pytest.raises(ValueError, match="Anmeldung im Browser"):
        cars.save([{**CAR, "password": json.dumps({"refresh_token": "made up"})}])
    with pytest.raises(ValueError, match="Anmeldung im Browser"):
        await cars.vehicles("bmw_cardata", "mini", CLIENT_ID, "made up")
    # a stored login stays when the car is saved again with the same client ID, also if the app sends a password;
    # another client ID needs a new login
    cars.runtime.storage.set_meta("cars", [{**CAR, "id": "ab12cd34", "password": bmw.login()}])
    stored = cars.cars[0].password
    cars.save([{**CAR, "id": "ab12cd34", "name": "Cooper SE", "password": "made up"}])
    assert (cars.cars[0].password, cars.cars[0].name) == (stored, "Cooper SE")
    with pytest.raises(ValueError, match="Anmeldung im Browser"):
        cars.save([{**CAR, "id": "ab12cd34", "username": "1a1b2c3d-0000"}])


async def test_the_login_that_bmw_does_not_hand_out_names_the_fix(tmp_path, bmw):
    _, cars = setup_cars(tmp_path)
    started = await cars.start_login("bmw_cardata", "bmw", CLIENT_ID)
    cars._logins[started["login"]].next_at = 0
    bmw.token_fault = True
    with pytest.raises(ValueError, match="neue erstellen"):
        await cars.finish_login(started["login"])
    calls = bmw.issued
    with pytest.raises(ValueError, match="neue erstellen"):  # the answer is kept, BMW is not asked again
        await cars.finish_login(started["login"])
    assert bmw.issued == calls
    with pytest.raises(ValueError, match="gibt es nicht mehr"):
        await cars.finish_login("unknown")
    with pytest.raises(ValueError, match="Client-ID"):
        await cars.start_login("bmw_cardata", "bmw", "owner@example.org")
    with pytest.raises(ValueError, match="Datenquelle"):
        await cars.start_login("vw_euda", "cupra", CLIENT_ID)


def test_an_expired_code_is_named():
    pending = {"expires_at": 0, "device_code": "D1", "verifier": "v"}
    with pytest.raises(SourceAuthError, match="abgelaufen"):
        BmwCarData.finish_login("bmw", CLIENT_ID, pending)


# ---- reading -------------------------------------------------------------------------------------------------

def test_the_container_is_created_once_and_asking_again_right_away_costs_no_request(bmw):
    client = BmwCarData("bmw", CLIENT_ID, bmw.login(), timeout=5)
    bmw.values(T0, soc=40, port="CONNECTED", status="CHARGINGACTIVE", target=80, odometer=12345, range=150)
    delivery = client.fetch(VIN, 0)
    assert [(d.soc, d.plugged, d.charging, d.target_soc, d.odometer_km, d.range_km) for d in delivery.data] == [
        (40, True, True, 80, 12345, 150)]
    assert len(bmw.containers) == 1 and set(bmw.containers[0]) >= {"containerId"}
    calls = len(bmw.api_calls)
    assert client.fetch(VIN, T0).data == [] and len(bmw.api_calls) == calls
    # after a restart the container is found again, not created a second time
    client = BmwCarData("bmw", CLIENT_ID, client.password, timeout=5)
    client.fetch(VIN, T0)
    assert len(bmw.containers) == 1


def test_a_container_deleted_in_the_portal_is_created_again_next_time(bmw):
    client = BmwCarData("bmw", CLIENT_ID, bmw.login(), timeout=5)
    client.fetch(VIN, 0)
    bmw.containers.clear()
    client._last_look.clear()
    client.fetch(VIN, 0)  # no error: looked up again and created within the same look
    assert len(bmw.containers) == 1


def test_a_car_no_longer_in_the_account_waits_for_the_owner(bmw):
    client = BmwCarData("bmw", CLIENT_ID, bmw.login(), timeout=5)
    client.fetch(VIN, 0)
    client._last_look.clear()
    calls = len(bmw.api_calls)
    with pytest.raises(SourceSetupError, match="nicht mehr") as raised:
        client.fetch("WBAOTHERCAR000003", 0)
    assert raised.value.wait_s == bmw_cardata.UNMAPPED_WAIT_S
    assert len(bmw.api_calls) - calls == 3  # the 404, the containers once more, the 404 again
    with pytest.raises(SourceSetupError):
        BmwCarData("bmw", CLIENT_ID, bmw.login(), timeout=5).fetch("WBAOTHERCAR000003", 0)


async def test_cars_of_one_account_share_its_pace(tmp_path, bmw):
    runtime, cars = setup_cars(tmp_path)
    token = bmw.login()
    runtime.storage.set_meta("cars", [{**CAR, "id": "ab12cd34", "password": token},
                                      {**CAR, "id": "ef56ab78", "name": "Zweiter", "password": token}])
    car = cars.cars[0]
    live = await cars.poll(car, cars.live.setdefault(car.id, cars_module.Live()))
    assert live.status == "waiting" and live.error is None
    assert live.next_at >= live.checked + 2 * bmw_cardata.EVERY_S


def test_only_cars_of_which_the_account_is_the_primary_user_are_listed(bmw):
    assert BmwCarData("mini", CLIENT_ID, bmw.login(), timeout=5).vehicles() == [{"vin": VIN, "name": ""}]


def test_a_void_refresh_token_asks_for_a_new_login(bmw):
    client = BmwCarData("bmw", CLIENT_ID, json.dumps({"refresh_token": "old"}), timeout=5)
    with pytest.raises(SourceAuthError, match="neu bestätigen"):
        client.vehicles()
    with pytest.raises(SourceAuthError):
        BmwCarData("bmw", CLIENT_ID, "", timeout=5).vehicles()


def test_a_redirect_is_not_followed_with_the_token(bmw):
    client = BmwCarData("bmw", CLIENT_ID, bmw.login(), timeout=5)
    with pytest.raises(SourceError, match="302"):
        client._api("GET", "/redirect")


async def test_the_daily_limit_waits_hours(tmp_path, bmw):
    runtime, cars = setup_cars(tmp_path)
    runtime.storage.set_meta("cars", [{**CAR, "id": "ab12cd34", "password": bmw.login()}])
    car = cars.cars[0]
    bmw.limit_reached = True
    live = await cars.poll(car, cars.live.setdefault(car.id, cars_module.Live()))
    assert live.status == "error" and "50 am Tag" in live.error
    assert live.next_at >= live.checked + bmw_cardata.LIMIT_WAIT_S


async def test_missing_access_to_the_api_is_a_setup_step(tmp_path, bmw, monkeypatch):
    client = BmwCarData("bmw", CLIENT_ID, bmw.login(), timeout=5)

    def forbidden(*args, **kwargs):
        return 403, {"exveErrorId": "CU-105"}
    client._token()
    monkeypatch.setattr(bmw_cardata, "_send", forbidden)
    with pytest.raises(SourceSetupError, match="CarData API"):
        client.vehicles()


# ---- the app ----------------------------------------------------------------------------------------------

def test_the_api_for_the_login_needs_a_login_and_names_mistakes(tmp_path, bmw, monkeypatch):
    monkeypatch.setenv("OPENAMPERE_DATA_DIR", str(tmp_path))
    client = TestClient(create_app(Runtime({}, Storage(tmp_path / "t.db"))))
    client.headers.update({"x-openampere": "1"})
    body = {"source": "bmw_cardata", "brand": "bmw", "username": CLIENT_ID}
    sources = {s["key"]: s for s in client.get("/api/cars").json()["sources"]}
    assert sources["bmw_cardata"]["login"] == "device" and sources["vw_euda"]["login"] == "password"
    assert sources["bmw_cardata"]["brands"][1]["portal"].startswith("https://www.mini.de/")
    login(client)
    assert client.post("/api/cars/login", json={**body, "username": "x"}).status_code == 400
    started = client.post("/api/cars/login", json=body).json()
    assert started["code"] == "ABCD-EFGH" and set(started) == {"login", "url", "code", "expires_in", "interval"}
    assert client.post(f"/api/cars/login/{started['login']}").json() == {"done": False}
    assert client.post("/api/cars/login/unknown").status_code == 400
    client.post("/api/auth/logout")
    assert client.post("/api/cars/login", json=body).status_code == 401
