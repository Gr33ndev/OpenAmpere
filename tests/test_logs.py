# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Log level from the settings and the last log lines in the diagnostics report (#169)."""

import logging

import pytest
from fastapi.testclient import TestClient

from openampere import logs
from openampere.api import create_app
from openampere.config import build_config, validate
from openampere.diagnostics import Diagnostics, report_markdown
from openampere.drivers.base import DeviceInfo
from openampere.runtime import Runtime
from openampere.storage import Storage

from conftest import login


@pytest.fixture
def recent():
    """The in-memory handler on OpenAmpere's logger, as the server installs it on the root logger."""
    logger = logging.getLogger("openampere")
    logs.RECENT.records.clear()
    logger.addHandler(logs.RECENT)
    try:
        yield logs.RECENT
    finally:
        logger.removeHandler(logs.RECENT)
        logs.RECENT.records.clear()
        logs.apply_level("info")


def test_log_level_from_file_settings_and_environment(monkeypatch):
    assert build_config()[0].log.level == "info"
    assert build_config({"log": {"level": "warning"}}, {"log.level": "debug"})[0].log.level == "debug"
    monkeypatch.setenv("OPENAMPERE_LOG_LEVEL", "DEBUG")  # upper case as usual for log levels
    config, locked = build_config({}, {"log.level": "warning"})
    assert config.log.level == "debug" and "log.level" in locked
    assert validate({"log.level": "debug"}) == {"log.level": "debug"}
    with pytest.raises(ValueError):
        validate({"log.level": "verbose"})


async def test_log_level_changes_without_restart(tmp_path, recent):
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    logger = logging.getLogger("openampere.collector")
    logger.debug("hidden")
    await runtime.update_settings({"log.level": "debug"})
    assert logging.getLogger("openampere").level == logging.DEBUG
    logger.debug("shown")
    await runtime.update_settings({"log.level": "warning"})
    logger.info("hidden too")
    logger.warning("a warning")
    messages = [message for *_, message in recent.records]
    assert "hidden" not in messages and "hidden too" not in messages
    assert "shown" in messages and "a warning" in messages


def test_recent_logs_keep_the_last_lines_with_relative_times():
    handler = logs.RecentLogs(size=3)
    logger = logging.getLogger("openampere.test_recent")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        for i in range(5):
            logger.info("line %d", i)
        try:
            raise ValueError("broken")
        except ValueError:
            logger.exception("failed")
    finally:
        logger.removeHandler(handler)
        logger.setLevel(logging.NOTSET)
    assert [message.split("\n")[0] for *_, message in handler.records] == ["line 3", "line 4", "failed"]
    created = handler.records[-1][0]
    lines = handler.lines(now=created + 3725)
    assert lines[-1].startswith("-01:02:05 ERROR openampere.test_recent: failed\nTraceback")
    assert "ValueError: broken" in lines[-1]


async def test_diagnostics_report_has_the_log_without_personal_data(tmp_path, recent):
    """The log quotes addresses, the serial number and service URLs; the shared report must not."""
    # from the file: the collector is not started, nothing tries to reach these addresses
    runtime = Runtime({"inverter": {"host": "192.168.178.30"}, "notify": {"ntfy_url": "https://ntfy.sh/openampere-k3x9"},
                       "evcc": {"url": "http://evcc-box:7070"}}, Storage(tmp_path / "t.db"))
    runtime.collector.device = DeviceInfo("FoxESS", "H3-10.0", serial="60BH1234567890", firmware="1.0")
    runtime.collector.last_error = "Keine Verbindung zum Wechselrichter."
    log = logging.getLogger("openampere.test")
    log.warning("cannot connect to inverter at 192.168.178.30:502")
    log.info("connected: DeviceInfo(manufacturer='FoxESS', serial='60BH1234567890')")
    log.warning("notification failed: Client error '404' for url 'https://ntfy.sh/openampere-k3x9'")
    log.warning("evcc not reachable at http://evcc-box:7070/api/state")
    log.info("request from 192.168.178.41 and 192.168.178.41 via nas.fritz.box, mail owner@example.org")
    try:
        raise OSError("disk")
    except OSError:
        log.exception("failed in /home/someone/openampere/src/x.py")

    report = await Diagnostics(runtime).run()  # works without a connection: the log tells why (#169)
    text = report_markdown(report)
    for secret in ("192.168.178.30", "192.168.178.41", "1234567890", "openampere-k3x9", "evcc-box", "nas.fritz.box",
                   "owner@example.org", "someone"):
        assert secret not in text, secret
    joined = "\n".join(report["log"])
    assert "<Wechselrichter>:502" in joined and "60BH…90" in joined and "<ntfy-Adresse>" in joined
    assert "<evcc-Adresse>/api/state" in joined and "<IP-1> and <IP-1> via <Host-1>" in joined
    assert "<E-Mail-1>" in joined and "/home/<Benutzer>/openampere" in joined
    assert "Protokoll (letzte" in text and "ERROR openampere.test" in text
    assert report["checks"][0]["id"] == "connection" and report["checks"][0]["status"] == "error"


def test_diagnostics_api_without_inverter(tmp_path):
    client = TestClient(create_app(Runtime({}, Storage(tmp_path / "t.db"))))
    login(client)
    response = client.post("/api/diagnostics")
    assert response.status_code == 200
    report = response.json()["report"]
    assert report["checks"][0] == {**report["checks"][0], "id": "connection", "status": "skipped"}
    assert "Gerät: nicht erkannt" in response.json()["markdown"]
