# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Host names the owner typed into the device query or the wallbox check and the page title of a queried device are
masked in the diagnostics report like other addresses and device text (security fix)."""

import time

from openampere.diagnostics import Diagnostics, report_markdown
from openampere.probe import ReadResult
from openampere.runtime import Runtime
from openampere.storage import Storage

TITLE = "Max-Mustermann-PC SN ABCD1234567 [hier](https://example.com) ![x](https://example.com/x.png)"


def _report(tmp_path, include_text=False):
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    try:
        diagnostics = Diagnostics(runtime)
        diagnostics.probe.last_identify = {"host": "max-mustermann-pc", "open_ports": [80, 502], "title": TITLE,
                                           "mac_vendor": None}
        diagnostics.probe.last_read = ReadResult(host="wallbox-garage", port=502, unit=1, function=3)
        diagnostics.wallbox._last = {"model": "foxess_ac", "label": "FoxESS AC", "host": "carport-box", "port": None,
                                     "unit": 1, "tried_ports": [502], "outcome": "unreachable", "blocks": [],
                                     "answers": {"app_mode": "unknown", "wired": "unknown"}, "findings": {}}
        report = diagnostics._pseudonymise({
            "created": time.time(), "version": "dev", "device": {}, "checks": [],
            "log": ["max-mustermann-pc antwortet nicht"], "connection": {"mode": "persistent", "timeout_s": 3},
            "probe": diagnostics.probe.section(include_text=include_text),
            "wallbox": diagnostics.wallbox.section(include_text=include_text)})
        return report, report_markdown(report)
    finally:
        runtime.storage.close()


def test_host_names_the_owner_typed_are_pseudonymised(tmp_path):
    report, text = _report(tmp_path)
    for private in ("max-mustermann-pc", "wallbox-garage", "carport-box"):
        assert private not in text and private not in str(report), private
    assert "Gerät <Host-1>:" in text  # the same name keeps the same pseudonym
    assert "<Host-1> antwortet nicht" in str(report["log"])


def test_the_page_title_is_shortened_and_escaped(tmp_path):
    report, text = _report(tmp_path)
    title = report["probe"]["identify"]["title"]
    assert title == TITLE[:4] + "…" + TITLE[-2:]
    assert "Mustermann" not in text and "ABCD1234567" not in text and "](https://" not in text


def test_a_title_the_owner_includes_cannot_add_links(tmp_path):
    report, text = _report(tmp_path, include_text=True)
    assert "ABCD1234567" in text  # the owner chose to include readable text
    assert "[hier](https://example.com)" not in text and "![x](" not in text
    assert "\\[hier\\]\\(https://example.com\\)" in text
