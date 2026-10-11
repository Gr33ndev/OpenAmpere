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
        {"name": "Heizstab", "kind": "http", "url_on": "http://admin:geheim@heizstab.local/on",
         "url_off": "http://admin:geheim@heizstab.local/off"}])
    text = str(Diagnostics(runtime)._pseudonymise({"checks": [], "log": [
        "Zähler METER-TEST-0001 nicht gefunden", "pumpe-keller.local antwortet nicht",
        "Fehler bei http://admin:geheim@heizstab.local/on", "heizstab.local nicht erreichbar"]}))
    for private in ("METER-TEST-0001", "pumpe-keller", "geheim", "heizstab.local"):
        assert private not in text, private
    runtime.storage.close()


def test_the_report_has_no_date_and_time():
    created = time.mktime((2026, 6, 1, 13, 37, 0, 0, 0, -1))
    report = {"created": created, "version": "dev", "device": {}, "checks": [], "log": [],
              "connection": {"mode": "persistent", "timeout_s": 3}}
    text = report_markdown(report)
    assert "13:37" not in text and "01.06.2026" not in text
