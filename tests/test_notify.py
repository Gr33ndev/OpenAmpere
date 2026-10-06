import json

from openampere import notify as notify_module
from openampere.drivers.base import Snapshot
from openampere.notify import Notifier
from openampere.runtime import Runtime
from openampere.storage import Storage

TOPIC_URL = "https://ntfy.example/openampere-test"


def make(tmp_path, monkeypatch):
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    runtime.config.notify.ntfy_url = TOPIC_URL
    runtime.config.inverter.host = "inverter.local"
    posts = []

    class Response:
        def __init__(self, request):
            posts.append((request.full_url, json.loads(request.data)))

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, n):
            return b""

    monkeypatch.setattr(notify_module.urllib.request, "urlopen", lambda request, timeout: Response(request))
    return runtime, posts


async def test_unreachable_is_sent_once_and_recovery_reported(tmp_path, monkeypatch):
    runtime, posts = make(tmp_path, monkeypatch)
    monkeypatch.setattr(type(runtime.collector), "configured", property(lambda self: True))
    notifier = Notifier(runtime)
    runtime.collector.disconnected_since = 1000.0
    assert await notifier.check(now=1000 + 60) == []
    assert await notifier.check(now=1000 + 16 * 60) == ["Wechselrichter nicht erreichbar"]
    assert await notifier.check(now=1000 + 20 * 60) == []  # only once
    assert posts[0][0] == "https://ntfy.example/" and posts[0][1]["topic"] == "openampere-test"

    runtime.collector.disconnected_since = None
    runtime.collector.connected = True
    assert await notifier.check(now=5000) == ["Wechselrichter wieder erreichbar"]


async def test_alarm_and_overwrite(tmp_path, monkeypatch):
    runtime, posts = make(tmp_path, monkeypatch)
    notifier = Notifier(runtime)
    runtime.collector.latest = Snapshot(timestamp=0, alarms=[0, 0x40])
    assert await notifier.check(now=10) == ["Störung am Wechselrichter"]
    assert "0040" in posts[-1][1]["message"]  # UTF-8 title travels in JSON
    assert await notifier.check(now=20) == []

    runtime.storage.log_control("battery_settings_check", {"from": {}, "to": {}}, False, "überschrieben")
    assert await notifier.check(now=30) == ["Einstellung überschrieben"]
    assert await notifier.check(now=40) == []


async def test_power_cut_is_reported_once_and_its_end(tmp_path, monkeypatch):
    """Off-grid (backup) mode: message when the grid is gone and when it is back (#48)."""
    runtime, posts = make(tmp_path, monkeypatch)
    notifier = Notifier(runtime)
    runtime.collector.latest = Snapshot(timestamp=0, off_grid=False, battery_soc=64)
    assert await notifier.check(now=10) == []
    runtime.collector.latest = Snapshot(timestamp=0, off_grid=True, battery_soc=64)
    runtime.collector.outages.observe(runtime.collector.latest)
    assert await notifier.check(now=15) == []  # one off-grid reading can be wrong (#89)
    runtime.collector.latest = Snapshot(timestamp=10, off_grid=True, battery_soc=64)
    runtime.collector.outages.observe(runtime.collector.latest)
    assert await notifier.check(now=20) == ["Stromausfall: Notstrombetrieb"]
    assert "64 %" in posts[-1][1]["message"]
    assert await notifier.check(now=30) == []  # only once
    runtime.collector.latest = Snapshot(timestamp=30, off_grid=False, battery_soc=40)
    runtime.collector.outages.observe(runtime.collector.latest)
    assert await notifier.check(now=40) == ["Strom ist wieder da"]
    assert await notifier.check(now=50) == []


async def test_cheapest_power_tomorrow_in_german_notation_with_two_decimals(tmp_path, monkeypatch):
    from datetime import datetime

    runtime, posts = make(tmp_path, monkeypatch)
    runtime.config.notify.on_cheap_power = True
    runtime.tariffs.save([{"valid_from": "2026-01-01", "kind": "dynamic", "price_ct": 0, "surcharge_ct": 20,
                           "vat_percent": 19, "feed_in_ct": 8, "area": "DE"}])
    now = datetime(2026, 10, 5, 15, tzinfo=runtime.tz)
    start = datetime(2026, 10, 6, tzinfo=runtime.tz).timestamp()
    runtime.storage.save_prices([(int(start + i * 900), 10.0 + (i % 7) / 3) for i in range(96)])
    sent = await Notifier(runtime).check(now=now.timestamp())
    assert "Strompreis morgen" in sent
    message = posts[-1][1]["message"]
    assert message.endswith(" ct/kWh.") and "," in message.split("etwa ")[1]
    assert len(message.split("etwa ")[1].split(" ")[0].split(",")[1]) == 2  # e.g. "36,30"
