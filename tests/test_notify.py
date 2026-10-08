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


async def test_storage_problems_are_reported_once_and_their_end(tmp_path, monkeypatch):
    """#170: a failing database write or a nearly full disk is sent once, and once more when it is fine again."""
    runtime, posts = make(tmp_path, monkeypatch)
    free = {"bytes": 50 * 10**9}
    monkeypatch.setattr(runtime.storage, "free_bytes", lambda: free["bytes"])
    notifier = Notifier(runtime)
    collector = runtime.collector
    collector.storage_failing_since, collector.storage_error = 1000.0, "full"
    assert await notifier.check(now=1000 + 60) == []  # a single failed write is no reason to warn
    assert await notifier.check(now=1000 + 6 * 60) == ["Messwerte werden nicht gespeichert"]
    assert "Speicherplatz ist voll" in posts[-1][1]["message"]
    assert await notifier.check(now=1000 + 30 * 60) == []  # only once
    collector.storage_failing_since, collector.storage_error = None, None
    assert await notifier.check(now=4000) == ["Messwerte werden wieder gespeichert"]
    assert await notifier.check(now=4100) == []

    free["bytes"] = 100 * 1024 * 1024
    assert await notifier.check(now=5000) == ["Wenig Speicherplatz"]
    assert "105 MB" in posts[-1][1]["message"]
    free["bytes"] = 210 * 1024 * 1024  # just above the limit: no back-and-forth
    assert await notifier.check(now=5100) == []
    free["bytes"] = 300 * 1024 * 1024
    assert await notifier.check(now=5200) == ["Wieder genug Speicherplatz"]

    runtime.config.notify.on_storage = False
    free["bytes"] = 1024
    assert await notifier.check(now=6000) == []


async def test_storage_message_is_not_repeated_when_its_state_cannot_be_saved(tmp_path, monkeypatch):
    """With a full disk, the notification state cannot be written either: it must not be sent every 30 s."""
    runtime, posts = make(tmp_path, monkeypatch)
    monkeypatch.setattr(runtime.storage, "free_bytes", lambda: 1024)

    def full(*_args):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(runtime.storage, "set_meta", full)
    notifier = Notifier(runtime)
    assert await notifier.check(now=10) == ["Wenig Speicherplatz"]
    assert await notifier.check(now=40) == []
