"""Meter values of the grid operator (#60): the Netze BW client against a fake portal, the sync job and the billing."""

import json
import threading
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlparse
from zoneinfo import ZoneInfo

import pytest

from openampere.billing import Billing
from openampere.config import EDITABLE
from openampere.gridmeter import PROVIDERS, GridMeter, Meter, ProviderAuthError
from openampere.gridmeter.netze_bw import NetzeBw
from openampere.runtime import Runtime
from openampere.storage import Storage
from openampere.tariffs import Tariffs

TZ = ZoneInfo("Europe/Berlin")
INSTALLATIONS = {"installations": [
    # the same physical meter in two installations: counted once
    {"id": "b-inst", "type": "IMS", "state": "Active", "meterId": "1EBZ0001", "friendlyName": "Hauszähler",
     "valueTypes": ["CONSUMPTION", "READING", "FEEDIN", "FEEDIN_READING"]},
    {"id": "a-inst", "type": "IMS", "state": {"code": "Active"}, "meterId": "1EBZ0001", "friendlyName": "Hauszähler",
     "valueTypes": ["CONSUMPTION", "FEEDIN"]},
    {"id": "c-old", "type": "IMS", "state": "Inactive", "meterId": "1EBZ0002", "valueTypes": ["CONSUMPTION"]},
    {"id": "d-mme", "type": "MME", "state": "Active", "meterId": "1EBZ0003", "valueTypes": ["CONSUMPTION"]},
]}


class Portal:
    """Fake of meine.netze-bw.de (served as 127.0.0.1) and its Auth0 login (served as localhost)."""

    def __init__(self) -> None:
        self.password = "richtig"
        self.extra_step = False
        self.sessions: set[str] = set()
        self.logins = 0
        self.requests: list[dict] = []
        self.last_day = date(2026, 6, 29)  # the operator's values arrive a day or two later
        portal = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status, body=b"", headers=None, kind="text/html"):
                body = body if isinstance(body, bytes) else json.dumps(body).encode() if kind == "json" else body.encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json" if kind == "json" else kind)
                for name, value in (headers or {}).items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _session(self):
                cookies = dict(c.strip().split("=", 1) for c in (self.headers.get("Cookie") or "").split(";") if "=" in c)
                return cookies.get("session") in portal.sessions

            def do_GET(self):
                host, url = self.headers["Host"].split(":")[0], urlparse(self.path)
                query = parse_qs(url.query)
                if host == "localhost" and url.path == "/login":
                    return self._send(200, '<form method="post"><input type="hidden" name="_csrf" value="CSRF1"></form>',
                                      {"Set-Cookie": "_csrf=CSRF1; Path=/"})
                if url.path == "/bff/auth/login":
                    if self._session():
                        return self._send(302, headers={"Location": f"{portal.base}/"})
                    target = urlencode({"state": "S1", "client": "C1", "redirect_uri": f"{portal.base}/signin-oidc",
                                        "response_type": "code", "scope": "openid", "nonce": "N1", "protocol": "oauth2"})
                    return self._send(302, headers={"Location": f"{portal.auth}/login?{target}"})
                if url.path == "/bff/auth/callback" and query.get("code") == ["CODE1"]:
                    sid = f"s{portal.logins}"
                    portal.sessions.add(sid)
                    return self._send(302, headers={"Location": f"{portal.base}/", "Set-Cookie": f"session={sid}; Path=/"})
                if url.path == "/":
                    return self._send(200, "<html></html>")
                if not self._session() or self.headers.get("x-csrf") != "1":
                    return self._send(401, {"error": "unauthorized"}, kind="json")
                if url.path == "/bff/auth/user":
                    return self._send(200, [{"type": "sub", "value": "user-1"}], kind="json")
                if url.path == "/bff/api/kuposervice/v1/portal/installations":
                    return self._send(200, INSTALLATIONS, kind="json")
                if url.path == "/bff/api/imsservice/v1/meters/a-inst/measurements":
                    portal.requests.append({k: v[0] for k, v in query.items()})
                    first = datetime.fromisoformat(query["startDate"][0].replace("Z", "+00:00")).astimezone(TZ).date()
                    last = datetime.fromisoformat(query["endDate"][0].replace("Z", "+00:00")).astimezone(TZ).date()
                    per_day = 11.0 if query["valueType"] == ["CONSUMPTION"] else 20_000.0
                    days = [first + timedelta(days=i) for i in range((min(last, portal.last_day + timedelta(days=1)) - first).days)]
                    return self._send(200, {"measurements": [
                        {"startDatetime": datetime(d.year, d.month, d.day, tzinfo=TZ).astimezone(timezone.utc)
                            .isoformat().replace("+00:00", "Z"), "value": per_day,
                         "unit": "kWh" if per_day < 1000 else "Wh", "status": "VALID"} for d in days]}, kind="json")
                return self._send(404)

            def do_POST(self):
                host, url = self.headers["Host"].split(":")[0], urlparse(self.path)
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
                if host == "localhost" and url.path == "/usernamepassword/login":
                    data = json.loads(body)
                    assert data["_csrf"] == "CSRF1" and data["state"] == "S1" and data["client_id"] == "C1"
                    if data["password"] != portal.password:
                        return self._send(401, {"code": "invalid_user_password"}, kind="json")
                    if portal.extra_step:
                        return self._send(200, "<div>Bitte Code aus der App eingeben</div>")
                    return self._send(200, '<form method="post" action="/login/callback">'
                                           '<input type="hidden" name="wa" value="wsignin1.0">'
                                           '<input type="hidden" name="wresult" value="TOKEN1"></form>')
                if host == "localhost" and url.path == "/login/callback" and parse_qs(body).get("wresult") == ["TOKEN1"]:
                    portal.logins += 1
                    return self._send(302, headers={"Location": f"{portal.base}/bff/auth/callback?code=CODE1"})
                return self._send(404)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        port = self.server.server_address[1]
        self.base, self.auth = f"http://127.0.0.1:{port}", f"http://localhost:{port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def client(self, password: str = "richtig") -> NetzeBw:
        return NetzeBw("kunde@example.org", password, base_url=self.base, auth_url=self.auth, timeout=5)


@pytest.fixture
def portal():
    p = Portal()
    yield p
    p.server.shutdown()


def test_login_meters_and_daily_values(portal):
    client = portal.client()
    meters = client.meters()
    assert [m.to_dict() for m in meters] == [{"id": "a-inst", "name": "Hauszähler", "kinds": ["import", "export"]}]
    days = client.daily(meters[0], "import", date(2026, 1, 1), date(2026, 7, 2))
    assert len(days) == 180 and days["2026-01-01"] == 11.0 and max(days) == "2026-06-29"
    assert len(portal.requests) == 3  # 90 days per request
    assert portal.requests[0]["startDate"] == "2025-12-31T23:00:00.000Z" and portal.requests[0]["filter"] == "1DAY"
    assert client.daily(meters[0], "export", date(2026, 6, 1), date(2026, 6, 3)) == {"2026-06-01": 20.0, "2026-06-02": 20.0}
    assert portal.logins == 1


def test_expired_session_logs_in_again(portal):
    client = portal.client()
    client.meters()
    portal.sessions.clear()
    assert client.meters()[0].id == "a-inst"
    assert portal.logins == 2


def test_refused_login(portal):
    with pytest.raises(ProviderAuthError, match="E-Mail und Passwort"):
        portal.client("falsch").meters()
    portal.extra_step = True
    with pytest.raises(ProviderAuthError, match="weiteren Schritt"):
        portal.client().meters()


def test_settings_offer_every_provider():
    assert EDITABLE["meter.provider"][1:] == ("none", *PROVIDERS)


@pytest.fixture
def runtime(tmp_path, portal):
    rt = Runtime({}, Storage(tmp_path / "t.db"))
    rt.config.meter.provider, rt.config.meter.username, rt.config.meter.password = "netze_bw", "kunde@example.org", "richtig"
    return rt


def fake_providers(portal):
    class FakeNetzeBw(NetzeBw):
        def __init__(self, username, password):
            super().__init__(username, password, base_url=portal.base, auth_url=portal.auth, timeout=5)
    return {"netze_bw": FakeNetzeBw}


async def test_sync_stores_the_days_and_does_not_retry_a_refused_login(runtime, portal):
    meter = GridMeter(runtime, fake_providers(portal))
    now = datetime(2026, 7, 1, 12, tzinfo=TZ).timestamp()
    await meter.sync(now=now)
    view = meter.view()
    assert view["error"] is None and view["until"] == "2026-06-29" and view["active"] == ["a-inst"]
    assert meter.active_ids() == ["netze_bw:a-inst"]
    assert len(runtime.storage.meter_days("import", "2025-01-01", "2027-01-01", meter.active_ids())) == (date(2026, 6, 29) - date(2025, 5, 27)).days + 1

    runtime.config.meter.password = "falsch"
    await meter.sync(force=True, now=now)
    assert "Passwort" in meter.view()["error"]
    logins = portal.logins
    await meter.sync(now=now + 2 * 86400)  # the background job leaves a refused login alone
    assert portal.logins == logins

    runtime.config.meter.provider = "none"  # switched off: the billing ignores the stored values
    assert meter.active_ids() == [] and meter.view()["meters"] == []


def daily_rows(first: date, days: int, grid_import=0.0):
    return [{"ts": int(datetime(d.year, d.month, d.day, 12, tzinfo=TZ).timestamp()), "pv": 0.0, "load": grid_import,
             "grid_import": grid_import, "grid_export": 0.0, "battery_charge": 0.0, "battery_discharge": 0.0, "soc": 50.0}
            for d in (first + timedelta(days=i) for i in range(days))]


class StubMeter:
    label = "Netze BW"

    def active_ids(self):
        return ["netze_bw:m1"]


def billing_with_meter(tmp_path, inverter_from: date, inverter_days: int, meter_from: date, meter_days: int):
    storage = Storage(tmp_path / "b.db")
    tariffs = Tariffs(storage, lambda: (30.0, 8.0))
    tariffs.save([{"valid_from": "2025-01-01", "price_ct": 30, "feed_in_ct": 8, "base_fee_eur_month": 12}])
    storage.import_energy(daily_rows(inverter_from, inverter_days, grid_import=10_000), "local")
    storage.save_meter_days("netze_bw:m1", "import",
                            {(meter_from + timedelta(days=i)).isoformat(): 11.0 for i in range(meter_days)})
    storage.save_meter_days("netze_bw:other", "import", {"2026-01-01": 999.0})  # not chosen: ignored
    bill = Billing(storage, tariffs, StubMeter())
    bill.save({"import": {"start_month": 1, "payments": [{"from": "2026-01", "eur": 50}]}})
    return bill.status(TZ, datetime(2026, 7, 1, 12, tzinfo=TZ).timestamp())["import"]


def test_billing_uses_the_meter_values(tmp_path):
    year = billing_with_meter(tmp_path, date(2026, 1, 1), 181, date(2026, 1, 1), 180)
    # meter values until 29 June, the inverter's value for 30 June
    assert year["so_far_kwh"] == pytest.approx(180 * 11 + 10, abs=0.1)
    assert year["so_far_eur"] == pytest.approx((180 * 11 + 10) * 0.30 + 12 * 12 / 365 * 181.5, abs=0.05)
    assert year["meter"] == {"source": "Netze BW", "from": "2026-01-01", "until": "2026-06-29", "kwh": 1980.0,
                             "deviation_percent": -9.1}
    assert year["missing_days"] == 0 and year["estimated_before"] is None


def test_meter_values_fill_the_time_before_the_recording(tmp_path):
    # OpenAmpere runs since April, the operator's values reach back to January: nothing is estimated
    year = billing_with_meter(tmp_path, date(2026, 4, 1), 91, date(2026, 1, 1), 180)
    assert year["estimated_before"] is None and year["missing_days"] == 0
    assert year["so_far_kwh"] == pytest.approx(180 * 11 + 10, abs=0.1)
    # the base fee counts once for the whole period, also before the recording started
    assert year["so_far_eur"] == pytest.approx((180 * 11 + 10) * 0.30 + 12 * 12 / 365 * 181.5, abs=0.05)


def test_meter_class_round_trip():
    assert Meter(**Meter("x", "Zähler", ["import"]).to_dict()) == Meter("x", "Zähler", ["import"])
