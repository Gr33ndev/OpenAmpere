"""`openampere healthcheck`: the command Docker's HEALTHCHECK runs inside the container (#167)."""

import socket
import sys
import threading
import time

import pytest
import uvicorn

from openampere import __main__ as cli
from openampere.api import create_app
from openampere.runtime import Runtime
from openampere.storage import Storage


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def config_env(tmp_path, monkeypatch):
    """No config.yaml, the port comes from the environment like in the container."""
    port = free_port()
    monkeypatch.setenv("OPENAMPERE_CONFIG", str(tmp_path / "config.yaml"))
    monkeypatch.setenv("OPENAMPERE_SERVER_PORT", str(port))
    return port


@pytest.fixture
def server(tmp_path, config_env):
    """The real app (no inverter, no password set) on the configured port."""
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    srv = uvicorn.Server(uvicorn.Config(create_app(runtime), host="127.0.0.1", port=config_env, lifespan="off",
                                        log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not srv.started and time.monotonic() < deadline:
            time.sleep(0.05)
        assert srv.started
        yield srv
    finally:
        srv.should_exit = True
        thread.join(10)


def test_healthy_when_the_server_answers(server):
    assert cli.healthcheck(None) == 0


def test_unhealthy_when_nothing_answers(config_env, capsys):
    assert cli.healthcheck(None, timeout=1) == 1
    assert "antwortet nicht" in capsys.readouterr().out


def test_port_from_config_file(tmp_path, server, monkeypatch):
    monkeypatch.delenv("OPENAMPERE_SERVER_PORT")
    path = tmp_path / "other.yaml"
    path.write_text(f"server:\n  port: {server.config.port}\n")
    assert cli.healthcheck(str(path)) == 0
    path.write_text(f"server:\n  port: {free_port()}\n")
    assert cli.healthcheck(str(path), timeout=1) == 1


def run_command(monkeypatch) -> int:
    monkeypatch.setattr(sys, "argv", ["openampere", "healthcheck"])
    with pytest.raises(SystemExit) as exit_:
        cli.main()
    return exit_.value.code


def test_command_exit_code_healthy(server, monkeypatch):
    assert run_command(monkeypatch) == 0


def test_command_exit_code_unhealthy(config_env, monkeypatch):
    assert run_command(monkeypatch) == 1


def test_health_checks_stay_out_of_the_access_log(server):
    """Every 30 seconds a line in the log would hide the messages that matter."""
    import logging
    import urllib.request

    seen = []
    handler = logging.Handler()
    handler.emit = lambda record: seen.append(record.getMessage())
    access = logging.getLogger("uvicorn.access")
    hide = cli._HideHealthchecks()
    uvicorn.Config(server.config.app)  # uvicorn configures its loggers again (second port): the filter stays
    access.addFilter(hide)
    uvicorn.Config(server.config.app)
    access.addHandler(handler)
    level = access.level
    access.setLevel(logging.INFO)
    try:
        assert hide in access.filters
        assert cli.healthcheck(None) == 0
        urllib.request.urlopen(f"http://127.0.0.1:{server.config.port}/api/auth/status", timeout=5).read()
        deadline = time.monotonic() + 5
        while not seen and time.monotonic() < deadline:
            time.sleep(0.05)
    finally:
        access.removeHandler(handler)
        access.removeFilter(hide)
        access.setLevel(level)
    assert len(seen) == 1 and "/api/auth/status " in seen[0] and "healthcheck" not in seen[0]
