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
from openampere.wallbox.native import Live, NativeWallbox, NativeWallboxes

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
        self.live = {"wb1": Live()}

    def enabled(self):
        return [self.box]

    def fresh(self, wb, now=None):
        return self.reading


def charging(kwh: float) -> Reading:
    return Reading(state="charging", connected=True, charging=True, power_w=7200, session_kwh=kwh)


def paused(kwh: float, state: str = "waiting") -> Reading:
    return Reading(state=state, connected=True, session_kwh=kwh)


UNPLUGGED = Reading(state="idle", connected=False, session_kwh=0)


def wallbox_notifier(tmp_path, monkeypatch):
    runtime, posts = make(tmp_path, monkeypatch)
    runtime.config.notify.on_wallbox = True
    wallboxes = FakeWallboxes()
    return Notifier(runtime, wallboxes=wallboxes), wallboxes, posts


async def test_wallbox_charge_start_and_end_once_each(tmp_path, monkeypatch):
    notifier, wallboxes, posts = wallbox_notifier(tmp_path, monkeypatch)
    assert await notifier.check(now=0) == []
    wallboxes.reading = charging(0.1)
    assert await notifier.check(now=60) == ["Auto lädt"]
    assert posts[-1][1]["message"] == "Garage: Das Laden hat begonnen, mit etwa 7,2 kW."
    wallboxes.reading = charging(18.24)
    assert await notifier.check(now=60 + 3 * 3600 + 600) == []  # only once per charge

    # the counters of some wallboxes may go back to 0 when unplugged: the figures as last seen charging
    wallboxes.reading = UNPLUGGED
    assert await notifier.check(now=60 + 3 * 3600 + 630) == ["Laden beendet"]
    assert posts[-1][1]["message"] == ("Garage: Das Laden ist beendet. Seit dem Anstecken geladen: 18,2 kWh "
                                       "in 3 Std. 10 Min.")
    assert await notifier.check(now=20000) == []


async def test_wallbox_short_pause_is_no_end(tmp_path, monkeypatch):
    """Charging with surplus stops with every cloud (#451): only a pause of 15 minutes counts as the end."""
    notifier, wallboxes, posts = wallbox_notifier(tmp_path, monkeypatch)
    wallboxes.reading = charging(1.0)
    assert await notifier.check(now=0) == ["Auto lädt"]
    wallboxes.reading = paused(1.0)
    assert await notifier.check(now=10 * 60) == []
    wallboxes.reading = None  # no recent reading for a moment: part of the pause
    assert await notifier.check(now=12 * 60) == []
    wallboxes.reading = charging(2.0)
    assert await notifier.check(now=14 * 60) == []  # the same charge goes on
    wallboxes.reading = paused(2.0)
    assert await notifier.check(now=28 * 60) == []
    assert await notifier.check(now=29 * 60) == ["Laden beendet"]
    assert len(posts) == 2


async def test_wallbox_complete_ends_the_charge_at_once(tmp_path, monkeypatch):
    notifier, wallboxes, posts = wallbox_notifier(tmp_path, monkeypatch)
    wallboxes.reading = charging(0.1)
    assert await notifier.check(now=0) == ["Auto lädt"]
    wallboxes.reading = charging(5.0)
    assert await notifier.check(now=3000) == []
    wallboxes.reading = paused(5.0, "finished")
    assert await notifier.check(now=3030) == ["Laden beendet"]
    assert posts[-1][1]["message"] == "Garage: Das Laden ist beendet. Seit dem Anstecken geladen: 5,0 kWh in 50 Min."


async def test_wallbox_complete_after_a_stop_by_openampere_is_a_pause(tmp_path, monkeypatch):
    """The dé wallbox may report STOP also after a stop by the solar control (#451): then it waits like a pause."""
    notifier, wallboxes, posts = wallbox_notifier(tmp_path, monkeypatch)
    wallboxes.reading = charging(5.0)
    assert await notifier.check(now=0) == ["Auto lädt"]
    wallboxes.live["wb1"].stop_sent = 20  # the solar control stopped the charge: a cloud
    wallboxes.reading = paused(5.0, "finished")
    assert await notifier.check(now=30) == []
    wallboxes.reading = charging(5.5)
    assert await notifier.check(now=10 * 60) == []  # the same charge goes on
    wallboxes.reading = paused(6.0, "finished")  # complete with no stop by OpenAmpere since: the car is full
    assert await notifier.check(now=11 * 60) == ["Laden beendet"]


async def test_wallbox_complete_right_after_a_stop_still_read_as_charging_is_a_pause(tmp_path, monkeypatch):
    """The stop shows only with the next read: a check between the two still sees the car charging."""
    notifier, wallboxes, posts = wallbox_notifier(tmp_path, monkeypatch)
    wallboxes.reading = charging(5.0)
    assert await notifier.check(now=0) == ["Auto lädt"]
    wallboxes.live["wb1"].stop_sent = 5
    assert await notifier.check(now=8) == []  # the read-back before the stop shows
    wallboxes.reading = paused(5.0, "finished")
    assert await notifier.check(now=38) == []
    assert await notifier.check(now=8 + 15 * 60) == ["Laden beendet"]


def log_stop(runtime, name="Garage", wallbox_id="wb1"):
    runtime.storage.log_control("wallbox", {"from": {"loadpoint": name, "id": wallbox_id},
                                            "to": {"action": "stop", "value": False}}, False, "kein Überschuss")


async def test_wallbox_stop_in_the_control_log_counts_after_a_reconnect_and_a_restart(tmp_path, monkeypatch):
    """A reconnect of the wallbox resets Live.stop_sent and a restart loses it: the control log keeps the stop."""
    notifier, wallboxes, posts = wallbox_notifier(tmp_path, monkeypatch)
    start = time.time()
    wallboxes.reading = charging(5.0)
    assert await notifier.check(now=start) == ["Auto lädt"]
    log_stop(notifier.runtime)  # stop_sent stays unset, as after a reconnect
    wallboxes.reading = paused(5.0, "finished")
    assert await notifier.check(now=start + 30) == []

    restarted = Notifier(notifier.runtime, wallboxes=wallboxes)
    assert await restarted.check(now=start + 60) == []
    log_stop(notifier.runtime, "Andere Wallbox", "wb2")  # another wallbox's stop changes nothing here
    assert await restarted.check(now=start + 60 + 15 * 60) == ["Laden beendet"]


async def test_wallbox_stop_found_by_id_after_a_rename(tmp_path, monkeypatch):
    notifier, wallboxes, posts = wallbox_notifier(tmp_path, monkeypatch)
    start = time.time()
    wallboxes.reading = charging(5.0)
    assert await notifier.check(now=start) == ["Auto lädt"]
    log_stop(notifier.runtime, "Alter Name")
    wallboxes.reading = paused(5.0, "finished")
    assert await notifier.check(now=start + 30) == []  # still OpenAmpere's stop


async def test_wallbox_complete_after_a_restart_without_a_stop_ends_at_once(tmp_path, monkeypatch):
    notifier, wallboxes, posts = wallbox_notifier(tmp_path, monkeypatch)
    start = time.time()
    wallboxes.reading = charging(5.0)
    assert await notifier.check(now=start) == ["Auto lädt"]
    notifier.runtime.storage.log_control("wallbox", {"from": {"loadpoint": "Garage"},
                                                     "to": {"action": "stop", "value": False}}, True, "Testmodus")
    restarted = Notifier(notifier.runtime, wallboxes=wallboxes)
    wallboxes.reading = paused(9.0, "finished")
    assert await restarted.check(now=start + 3600) == ["Laden beendet"]  # a test-mode stop was not sent


async def test_wallbox_charge_on_after_an_end_message_reports_the_total(tmp_path, monkeypatch):
    """10 kWh, a long pause (end message), 2 kWh more: "lädt wieder", and the end gives the 12 kWh since plugging
    in, without a duration, which would span the pause. A short Wi-Fi drop in between changes nothing."""
    notifier, wallboxes, posts = wallbox_notifier(tmp_path, monkeypatch)
    wallboxes.reading = charging(0.1)
    assert await notifier.check(now=0) == ["Auto lädt"]
    wallboxes.reading = charging(10.0)
    assert await notifier.check(now=3600) == []
    wallboxes.reading = paused(10.0)
    assert await notifier.check(now=3600 + 16 * 60) == ["Laden beendet"]
    assert posts[-1][1]["message"] == "Garage: Das Laden ist beendet. Seit dem Anstecken geladen: 10,0 kWh in 1 Std."
    wallboxes.reading = None
    assert await notifier.check(now=6000) == []

    wallboxes.reading = charging(10.1)
    assert await notifier.check(now=6600) == ["Auto lädt"]
    assert posts[-1][1]["message"] == "Garage: Das Auto lädt wieder, mit etwa 7,2 kW."
    wallboxes.reading = charging(12.0)
    assert await notifier.check(now=7200) == []
    wallboxes.reading = UNPLUGGED
    assert await notifier.check(now=7230) == ["Laden beendet"]
    assert posts[-1][1]["message"] == "Garage: Das Laden ist beendet. Seit dem Anstecken geladen: 12,0 kWh."


async def test_wallbox_unplugged_unseen_starts_a_new_session(tmp_path, monkeypatch):
    """Pulled from the socket together with the car after an end message, back the next day and already charging:
    its counter began again, so this is no "lädt wieder"."""
    notifier, wallboxes, posts = wallbox_notifier(tmp_path, monkeypatch)
    wallboxes.reading = charging(18.0)
    assert await notifier.check(now=0) == ["Auto lädt"]
    wallboxes.reading = paused(18.0)
    assert await notifier.check(now=16 * 60) == ["Laden beendet"]
    wallboxes.reading = None
    assert await notifier.check(now=17 * 60) == []

    day = 86400
    wallboxes.reading = charging(0.5)
    assert await notifier.check(now=day) == ["Auto lädt"]
    assert posts[-1][1]["message"] == "Garage: Das Laden hat begonnen, mit etwa 7,2 kW."
    wallboxes.reading = charging(25.0)
    assert await notifier.check(now=day + 9000) == []
    wallboxes.reading = UNPLUGGED
    assert await notifier.check(now=day + 9030) == ["Laden beendet"]
    assert posts[-1][1]["message"] == ("Garage: Das Laden ist beendet. Seit dem Anstecken geladen: 25,0 kWh "
                                       "in 2 Std. 30 Min.")


async def test_wallbox_unplugging_after_an_end_message_starts_afresh(tmp_path, monkeypatch):
    notifier, wallboxes, posts = wallbox_notifier(tmp_path, monkeypatch)
    wallboxes.reading = charging(5.0)
    assert await notifier.check(now=0) == ["Auto lädt"]
    wallboxes.reading = paused(5.0)
    assert await notifier.check(now=16 * 60) == ["Laden beendet"]
    wallboxes.reading = UNPLUGGED
    assert await notifier.check(now=17 * 60) == []
    wallboxes.reading = charging(5.0)  # a wallbox that keeps its counter until the next charge
    assert await notifier.check(now=18 * 60) == ["Auto lädt"]
    assert posts[-1][1]["message"] == "Garage: Das Laden hat begonnen, mit etwa 7,2 kW."


async def test_wallbox_restart_during_a_charge_sends_no_second_start(tmp_path, monkeypatch):
    notifier, wallboxes, posts = wallbox_notifier(tmp_path, monkeypatch)
    wallboxes.reading = charging(1.0)
    assert await notifier.check(now=0) == ["Auto lädt"]

    restarted = Notifier(notifier.runtime, wallboxes=wallboxes)
    assert await restarted.check(now=60) == []
    wallboxes.reading = Reading(state="unknown", connected=False)  # unknown state: no proof the car is unplugged
    assert await restarted.check(now=120) == []
    assert await restarted.check(now=120 + 15 * 60) == ["Laden beendet"]


async def test_wallbox_restart_gives_no_duration_with_the_downtime(tmp_path, monkeypatch):
    """Down for hours during a charge, the car unplugged meanwhile: the end message gives no duration."""
    notifier, wallboxes, posts = wallbox_notifier(tmp_path, monkeypatch)
    wallboxes.reading = charging(0.1)
    assert await notifier.check(now=0) == ["Auto lädt"]
    wallboxes.reading = charging(7.0)
    assert await notifier.check(now=3600) == []

    restarted = Notifier(notifier.runtime, wallboxes=wallboxes)
    wallboxes.reading = Reading(state="idle", connected=False, session_kwh=7.0)
    assert await restarted.check(now=5 * 3600) == ["Laden beendet"]
    assert posts[-1][1]["message"] == "Garage: Das Laden ist beendet. Seit dem Anstecken geladen: 7,0 kWh."


async def test_wallbox_restart_gives_no_duration_also_with_the_car_plugged_in(tmp_path, monkeypatch):
    notifier, wallboxes, posts = wallbox_notifier(tmp_path, monkeypatch)
    wallboxes.reading = charging(0.1)
    assert await notifier.check(now=0) == ["Auto lädt"]
    wallboxes.reading = charging(7.0)
    assert await notifier.check(now=3600) == []

    restarted = Notifier(notifier.runtime, wallboxes=wallboxes)
    wallboxes.reading = paused(7.0)
    assert await restarted.check(now=5 * 3600) == []
    assert await restarted.check(now=5 * 3600 + 15 * 60) == ["Laden beendet"]
    assert posts[-1][1]["message"] == "Garage: Das Laden ist beendet. Seit dem Anstecken geladen: 7,0 kWh."


async def test_wallbox_off_by_default(tmp_path, monkeypatch):
    runtime, posts = make(tmp_path, monkeypatch)
    wallboxes = FakeWallboxes()
    wallboxes.reading = charging(1.0)
    assert await Notifier(runtime, wallboxes=wallboxes).check(now=0) == []


async def test_wallbox_charge_is_forgotten_when_the_switch_goes_off(tmp_path, monkeypatch):
    """Switched on again days later: no late end of the old charge, and the next charge gets its start."""
    notifier, wallboxes, posts = wallbox_notifier(tmp_path, monkeypatch)
    wallboxes.reading = charging(1.0)
    assert await notifier.check(now=0) == ["Auto lädt"]
    notifier.runtime.config.notify.on_wallbox = False
    assert await notifier.check(now=60) == []
    wallboxes.reading = UNPLUGGED
    notifier.runtime.config.notify.on_wallbox = True
    assert await notifier.check(now=3 * 86400) == []
    wallboxes.reading = charging(0.1)
    assert await notifier.check(now=3 * 86400 + 60) == ["Auto lädt"]


async def test_wallbox_without_readings_ends_its_charge(tmp_path, monkeypatch):
    """A portable wallbox is often unplugged together with the car: no reading for 15 minutes ends the charge."""
    notifier, wallboxes, posts = wallbox_notifier(tmp_path, monkeypatch)
    wallboxes.reading = charging(0.1)
    assert await notifier.check(now=0) == ["Auto lädt"]
    wallboxes.reading = charging(6.0)
    assert await notifier.check(now=3600) == []
    wallboxes.reading = None
    assert await notifier.check(now=3600 + 10 * 60) == []
    assert await notifier.check(now=3600 + 16 * 60) == ["Laden beendet"]
    assert posts[-1][1]["message"] == "Garage: Das Laden ist beendet. Seit dem Anstecken geladen: 6,0 kWh in 1 Std."


async def test_wallbox_end_message_keeps_its_summary_when_sending_fails(tmp_path, monkeypatch):
    notifier, wallboxes, posts = wallbox_notifier(tmp_path, monkeypatch)
    wallboxes.reading = charging(0.1)
    assert await notifier.check(now=0) == ["Auto lädt"]
    wallboxes.reading = charging(9.5)
    assert await notifier.check(now=5400) == []
    wallboxes.reading = UNPLUGGED

    async def unreachable(*args):
        return False

    notifier.push = unreachable
    assert await notifier.check(now=5430) == []
    del notifier.push  # ntfy answers again
    assert await notifier.check(now=5460) == ["Laden beendet"]
    assert posts[-1][1]["message"] == ("Garage: Das Laden ist beendet. Seit dem Anstecken geladen: 9,5 kWh "
                                       "in 1 Std. 30 Min.")


async def test_evcc_loadpoint_charge_with_solar_share(tmp_path, monkeypatch):
    runtime, posts = make(tmp_path, monkeypatch)
    runtime.config.notify.on_wallbox = True

    class FakeEvcc:
        configured = True
        answers = True
        loadpoints = [{"id": 1, "title": "Carport", "heating": False, "connected": True, "charging": True,
                       "power_w": 11000.0, "session_wh": 500.0, "session_solar_pct": None},
                      {"id": 2, "title": "Wärmepumpe", "heating": True, "connected": True, "charging": True,
                       "power_w": 2000.0, "session_wh": None, "session_solar_pct": None}]

        def fresh(self):
            return {"loadpoints": self.loadpoints} if self.answers else None

    evcc = FakeEvcc()
    carport = evcc.loadpoints[0]
    notifier = Notifier(runtime, evcc=evcc)
    assert await notifier.check(now=0) == ["Auto lädt"]  # not for the heat pump
    assert posts[-1][1]["message"] == "Carport: Das Laden hat begonnen, mit etwa 11,0 kW."
    carport |= {"session_wh": 12_000.0, "session_solar_pct": 81.6}
    assert await notifier.check(now=45 * 60) == []
    evcc.answers = False  # evcc not answering for a while: the charge is kept
    assert await notifier.check(now=50 * 60) == []
    evcc.answers = True
    carport |= {"charging": False, "power_w": 0.0}
    assert await notifier.check(now=61 * 60) == ["Laden beendet"]
    assert posts[-1][1]["message"] == ("Carport: Das Laden ist beendet. Seit dem Anstecken geladen: 12,0 kWh "
                                       "in 45 Min. Davon etwa 82 % Solarstrom.")

    # evcc keeps the last session's values after unplugging until the next connect: a new session all the same
    carport |= {"connected": False}
    assert await notifier.check(now=62 * 60) == []
    carport |= {"connected": True, "charging": True, "session_wh": 100.0, "session_solar_pct": None}
    assert await notifier.check(now=63 * 60) == ["Auto lädt"]
    assert posts[-1][1]["message"] == "Carport: Das Laden hat begonnen."

    # a loadpoint removed from evcc during a charge: forgotten, so a new one at the same position starts fresh
    evcc.loadpoints = []
    assert await notifier.check(now=64 * 60) == []
    evcc.loadpoints = [{"id": 1, "title": "Neu", "heating": False, "connected": True, "charging": False,
                        "power_w": 0.0, "session_wh": None, "session_solar_pct": None}]
    assert await notifier.check(now=70 * 60) == []
    assert await notifier.check(now=90 * 60) == []  # no end of a charge that never was at this loadpoint


async def test_wallbox_charge_read_from_the_simulated_de_wallbox(tmp_path, monkeypatch):
    """The whole way: Tuya protocol, the driver's reading and the notifications, with a short pause, a restart of
    OpenAmpere, a long pause and unplugging."""
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
        start = time.time()

        async def check(notifier, minutes: float) -> list[str]:
            await wallboxes.read(wb)
            wallboxes.live[wb.id].updated = start + minutes * 60  # read "then", so it counts as recent
            return await notifier.check(now=start + minutes * 60)

        notifier = Notifier(runtime, wallboxes=wallboxes)
        assert await check(notifier, 0) == []  # plugged in, not charging
        charger.apply({"140": True})
        assert await check(notifier, 1) == ["Auto lädt"]
        assert posts[-1][1]["message"] == "Garage: Das Laden hat begonnen, mit etwa 11,0 kW."

        charger.tick(3600)
        assert await check(notifier, 61) == []
        charger.charging = False  # a short pause
        assert await check(notifier, 70) == []
        charger.charging = True
        assert await check(notifier, 72) == []  # the same charge goes on

        notifier = Notifier(runtime, wallboxes=wallboxes)  # restarted
        charger.tick(600)
        assert await check(notifier, 82) == []  # no second start
        charger.charging = False
        assert await check(notifier, 83) == []
        assert await check(notifier, 98) == ["Laden beendet"]  # a long pause
        # no duration: the charge began before the restart
        assert posts[-1][1]["message"] == "Garage: Das Laden ist beendet. Seit dem Anstecken geladen: 12,8 kWh."
        charger.plugged = False
        assert await check(notifier, 99) == []
    finally:
        server.close()
        await server.wait_closed()


async def test_wallbox_short_charge_then_unplugged_unseen_is_no_charge_on(tmp_path, monkeypatch):
    """A minute of solar charging at sunset (end counter 0.0), the portable wallbox unplugged unseen with the car,
    the next day a new session whose first reading also shows 0.0: "hat begonnen", timed from today."""
    notifier, wallboxes, posts = wallbox_notifier(tmp_path, monkeypatch)
    wallboxes.reading = charging(0.0)
    assert await notifier.check(now=0) == ["Auto lädt"]
    wallboxes.reading = paused(0.0)
    assert await notifier.check(now=16 * 60) == ["Laden beendet"]
    assert posts[-1][1]["message"] == "Garage: Das Laden ist beendet."
    wallboxes.reading = None
    assert await notifier.check(now=17 * 60) == []

    day = 86400
    wallboxes.reading = charging(0.0)
    assert await notifier.check(now=day) == ["Auto lädt"]
    assert posts[-1][1]["message"] == "Garage: Das Laden hat begonnen, mit etwa 7,2 kW."
    wallboxes.reading = charging(8.0)
    assert await notifier.check(now=day + 3600) == []
    wallboxes.reading = UNPLUGGED
    assert await notifier.check(now=day + 3630) == ["Laden beendet"]
    assert posts[-1][1]["message"] == "Garage: Das Laden ist beendet. Seit dem Anstecken geladen: 8,0 kWh in 1 Std."


async def test_wallbox_charge_on_the_next_day_is_a_new_charge(tmp_path, monkeypatch):
    """Plugged in overnight: a charge the next morning is no "lädt wieder" of the evening's one."""
    notifier, wallboxes, posts = wallbox_notifier(tmp_path, monkeypatch)
    wallboxes.reading = charging(0.1)
    assert await notifier.check(now=0) == ["Auto lädt"]
    wallboxes.reading = charging(6.0)
    assert await notifier.check(now=3600) == []
    wallboxes.reading = paused(6.0)
    assert await notifier.check(now=3630) == []
    assert await notifier.check(now=3600 + 15 * 60) == ["Laden beendet"]
    morning = 3600 + 14 * 3600
    wallboxes.reading = charging(6.1)
    assert await notifier.check(now=morning) == ["Auto lädt"]
    assert posts[-1][1]["message"] == "Garage: Das Laden hat begonnen, mit etwa 7,2 kW."
    wallboxes.reading = charging(11.0)
    assert await notifier.check(now=morning + 3600) == []
    wallboxes.reading = UNPLUGGED
    assert await notifier.check(now=morning + 3630) == ["Laden beendet"]
    # the evening's energy is in the counter: no duration, which would read like 11 kWh in one hour
    assert posts[-1][1]["message"] == "Garage: Das Laden ist beendet. Seit dem Anstecken geladen: 11,0 kWh."


async def test_wallbox_without_energy_counter_gives_the_duration(tmp_path, monkeypatch):
    """The FoxESS AC EV Charger reports no energy of the session."""
    notifier, wallboxes, posts = wallbox_notifier(tmp_path, monkeypatch)
    wallboxes.reading = Reading(state="charging", connected=True, charging=True, power_w=11000)
    assert await notifier.check(now=0) == ["Auto lädt"]
    assert await notifier.check(now=4200) == []
    wallboxes.reading = Reading(state="idle", connected=False)
    assert await notifier.check(now=4230) == ["Laden beendet"]
    assert posts[-1][1]["message"] == "Garage: Das Laden ist beendet, nach 1 Std. 10 Min."


async def test_wallbox_restart_then_charging_again_gives_no_duration(tmp_path, monkeypatch):
    """Down overnight with the full car plugged in, charging again within 15 minutes of the restart: the same charge,
    but its duration would include the night."""
    notifier, wallboxes, posts = wallbox_notifier(tmp_path, monkeypatch)
    wallboxes.reading = charging(0.1)
    assert await notifier.check(now=0) == ["Auto lädt"]
    wallboxes.reading = charging(7.0)
    assert await notifier.check(now=3600) == []

    restarted = Notifier(notifier.runtime, wallboxes=wallboxes)
    morning = 15 * 3600
    wallboxes.reading = paused(7.0)
    assert await restarted.check(now=morning) == []
    wallboxes.reading = charging(7.5)
    assert await restarted.check(now=morning + 300) == []  # no second start
    wallboxes.reading = charging(9.0)
    assert await restarted.check(now=morning + 3600) == []
    wallboxes.reading = UNPLUGGED
    assert await restarted.check(now=morning + 3630) == ["Laden beendet"]
    assert posts[-1][1]["message"] == "Garage: Das Laden ist beendet. Seit dem Anstecken geladen: 9,0 kWh."
