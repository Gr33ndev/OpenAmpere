# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""A login or password change checked against the old password does not outlast a new password or a reset."""

import hashlib
import os
import sys
import threading

import pytest
from fastapi.testclient import TestClient

from openampere import __main__ as cli
from openampere import auth as auth_module
from openampere.auth import Auth
from openampere.storage import Storage

from conftest import PASSWORD as OLD
from conftest import login
from test_security import app_client

NEW = "neues-passwort-456"


class HeldCheck:
    """Holds the first password check after its scrypt hash, before the login or change goes on with it."""

    def __init__(self, monkeypatch):
        self.checked, self.release, self.calls = threading.Event(), threading.Event(), 0
        real = auth_module._hash

        def held(password, salt, *args, **kwargs):
            result = real(password, salt, *args, **kwargs)
            self.calls += 1
            if self.calls == 1:
                self.checked.set()
                assert self.release.wait(30)
            return result
        monkeypatch.setattr(auth_module, "_hash", held)

    def run(self, request):
        """Starts the request in a thread and returns once its password check is held."""
        result = {}
        thread = threading.Thread(target=lambda: result.update(response=request()))
        thread.start()
        assert self.checked.wait(30)
        return thread, result

    def finish(self, thread, result):
        self.release.set()
        thread.join(30)
        return result["response"]


def attacker_client(client):
    return TestClient(client.app, client=("192.0.2.66", 50000), headers={"x-openampere": "1"})


def test_a_login_checked_before_a_password_change_gets_no_valid_session(tmp_path, monkeypatch):
    runtime, owner = app_client(tmp_path)
    login(owner)
    attacker = attacker_client(owner)
    hold = HeldCheck(monkeypatch)
    try:
        thread, result = hold.run(lambda: attacker.post("/api/auth/login", json={"password": OLD}))
        assert owner.post("/api/auth/password", json={"current": OLD, "new": NEW}).status_code == 200
        hold.finish(thread, result)
        assert attacker.get("/api/auth/status").json()["authenticated"] is False
        assert attacker.get("/api/tokens").status_code == 401
        assert owner.get("/api/auth/status").json()["authenticated"] is True  # the owner's own new session works
        assert attacker.post("/api/auth/login", json={"password": OLD}).status_code == 401
    finally:
        hold.release.set()
        runtime.storage.close()


def test_a_login_checked_before_reset_password_gets_no_valid_session(tmp_path, monkeypatch):
    """`openampere reset-password` runs as another process, with its own connection to the database."""
    runtime, owner = app_client(tmp_path)
    login(owner)
    attacker = attacker_client(owner)
    config = tmp_path / "config.yaml"
    config.write_text(f"storage:\n  path: {runtime.storage.path}\n")
    monkeypatch.setattr(sys, "argv", ["openampere", "reset-password", "--config", str(config)])
    hold = HeldCheck(monkeypatch)
    try:
        thread, result = hold.run(lambda: attacker.post("/api/auth/login", json={"password": OLD}))
        cli.main()
        assert owner.get("/api/auth/status").json()["configured"] is False
        hold.finish(thread, result)
        assert owner.post("/api/auth/setup", json={"password": NEW}).status_code == 200
        assert attacker.get("/api/auth/status").json()["authenticated"] is False
        assert attacker.get("/api/tokens").status_code == 401
        assert owner.get("/api/auth/status").json()["authenticated"] is True
    finally:
        hold.release.set()
        runtime.storage.close()


def test_a_change_checked_before_another_change_does_not_replace_the_new_password(tmp_path, monkeypatch):
    runtime, owner = app_client(tmp_path)
    login(owner)
    attacker = login(attacker_client(owner))
    hold = HeldCheck(monkeypatch)
    try:
        thread, result = hold.run(lambda: attacker.post("/api/auth/password",
                                                        json={"current": OLD, "new": "anderes-passwort-789"}))
        assert owner.post("/api/auth/password", json={"current": OLD, "new": NEW}).status_code == 200
        assert hold.finish(thread, result).status_code == 401
        assert owner.get("/api/auth/status").json()["authenticated"] is True
        fresh = TestClient(owner.app, client=("192.0.2.20", 50000), headers={"x-openampere": "1"})
        assert fresh.post("/api/auth/login", json={"password": NEW}).status_code == 200
    finally:
        hold.release.set()
        runtime.storage.close()


def test_a_login_with_an_old_format_hash_does_not_bring_back_the_old_password(tmp_path, monkeypatch):
    """A hash of a version before 0.21 (scrypt p=1) is stored again at the next login: never over a newer password."""
    storage = Storage(tmp_path / "t.db")
    salt = os.urandom(16)
    storage.set_meta("auth", {"salt": salt.hex(),
                              "hash": hashlib.scrypt(OLD.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32).hex()})
    auth = Auth(storage)
    hold = HeldCheck(monkeypatch)
    try:
        thread, result = hold.run(lambda: auth.check_password(OLD, "192.0.2.66"))
        auth.set_password(NEW)  # e.g. by an admin of Home Assistant in the sidebar
        hold.finish(thread, result)
        assert auth.check_password(NEW, "192.0.2.1")
        assert not auth.check_password(OLD, "192.0.2.1")
    finally:
        hold.release.set()
        storage.close()


def test_sessions_of_older_versions_end_with_the_update(tmp_path):
    """They are not bound to a password: each device logs in once more."""
    runtime, owner = app_client(tmp_path)
    login(owner)
    runtime.storage.set_meta("sessions", {k: 4102444800 for k in runtime.storage.get_meta("sessions")})
    assert owner.get("/api/auth/status").json()["authenticated"] is False
    login(owner)
    assert owner.get("/api/auth/status").json()["authenticated"] is True
    runtime.storage.close()


@pytest.mark.parametrize("change", ["password", "logout"])
def test_a_new_password_or_logging_out_everywhere_still_ends_every_other_session(tmp_path, change):
    runtime, owner = app_client(tmp_path)
    login(owner)
    other = login(TestClient(owner.app, client=("192.0.2.20", 50000)))
    if change == "password":
        assert owner.post("/api/auth/password", json={"current": OLD, "new": NEW}).status_code == 200
    else:
        assert owner.post("/api/auth/logout?everywhere=true").status_code == 200
    assert other.get("/api/auth/status").json()["authenticated"] is False
    runtime.storage.close()
