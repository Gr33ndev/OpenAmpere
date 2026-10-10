# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Meter values of the grid operator (#60, #67): the Netze BW client against a fake portal, the sync job and the billing.

The fake portal behaves like the real one as far as users reported it: grid power and feed-in are separate
installations of one meter, values come as CSV quarter hours since the smart meter was installed, the day before is
published late, and the meter values may answer 401 while the portal session is still valid.
"""

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
from openampere.gridmeter.netze_bw import NetzeBw, parse_csv
from openampere.runtime import Runtime
from openampere.storage import Storage
from openampere.tariffs import Tariffs

TZ = ZoneInfo("Europe/Berlin")
QUARTER_KWH = {"cons-1": 0.125, "feed-1": 0.25}  # 12 and 24 kWh on a normal day
INSTALLATIONS = {"installations": [
    # one smart meter: grid power and feed-in are two installations with their own ids
    {"id": "feed-1", "type": "IMS", "state": "Active", "meterId": "1XYZ0000000001", "valueTypes": ["FEEDIN", "FEEDIN_READING"]},
    {"id": "cons-1", "type": "IMS", "state": {"code": "Active"}, "meterId": "1XYZ0000000001",
     "valueTypes": ["CONSUMPTION", "READING"]},
    # the same again in a second installation: counted once
    {"id": "cons-9", "type": "IMS", "state": "Active", "meterId": "1XYZ0000000001", "valueTypes": ["CONSUMPTION"]},
    {"id": "old-1", "type": "IMS", "state": "Inactive", "meterId": "1XYZ0000000002", "valueTypes": ["CONSUMPTION"]},
    {"id": "zse-1", "type": "ZSE", "state": "Active", "meterId": "1XYZ0000000003", "valueTypes": []},
]}


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


class Portal:
    """Fake of meine.netze-bw.de (served as 127.0.0.1) and its Auth0 login (served as localhost)."""

    def __init__(self) -> None:
        self.password = "richtig"
        self.extra_step = False
        self.sessions: set[str] = set()
        self.ims_sessions: set[str] = set()  # sessions the meter-value service still accepts
        self.logins = 0
        self.requests: list[dict] = []
        self.user_agents: set[str] = set()
        self.installed = datetime(2026, 3, 13, tzinfo=TZ)  # the smart meter: values only from here on
        self.published_until = datetime(2026, 6, 30, 14, 0, tzinfo=TZ)  # 29 June complete, 30 June only partly
        portal = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status, body=b"", headers=None, kind="text/html"):
                body = json.dumps(body).encode() if kind == "json" else body.encode() if isinstance(body, str) else body
                self.send_response(status)
                self.send_header("Content-Type", {"json": "application/json", "csv": "text/csv"}.get(kind, kind))
                for name, value in (headers or {}).items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _sid(self):
                cookies = dict(c.strip().split("=", 1) for c in (self.headers.get("Cookie") or "").split(";") if "=" in c)
                return cookies.get("session")

            def do_GET(self):
                portal.user_agents.add(self.headers.get("User-Agent", ""))
                host, url = self.headers["Host"].split(":")[0], urlparse(self.path)
                query = {k: v[0] for k, v in parse_qs(url.query).items()}
                if host == "localhost" and url.path == "/login":
                    return self._send(200, '<form method="post"><input type="hidden" name="_csrf" value="CSRF1"></form>',
                                      {"Set-Cookie": "_csrf=CSRF1; Path=/"})
                if url.path == "/bff/auth/login":
                    if self._sid() in portal.sessions:
                        return self._send(302, headers={"Location": f"{portal.base}/"})
                    target = urlencode({"state": "S1", "client": "C1", "redirect_uri": f"{portal.base}/bff/oidc/signin",
                                        "response_type": "code", "scope": "openid", "nonce": "N1", "protocol": "oauth2"})
                    return self._send(302, headers={"Location": f"{portal.auth}/login?{target}"})
                if url.path == "/bff/auth/callback" and query.get("code") == "CODE1":
                    sid = f"s{portal.logins}"
                    portal.sessions.add(sid)
                    portal.ims_sessions.add(sid)
                    return self._send(302, headers={"Location": f"{portal.base}/", "Set-Cookie": f"session={sid}; Path=/"})
                if url.path == "/":
                    return self._send(200, "<html></html>")
                if self._sid() not in portal.sessions or self.headers.get("x-csrf") != "1":
                    return self._send(401, {"error": "unauthorized"}, kind="json")
                if url.path == "/bff/auth/user":
                    return self._send(200, [{"type": "sub", "value": "user-1"}], kind="json")
                if url.path == "/bff/api/kuposervice/v1/portal/installations":
                    return self._send(200, INSTALLATIONS, kind="json")
                meter = url.path.removeprefix("/bff/api/imsservice/v1/meters/").removesuffix("/measurements/download")
                if meter in QUARTER_KWH:
                    if self._sid() not in portal.ims_sessions:
                        return self._send(401, {"error": "ims"}, kind="json")
                    portal.requests.append({"meter": meter, **query})
                    return self._csv(meter, utc(query["startDate"]), utc(query["endDate"]))
                return self._send(404)

            def _csv(self, meter, start, end):
                if end <= portal.installed:
                    return self._send(400, {"error": "before installation"}, kind="json")
                header = "Datum;Uhrzeit;Verbrauch (kWh);Zaehlerstand Verbrauch (kWh);Status" if meter.startswith("cons") \
                    else "Datum;Uhrzeit;Einspeisung (kWh);Zaehlerstand Einspeisung (kWh);Einspeisung Status"
                rows, moment, reading = [header], max(start, portal.installed), 1000.0
                first = True
                while moment <= min(end, portal.published_until):
                    local = moment.astimezone(TZ)
                    energy = "" if first else f"{QUARTER_KWH[meter]:.3f}".replace(".", ",")
                    reading += 0 if first else QUARTER_KWH[meter]
                    rows.append(f"{local:%d.%m.%Y};{local:%H:%M};{energy};{reading:.3f};W".replace(f"{reading:.3f}", f"{reading:.3f}".replace(".", ",")))
                    moment += timedelta(minutes=15)
                    first = False
                return self._send(200, "\n".join(rows) + "\n", kind="csv")

            def do_POST(self):
                portal.user_agents.add(self.headers.get("User-Agent", ""))
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
        return NetzeBw("kunde@example.org", password, base_url=self.base, auth_url=self.auth, timeout=5, pause_s=0)


@pytest.fixture
def portal():
    p = Portal()
    yield p
    p.server.shutdown()


def test_grid_power_and_feed_in_are_separate_meters(portal):
    meters = portal.client().meters()
    assert [m.to_dict() for m in meters] == [
        {"id": "cons-1", "name": "1XYZ0000000001 · Bezug", "kinds": ["import"]},
        {"id": "feed-1", "name": "1XYZ0000000001 · Einspeisung", "kinds": ["export"]},
    ]


def test_names_openampere_instead_of_a_browser(portal):
    """Login and downloads say who is asking (#174), they do not pretend to be a desktop browser."""
    portal.client().meters()
    assert len(portal.user_agents) == 1
    agent = portal.user_agents.pop()
    assert agent.startswith("Mozilla/5.0 (compatible; OpenAmpere/") and "github.com/Gr33ndev/OpenAmpere" in agent
    assert "Chrome" not in agent and "Safari" not in agent


def test_only_complete_days_since_the_smart_meter(portal):
    client = portal.client()
    cons, feed = sorted(client.meters(), key=lambda m: m.id)
    days = client.daily(cons, "import", date(2025, 5, 27), date(2026, 7, 2))
    # from the installation day to 29 June; 30 June is published only partly and stays unknown (not 0)
    assert min(days) == "2026-03-13" and max(days) == "2026-06-29" and "2026-06-30" not in days
    assert len(days) == (date(2026, 6, 29) - date(2026, 3, 13)).days + 1
    assert days["2026-03-13"] == pytest.approx(12.0) and days["2026-03-29"] == pytest.approx(23 * 4 * 0.125)  # clock change
    # weekly requests from now backwards, stopping at the installation (the portal refuses older weeks)
    assert portal.requests[0]["startDate"] == "2026-06-24T22:00:00.000Z"
    assert len(portal.requests) <= (date(2026, 7, 2) - date(2026, 3, 13)).days // 7 + 3
    assert client.daily(feed, "export", date(2026, 6, 1), date(2026, 6, 3)) == {"2026-06-01": 24.0, "2026-06-02": 24.0}


def test_meter_values_refused_while_the_portal_session_is_valid(portal):
    client = portal.client()
    cons = next(m for m in client.meters() if m.id == "cons-1")
    portal.ims_sessions.clear()  # /bff/auth/user still 200, the meter values 401
    assert client.daily(cons, "import", date(2026, 6, 1), date(2026, 6, 2)) == {"2026-06-01": pytest.approx(12.0)}
    assert portal.logins == 2  # a real new login, not the still valid session


def test_refused_login(portal):
    with pytest.raises(ProviderAuthError, match="E-Mail und Passwort"):
        portal.client("falsch").meters()
    portal.extra_step = True
    with pytest.raises(ProviderAuthError, match="weiteren Schritt"):
        portal.client().meters()


def test_csv_with_the_autumn_clock_change():
    rows = ["Datum;Uhrzeit;Einspeisung;Zaehlerstand Einspeisung;Status", "25.10.2025;00:00;;10,000;W"]
    moment = datetime(2025, 10, 24, 22, tzinfo=timezone.utc)
    for _ in range(100):  # the long day: 02:00 to 03:00 twice
        moment += timedelta(minutes=15)
        local = moment.astimezone(TZ)
        rows.append(f"{local:%d.%m.%Y};{local:%H:%M};0,010;1,000;W")
    quarters = parse_csv("\n".join(rows), "export")
    assert len(quarters) == 100 and min(quarters) == datetime(2025, 10, 24, 22, tzinfo=timezone.utc)
    assert sum(quarters.values()) == pytest.approx(1.0)


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
            super().__init__(username, password, base_url=portal.base, auth_url=portal.auth, timeout=5, pause_s=0)
    return {"netze_bw": FakeNetzeBw}


async def test_sync_stores_complete_days_and_does_not_retry_a_refused_login(runtime, portal):
    meter = GridMeter(runtime, fake_providers(portal))
    now = datetime(2026, 7, 1, 12, tzinfo=TZ).timestamp()
    await meter.sync(now=now)
    view = meter.view()
    assert view["error"] is None and view["until"] == "2026-06-29" and sorted(view["active"]) == ["cons-1", "feed-1"]
    ids = meter.active_ids()
    assert len(runtime.storage.meter_days("import", "2025-01-01", "2027-01-01", ids)) == 109
    assert runtime.storage.meter_days("export", "2026-06-29", "2026-06-30", ids) == {"2026-06-29": 24.0}

    # a day stored before but not complete in the newest answer is unknown again
    runtime.storage.save_meter_days("netze_bw:cons-1", "import", {"2026-06-30": 0.0})
    await meter.sync(force=True, now=now)
    assert "2026-06-30" not in runtime.storage.meter_days("import", "2026-06-01", "2026-07-02", ids)

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
