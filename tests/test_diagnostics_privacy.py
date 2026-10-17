# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""The diagnostics report goes into public issues: no meter numbers, device addresses or time of the report."""

import time

from openampere.consumers import SurplusControl
from openampere.diagnostics import Diagnostics, report_markdown
from openampere.runtime import Runtime
from openampere.storage import Storage


async def test_meter_numbers_and_device_addresses_are_pseudonymised(tmp_path):
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    await runtime.update_settings({"meter.meter_ids": ["METER-TEST-0001"]})
    SurplusControl(runtime).save([
        {"name": "Pumpe", "kind": "shelly1", "host": "pumpe-keller.local"},
        {"name": "Heizstab", "kind": "http", "url_on": "http://heizstab.local/on?password=geheim",
         "url_off": "http://heizstab.local/off?password=geheim"}])
    text = str(Diagnostics(runtime)._pseudonymise({"checks": [], "log": [
        "Zähler METER-TEST-0001 nicht gefunden", "pumpe-keller.local antwortet nicht",
        "Fehler bei http://heizstab.local/on?password=geheim", "heizstab.local nicht erreichbar"]}))
    for private in ("METER-TEST-0001", "pumpe-keller", "geheim", "heizstab.local"):
        assert private not in text, private
    runtime.storage.close()


def test_the_report_has_no_date_and_time():
    created = time.mktime((2026, 6, 1, 13, 37, 0, 0, 0, -1))
    report = {"created": created, "version": "dev", "device": {}, "checks": [], "log": [],
              "connection": {"mode": "persistent", "timeout_s": 3}}
    text = report_markdown(report)
    assert "13:37" not in text and "01.06.2026" not in text


def test_raw_modbus_frames_are_not_in_the_report(tmp_path):
    import logging

    from pymodbus.logging import Log

    from openampere.logs import RecentLogs
    recent, logger = RecentLogs(), logging.getLogger("pymodbus.logging")
    logger.addHandler(recent)
    try:  # pymodbus appends the last frames to its errors; they hold register values such as energy counters (#338)
        Log.frame_dump = [(Log.SEND_DATA, b"\x00\xf8\x00\x00\x00\x06\x01\x03\x40\xbf\x00\x40", b""),
                          (Log.RECV_DATA, b"\x00\xf8\x00\x00\x00\x07\x01\x03\x04\x00\x0e\x53\xc2", b"")]
        Log.error("No response received after 0 retries, continue with next request")
    finally:
        logger.removeHandler(recent)
        Log.frame_dump = []
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    report = Diagnostics(runtime)._pseudonymise({"created": time.time(), "version": "dev", "device": {},
                                                 "checks": [], "log": recent.lines(),
                                                 "connection": {"mode": "persistent", "timeout_s": 3}})
    text = report_markdown(report)
    assert "No response received after 0 retries" in text and "<Modbus-Rohdaten entfernt>" in text
    assert "0x53" not in text and "send:" not in text and "recv:" not in text
    runtime.storage.close()


async def test_a_report_with_the_full_serial_number_warns_at_the_top(tmp_path):
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    diagnostics = Diagnostics(runtime)
    try:  # owners post reports in public issues; one with the full serial number must say it is not for that (#428)
        full = await diagnostics.run(include_serial=True)
        assert full["full_serial"] is True
        assert report_markdown(full).startswith("> **Achtung:** Dieser Bericht enthält die vollständige Seriennummer.")
        masked = await diagnostics.run()
        assert masked["full_serial"] is False
        assert "vollständige Seriennummer" not in report_markdown(masked)
        assert report_markdown(masked).startswith("## OpenAmpere-Diagnose")
    finally:
        runtime.storage.close()


def test_a_stored_report_from_before_the_warning_counts_as_masked():
    report = {"created": time.time(), "version": "dev", "device": {}, "checks": [], "log": [],
              "connection": {"mode": "persistent", "timeout_s": 3}}
    assert report_markdown(report).startswith("## OpenAmpere-Diagnose")
