# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Going back to an older version must not bring back an old password, sessions or revoked app tokens.

The older version continues with the copy of the database made before the upgrade (#166). The released versions
take that copy as it is, so the newer version keeps the password, sessions, app tokens and device state in the copy
those of the running installation."""

import sqlite3
import sys
from contextlib import contextmanager

import pytest

from openampere import __main__ as cli
from openampere import storage as storage_module
from openampere.apitokens import ApiTokens
from openampere.auth import Auth
from openampere.storage import Storage

OLD_PASSWORD = "altes-passwort-1"
NEW_PASSWORD = "neues-passwort-2"


@contextmanager
def older_version():
    """OpenAmpere before the upgrade (schema version 1, no migrations): its rollback is the one of the released
    versions, which take the copy as it is."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(storage_module, "MIGRATIONS", {})
        patch.setattr(storage_module, "SCHEMA_VERSION", 1)
        yield


def old_installation(path, password: str | None = OLD_PASSWORD) -> dict:
    """An installation of the older version; returns its session and app token."""
    with older_version():
        storage = Storage(path)
        try:
            auth = Auth(storage)
            session = auth.create_session(auth.setup(password)) if password else None
            token, entry = ApiTokens(storage).create("Home Assistant", "control")
        finally:
            storage.close()
    return {"session": session, "token": token, "token_id": entry["id"]}


@contextmanager
def newer_version(path):
    storage = Storage(path)  # upgrades the database and keeps the copy
    try:
        assert list(path.parent.glob(f"{path.name}.schema-1"))
        yield storage
    finally:
        storage.close()


@contextmanager
def rolled_back(path):
    """The updater (or the owner) went back to the older version."""
    with older_version():
        storage = Storage(path)
        try:
            assert storage.get_meta("database_rollback")
            yield storage
        finally:
            storage.close()


def test_a_changed_password_revoked_sessions_and_tokens_stay_so_after_a_rollback(tmp_path):
    path = tmp_path / "openampere.db"
    old = old_installation(path)

    with newer_version(path) as storage:
        auth, tokens = Auth(storage), ApiTokens(storage)
        assert auth.valid(old["session"]) and tokens.check(old["token"])  # the upgrade keeps them
        auth.set_password(NEW_PASSWORD)
        auth.revoke(None, everywhere=True)  # "log out everywhere"
        tokens.revoke(old["token_id"])
        new_session = auth.create_session(auth.verify_password(NEW_PASSWORD))

    with rolled_back(path) as storage:
        auth, tokens = Auth(storage), ApiTokens(storage)
        assert not auth.check_password(OLD_PASSWORD)
        assert auth.check_password(NEW_PASSWORD)
        assert not auth.valid(old["session"])
        assert auth.valid(new_session)
        assert tokens.check(old["token"]) is None
        assert auth.configured  # no first-password setup for anybody in the home network


def test_a_password_set_first_after_the_upgrade_keeps_the_setup_closed_after_a_rollback(tmp_path):
    path = tmp_path / "openampere.db"
    old_installation(path, password=None)

    with newer_version(path) as storage:
        assert Auth(storage).setup(NEW_PASSWORD)

    with rolled_back(path) as storage:
        auth = Auth(storage)
        assert auth.configured and not auth.locked
        assert auth.check_password(NEW_PASSWORD)


def test_a_new_app_token_and_a_newer_login_survive_a_rollback(tmp_path):
    """The other direction: what was set up in the newer version keeps working, nobody has to pair again."""
    path = tmp_path / "openampere.db"
    old_installation(path)

    with newer_version(path) as storage:
        auth = Auth(storage)
        session = auth.create_session(auth.verify_password(OLD_PASSWORD))
        token, _ = ApiTokens(storage).create("Home Assistant 2", "read")

    with rolled_back(path) as storage:
        assert Auth(storage).valid(session)
        assert ApiTokens(storage).check(token)


def test_reset_password_on_the_command_line_also_resets_the_copy(tmp_path, monkeypatch):
    path = tmp_path / "openampere.db"
    old = old_installation(path)

    with newer_version(path):
        monkeypatch.setenv("OPENAMPERE_STORAGE_PATH", str(path))
        monkeypatch.setattr(sys, "argv", ["openampere", "reset-password"])
        cli.main()  # while the newer version runs, in another process

    with rolled_back(path) as storage:
        auth = Auth(storage)
        assert not auth.check_password(OLD_PASSWORD)
        assert not auth.valid(old["session"])


def test_a_copy_that_missed_a_change_is_brought_up_to_date_at_the_next_start(tmp_path):
    path = tmp_path / "openampere.db"
    old = old_installation(path)
    with newer_version(path):
        pass
    db = sqlite3.connect(path)  # e.g. changed by another program while OpenAmpere was not running
    with db:
        db.execute("DELETE FROM meta WHERE key IN ('auth', 'sessions')")
    db.close()

    with newer_version(path):
        pass

    with rolled_back(path) as storage:
        assert not Auth(storage).valid(old["session"])
        assert not Auth(storage).check_password(OLD_PASSWORD)
