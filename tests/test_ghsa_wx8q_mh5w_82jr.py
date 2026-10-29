# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""A restored backup neither takes over which wallbox charges OpenAmpere started nor real control of a wallbox, and
stored wallboxes go through the address checks of the app (GHSA-wx8q-mh5w-82jr)."""

from types import SimpleNamespace

import pytest

from openampere.secretbox import SecretBox
from openampere.storage import KEEP_ON_RESTORE, Storage
from openampere.wallbox import native
from openampere.wallbox.base import Reading
from openampere.wallbox.native import OWNED, STOPPED, NativeWallboxes

INVERTER = ("192.168.1.10", 502)
WB = {"name": "Box", "model": "foxess_ac", "host": "192.168.1.50", "port": 502, "unit": 1, "mode": "manual",
      "id": "aaaaaaaa", "control": True, "enabled": True, "max_current_a": 16}
OUTSIDE = {**WB, "name": "Fremd", "host": "8.8.8.8", "id": "bbbbbbbb"}  # an address on the internet
AT_INVERTER = {**WB, "name": "Falsch", "host": INVERTER[0], "id": "cccccccc"}
STARTED = {"current_a": 6, "started": True, "stopped": False, "wallbox": WB}


def restored(tmp_path, running: dict, backup: dict) -> Storage:
    """An existing installation with the meta values `running` restores a backup with `backup`; the storage after
    the next start (which applies the restore)."""
    path, other = tmp_path / "openampere.db", tmp_path / "other.db"
    storage = Storage(path)
    for key, value in running.items():
        storage.set_meta(key, value)
    storage.close()
    source = Storage(other)
    for key, value in backup.items():
        source.set_meta(key, value)
    source.backup(tmp_path / "backup.db")
    source.close()
    storage = Storage(path)
    storage.stage_restore(tmp_path / "backup.db", settings=lambda _backup, current: current, source="Test")
    storage.close()
    return Storage(path)


def wallboxes(tmp_path, storage: Storage, *, enabled=False, dry_run=True) -> NativeWallboxes:
    config = SimpleNamespace(inverter=SimpleNamespace(host=INVERTER[0], port=INVERTER[1]),
                             control=SimpleNamespace(enabled=enabled, dry_run=dry_run))
    return NativeWallboxes(SimpleNamespace(storage=storage, secrets=SecretBox(tmp_path / "secret.key"),
                                           config=config))


@pytest.fixture
def writes(monkeypatch):
    """Every write a release would send; the charger reports a connected car that charges."""
    sent = []

    async def read(self, wb, live=None):
        return live

    async def write(self, wb, live, values, action, value, reason, *, safe=False):
        sent.append((wb.host, wb.port, values, safe))
        return True

    reading = Reading(state="charging", connected=True, charging=True, set_current_a=None)
    monkeypatch.setattr(NativeWallboxes, "read", read)
    monkeypatch.setattr(NativeWallboxes, "_fresh", staticmethod(lambda live, now: reading))
    monkeypatch.setattr(NativeWallboxes, "_write", write)
    return sent


def test_wallbox_ownership_is_device_state_of_the_installation():
    assert OWNED in KEEP_ON_RESTORE and STOPPED in KEEP_ON_RESTORE


async def test_a_backup_does_not_make_openampere_stop_a_charge_it_did_not_start(tmp_path, writes):
    storage = restored(tmp_path, {"wallboxes": [WB], OWNED: {}, STOPPED: {"aaaaaaaa": 1.0}},
                       {"wallboxes": [WB], OWNED: {"aaaaaaaa": STARTED}, STOPPED: {}})
    try:
        assert storage.get_meta(OWNED) == {} and storage.get_meta(STOPPED) == {"aaaaaaaa": 1.0}
        boxes = wallboxes(tmp_path, storage)
        await boxes.release_all("Steuerung ausgeschaltet")
        assert writes == []  # no stop sent, also not as a safe write in test mode
    finally:
        storage.close()


async def test_a_backup_does_not_make_openampere_forget_a_charge_it_started(tmp_path, writes):
    storage = restored(tmp_path, {"wallboxes": [WB], OWNED: {"aaaaaaaa": STARTED}}, {"wallboxes": [WB]})
    try:
        assert storage.get_meta(OWNED) == {"aaaaaaaa": STARTED}
        await wallboxes(tmp_path, storage).release_all("Steuerung ausgeschaltet")
        assert [(host, safe) for host, _port, _values, safe in writes] == [(WB["host"], True)]  # stopped
        assert native.MODELS["foxess_ac"].start(False).items() <= writes[0][2].items()
    finally:
        storage.close()


def test_a_backup_does_not_switch_on_real_control(tmp_path):
    other = {**WB, "name": "Neu", "host": "192.168.1.51", "id": "dddddddd"}
    storage = restored(tmp_path, {"wallboxes": [{**WB, "control": False}]}, {"wallboxes": [WB, other]})
    try:
        assert [wb["control"] for wb in storage.get_meta("wallboxes")] == [False, False]  # unknown id: off
        boxes = wallboxes(tmp_path, storage).wallboxes
        assert [wb.id for wb in boxes] == ["aaaaaaaa", "dddddddd"] and not any(native.real(wb) for wb in boxes)
        entry = storage.control_log()[0]
        assert entry["result"] == "Datensicherung wiederhergestellt"
        assert entry["details"]["wallbox_control_kept"] == [
            {"id": "aaaaaaaa", "name": "Box", "backup": True, "kept": False},
            {"id": "dddddddd", "name": "Neu", "backup": True, "kept": False}]
    finally:
        storage.close()


def test_a_backup_keeps_real_control_the_owner_agreed_to(tmp_path):
    storage = restored(tmp_path, {"wallboxes": [WB]}, {"wallboxes": [{**WB, "control": False, "name": "Alt"}]})
    try:
        assert storage.get_meta("wallboxes")[0]["control"] is True
        assert storage.get_meta("wallboxes")[0]["name"] == "Alt"  # the rest comes from the backup
    finally:
        storage.close()


def test_stored_wallboxes_at_addresses_the_app_refuses_are_ignored(tmp_path):
    storage = Storage(tmp_path / "openampere.db")
    try:
        storage.set_meta("wallboxes", [WB, OUTSIDE, AT_INVERTER, {**AT_INVERTER, "id": "eeeeeeee", "port": 503}])
        # another port of the inverter's address can be another device, e.g. behind a Modbus gateway (#417)
        assert [wb.id for wb in wallboxes(tmp_path, storage).wallboxes] == ["aaaaaaaa", "eeeeeeee"]
    finally:
        storage.close()


async def test_a_wallbox_openampere_controlled_is_let_go_of_only_at_an_allowed_address(tmp_path, writes):
    storage = restored(tmp_path, {}, {OWNED: {
        "bbbbbbbb": {**STARTED, "wallbox": OUTSIDE}, "cccccccc": {**STARTED, "wallbox": AT_INVERTER}}})
    try:
        assert storage.get_meta(OWNED) is None  # not from the backup
        storage.set_meta(OWNED, {"bbbbbbbb": {**STARTED, "wallbox": OUTSIDE},
                                 "cccccccc": {**STARTED, "wallbox": AT_INVERTER},
                                 "ffffffff": {**STARTED, "wallbox": {**WB, "id": "ffffffff"}}})
        boxes = wallboxes(tmp_path, storage)
        boxes.restore()
        assert sorted(boxes._retired) == ["ffffffff"]
        await boxes.retire()
        assert [host for host, *_ in writes] == [WB["host"]]
    finally:
        storage.close()
