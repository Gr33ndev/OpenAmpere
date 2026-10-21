# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
import asyncio
import json
import time

from openampere import notify as notify_module
from openampere import tuya
from openampere.drivers.base import Snapshot
from openampere.notify import Notifier
from openampere.runtime import Runtime
from openampere.simulator import SimulatedTuyaCharger
from openampere.storage import Storage
from openampere.wallbox.base import Reading
from openampere.wallbox.native import NativeWallbox, NativeWallboxes

TOPIC_URL = "https://ntfy.example/openampere-test"
TUYA_KEY = "ab12cd34ef56gh78"


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

    monkeypatch.setattr(notify_module, "open_without_redirect", lambda request, timeout: Response(request))
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
    assert await notifier.check(now=10) == []  # not yet: it may be gone by itself (#404)
    assert await notifier.check(now=10 + 30 * 60) == ["Störung am Wechselrichter"]
    assert "0040" in posts[-1][1]["message"]  # UTF-8 title travels in JSON
    assert await notifier.check(now=20 + 30 * 60) == []

    runtime.storage.log_control("battery_settings_check", {"from": {}, "to": {}}, False, "überschrieben")
    assert await notifier.check(now=30 + 30 * 60) == ["Einstellung überschrieben"]
    assert await notifier.check(now=40 + 30 * 60) == []


async def test_short_fault_is_not_reported(tmp_path, monkeypatch):
    """SAJ E2 inverters show a fault for about ten minutes every night and then run normally (#404)."""
    runtime, posts = make(tmp_path, monkeypatch)
    notifier = Notifier(runtime)
    for night in range(3):
        start = night * 86400
        runtime.collector.latest = Snapshot(timestamp=start, alarms=[0x800000, 0, 0])
        for minute in range(0, 12):
            assert await notifier.check(now=start + minute * 60) == []
        runtime.collector.latest = Snapshot(timestamp=start + 720, alarms=[0, 0, 0])
        assert await notifier.check(now=start + 720) == []
    assert posts == []

    # a fault that lasts is reported, also when its code changes on the way
    runtime.collector.latest = Snapshot(timestamp=0, alarms=[0x800000, 0, 0])
    assert await notifier.check(now=500_000) == []
    runtime.collector.latest = Snapshot(timestamp=0, alarms=[0x800000, 0x1, 0])
    assert await notifier.check(now=500_000 + 20 * 60) == []
    assert await notifier.check(now=500_000 + 30 * 60) == ["Störung am Wechselrichter"]
    assert "800000,0001" in posts[-1][1]["message"]


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


class FakeWallboxes:
    """Stands in for wallbox.native.NativeWallboxes: one wallbox with the reading set by the test."""

    def __init__(self):
        self.box = NativeWallbox(name="Garage", id="wb1")
        self.reading: Reading | None = Reading(state="idle")

    def enabled(self):
        return [self.box]

    def fresh(self, wb, now=None):
        return self.reading


def charging(kwh: float, seconds: int) -> Reading:
    return Reading(state="charging", connected=True, charging=True, power_w=7200, session_kwh=kwh, session_s=seconds)


async def test_wallbox_charge_start_and_end_once_each(tmp_path, monkeypatch):
    runtime, posts = make(tmp_path, monkeypatch)
    runtime.config.notify.on_wallbox = True
    wallboxes = FakeWallboxes()
    notifier = Notifier(runtime, wallboxes=wallboxes)
    assert await notifier.check(now=0) == []

    wallboxes.reading = charging(0.1, 60)
    assert await notifier.check(now=60) == ["Auto lädt"]
    assert posts[-1][1]["message"] == "Garage: Das Laden hat begonnen, mit etwa 7,2 kW."
    wallboxes.reading = charging(18.24, 3 * 3600 + 600)
    assert await notifier.check(now=120) == []  # only once per charge

    # the counters go back to 0 when the car is unplugged: the summary is the last one seen while charging
    wallboxes.reading = Reading(state="idle", connected=False, session_kwh=0, session_s=0)
    assert await notifier.check(now=180) == ["Laden beendet"]
    assert posts[-1][1]["message"] == "Garage: Das Laden ist beendet. Geladen: 18,2 kWh in 3 Std. 10 Min."
    assert await notifier.check(now=240) == []


async def test_wallbox_short_pause_is_no_end(tmp_path, monkeypatch):
    """Charging with surplus stops with every cloud (#451): only a pause of 15 minutes counts as the end."""
    runtime, posts = make(tmp_path, monkeypatch)
    runtime.config.notify.on_wallbox = True
    wallboxes = FakeWallboxes()
    notifier = Notifier(runtime, wallboxes=wallboxes)
    wallboxes.reading = charging(1.0, 600)
    assert await notifier.check(now=0) == ["Auto lädt"]
    paused = Reading(state="waiting", connected=True, session_kwh=1.0, session_s=600)
    wallboxes.reading = paused
    assert await notifier.check(now=10 * 60) == []
    wallboxes.reading = None  # no recent reading for a moment: part of the pause
    assert await notifier.check(now=12 * 60) == []
    wallboxes.reading = charging(2.0, 1200)
    assert await notifier.check(now=14 * 60) == []  # the same charge goes on
    wallboxes.reading = paused
    assert await notifier.check(now=28 * 60) == []
    assert await notifier.check(now=29 * 60) == ["Laden beendet"]
    assert len(posts) == 2


async def test_wallbox_complete_is_a_pause(tmp_path, monkeypatch):
    """The dé wallbox may report STOP also after a stop by the solar control: no end of its own (#451)."""
    runtime, posts = make(tmp_path, monkeypatch)
    runtime.config.notify.on_wallbox = True
    wallboxes = FakeWallboxes()
    notifier = Notifier(runtime, wallboxes=wallboxes)
    wallboxes.reading = charging(5.0, 3000)
    assert await notifier.check(now=0) == ["Auto lädt"]
    wallboxes.reading = Reading(state="finished", connected=True, session_kwh=5.0, session_s=3000)
    assert await notifier.check(now=60) == []
    assert await notifier.check(now=15 * 60) == ["Laden beendet"]


async def test_wallbox_second_charge_after_a_long_pause_counts_only_its_own_energy(tmp_path, monkeypatch):
    """The counters run from plugging in: 10 kWh, a pause of 20 minutes, 5 kWh more is a second charge of 5 kWh."""
    runtime, posts = make(tmp_path, monkeypatch)
    runtime.config.notify.on_wallbox = True
    wallboxes = FakeWallboxes()
    notifier = Notifier(runtime, wallboxes=wallboxes)
    wallboxes.reading = charging(0.1, 60)
    assert await notifier.check(now=0) == ["Auto lädt"]
    wallboxes.reading = charging(10.0, 5400)
    assert await notifier.check(now=5400) == []
    wallboxes.reading = Reading(state="waiting", connected=True, session_kwh=10.0, session_s=5400)
    assert await notifier.check(now=5400 + 16 * 60) == ["Laden beendet"]
    assert posts[-1][1]["message"] == "Garage: Das Laden ist beendet. Geladen: 10,0 kWh in 1 Std. 30 Min."

    wallboxes.reading = charging(10.1, 5460)
    assert await notifier.check(now=7000) == ["Auto lädt"]
    wallboxes.reading = charging(15.0, 5400 + 2400)
    assert await notifier.check(now=9400) == []
    wallboxes.reading = Reading(state="idle", connected=False)
    assert await notifier.check(now=9430) == ["Laden beendet"]
    assert posts[-1][1]["message"] == "Garage: Das Laden ist beendet. Geladen: 5,0 kWh in 40 Min."


async def test_wallbox_restart_during_a_charge_sends_no_second_start(tmp_path, monkeypatch):
    runtime, posts = make(tmp_path, monkeypatch)
    runtime.config.notify.on_wallbox = True
    wallboxes = FakeWallboxes()
    wallboxes.reading = charging(1.0, 600)
    assert await Notifier(runtime, wallboxes=wallboxes).check(now=0) == ["Auto lädt"]

    restarted = Notifier(runtime, wallboxes=wallboxes)
    assert await restarted.check(now=60) == []
    wallboxes.reading = Reading(state="unknown", connected=False)  # unknown state: no proof the car is unplugged
    assert await restarted.check(now=120) == []
    assert await restarted.check(now=120 + 15 * 60) == ["Laden beendet"]


async def test_wallbox_off_by_default(tmp_path, monkeypatch):
    runtime, posts = make(tmp_path, monkeypatch)
    wallboxes = FakeWallboxes()
    wallboxes.reading = charging(1.0, 600)
    assert await Notifier(runtime, wallboxes=wallboxes).check(now=0) == []


async def test_evcc_loadpoint_charge_with_solar_share(tmp_path, monkeypatch):
    runtime, posts = make(tmp_path, monkeypatch)
    runtime.config.notify.on_wallbox = True

    class FakeEvcc:
        configured = True
        loadpoints = [{"id": 1, "title": "Carport", "heating": False, "connected": True, "charging": True,
                       "power_w": 11000.0, "session_wh": 500.0, "session_solar_pct": None},
                      {"id": 2, "title": "Wärmepumpe", "heating": True, "connected": True, "charging": True,
                       "power_w": 2000.0, "session_wh": None, "session_solar_pct": None}]

        def fresh(self):
            return {"loadpoints": self.loadpoints}

    evcc = FakeEvcc()
    notifier = Notifier(runtime, evcc=evcc)
    assert await notifier.check(now=0) == ["Auto lädt"]  # not for the heat pump
    assert posts[-1][1]["message"] == "Carport: Das Laden hat begonnen, mit etwa 11,0 kW."
    evcc.loadpoints[0] |= {"session_wh": 12_000.0, "session_solar_pct": 81.6}
    assert await notifier.check(now=45 * 60) == []
    evcc.loadpoints[0] |= {"charging": False, "connected": False, "power_w": 0.0, "session_wh": None}
    assert await notifier.check(now=46 * 60) == ["Laden beendet"]
    assert posts[-1][1]["message"] == ("Carport: Das Laden ist beendet. Geladen: 12,0 kWh in 45 Min. "
                                       "Davon etwa 82 % Solarstrom.")

    # a loadpoint removed from evcc during a charge: forgotten, so a new one at the same position starts fresh
    # a second charge with the car still plugged in: evcc's counters and solar share include the first one
    evcc.loadpoints[0] |= {"connected": True, "session_wh": 12_000.0}
    assert await notifier.check(now=46 * 60 + 30) == []
    evcc.loadpoints[0] |= {"charging": True}
    assert await notifier.check(now=47 * 60) == ["Auto lädt"]
    evcc.loadpoints[0] |= {"session_wh": 14_000.0}
    assert await notifier.check(now=57 * 60) == []
    evcc.loadpoints[0] |= {"charging": False, "power_w": 0.0}
    assert await notifier.check(now=73 * 60) == ["Laden beendet"]
    assert posts[-1][1]["message"] == "Carport: Das Laden ist beendet. Geladen: 2,0 kWh in 10 Min."

    evcc.loadpoints[0] |= {"charging": True, "connected": True}
    assert await notifier.check(now=80 * 60) == ["Auto lädt"]
    evcc.loadpoints = []
    assert await notifier.check(now=81 * 60) == []
    evcc.loadpoints = [{"id": 1, "title": "Neu", "heating": False, "connected": True, "charging": False,
                        "power_w": 0.0, "session_wh": None, "session_solar_pct": None}]
    assert await notifier.check(now=100 * 60) == []
    assert await notifier.check(now=120 * 60) == []  # no end of a charge that never was at this loadpoint


async def test_wallbox_charge_is_forgotten_when_the_switch_goes_off(tmp_path, monkeypatch):
    """Switched on again days later: no late end of the old charge, and the next charge gets its start."""
    runtime, posts = make(tmp_path, monkeypatch)
    runtime.config.notify.on_wallbox = True
    wallboxes = FakeWallboxes()
    notifier = Notifier(runtime, wallboxes=wallboxes)
    wallboxes.reading = charging(1.0, 600)
    assert await notifier.check(now=0) == ["Auto lädt"]
    runtime.config.notify.on_wallbox = False
    assert await notifier.check(now=60) == []
    wallboxes.reading = Reading(state="idle", connected=False)
    runtime.config.notify.on_wallbox = True
    assert await notifier.check(now=3 * 86400) == []
    wallboxes.reading = charging(0.1, 60)
    assert await notifier.check(now=3 * 86400 + 60) == ["Auto lädt"]


async def test_wallbox_without_readings_ends_its_charge(tmp_path, monkeypatch):
    """A portable wallbox is often unplugged together with the car: no reading for 15 minutes ends the charge."""
    runtime, posts = make(tmp_path, monkeypatch)
    runtime.config.notify.on_wallbox = True
    wallboxes = FakeWallboxes()
    notifier = Notifier(runtime, wallboxes=wallboxes)
    wallboxes.reading = charging(6.0, 3600)
    assert await notifier.check(now=0) == ["Auto lädt"]
    wallboxes.reading = None
    assert await notifier.check(now=10 * 60) == []
    assert await notifier.check(now=16 * 60) == ["Laden beendet"]
    assert posts[-1][1]["message"] == "Garage: Das Laden ist beendet. Geladen: 6,0 kWh in 1 Std."


async def test_wallbox_end_message_keeps_its_summary_when_sending_fails(tmp_path, monkeypatch):
    runtime, posts = make(tmp_path, monkeypatch)
    runtime.config.notify.on_wallbox = True
    wallboxes = FakeWallboxes()
    notifier = Notifier(runtime, wallboxes=wallboxes)
    wallboxes.reading = charging(9.5, 5400)
    assert await notifier.check(now=0) == ["Auto lädt"]
    wallboxes.reading = Reading(state="idle", connected=False, session_kwh=0, session_s=0)

    async def unreachable(*args):
        return False

    notifier.push = unreachable
    assert await notifier.check(now=60) == []
    del notifier.push  # ntfy answers again
    assert await notifier.check(now=90) == ["Laden beendet"]
    assert posts[-1][1]["message"] == "Garage: Das Laden ist beendet. Geladen: 9,5 kWh in 1 Std. 30 Min."


async def test_wallbox_charge_read_from_the_simulated_de_wallbox(tmp_path, monkeypatch):
    """The whole way: Tuya protocol, the driver's reading and the notifications."""
    runtime, posts = make(tmp_path, monkeypatch)
    runtime.config.notify.on_wallbox = True
    charger = SimulatedTuyaCharger(TUYA_KEY, plugged=True)
    server = await asyncio.start_server(charger.serve_client, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        wallboxes = NativeWallboxes(runtime)
        monkeypatch.setattr(wallboxes, "device", lambda wb: tuya.TuyaDevice(wb.host, wb.local_key, port=port, timeout=1))
        wallboxes.save([{"name": "Garage", "host": "127.0.0.1", "local_key": TUYA_KEY, "mode": "manual"}])
        wb = wallboxes.enabled()[0]
        notifier = Notifier(runtime, wallboxes=wallboxes)
        await wallboxes.read(wb)
        assert await notifier.check(now=time.time()) == []  # plugged in, not charging

        charger.apply({"140": True})
        await wallboxes.read(wb)
        assert await notifier.check(now=time.time()) == ["Auto lädt"]
        assert posts[-1][1]["message"] == "Garage: Das Laden hat begonnen, mit etwa 11,0 kW."

        charger.tick(3600)
        await wallboxes.read(wb)
        assert await notifier.check(now=time.time()) == []
        charger.plugged = False
        await wallboxes.read(wb)
        assert await notifier.check(now=time.time()) == ["Laden beendet"]
        assert posts[-1][1]["message"] == "Garage: Das Laden ist beendet. Geladen: 11,0 kWh in 1 Std."
    finally:
        server.close()
        await server.wait_closed()


async def test_wallbox_charge_right_after_plugging_in_counts_from_zero(tmp_path, monkeypatch):
    """Unplugged with yesterday's counters, then charging at the next check: those counters are no baseline."""
    runtime, posts = make(tmp_path, monkeypatch)
    runtime.config.notify.on_wallbox = True
    wallboxes = FakeWallboxes()
    notifier = Notifier(runtime, wallboxes=wallboxes)
    wallboxes.reading = Reading(state="idle", connected=False, session_kwh=20.0, session_s=7200)
    assert await notifier.check(now=0) == []
    wallboxes.reading = charging(0.2, 60)
    assert await notifier.check(now=30) == ["Auto lädt"]
    wallboxes.reading = charging(25.0, 9000)
    assert await notifier.check(now=9000) == []
    wallboxes.reading = Reading(state="idle", connected=False, session_kwh=25.0, session_s=9000)
    assert await notifier.check(now=9030) == ["Laden beendet"]
    assert posts[-1][1]["message"] == "Garage: Das Laden ist beendet. Geladen: 25,0 kWh in 2 Std. 30 Min."
