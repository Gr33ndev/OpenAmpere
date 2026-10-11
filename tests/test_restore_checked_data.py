# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""A restored backup brings only checked devices and charging settings (GHSA-4jmw-6h58-vc78)."""

import json
import sqlite3

from conftest import login
from test_database_recovery import backup_of, restart, start


def test_a_restored_backup_brings_only_checked_devices_charging_settings_and_no_update_notice(tmp_path):
    """GHSA-4jmw-6h58-vc78: data besides the settings was taken over unchecked, and a control switched on by the
    restore did not show in the control log."""
    from openampere.charging import GridCharging
    from openampere.consumers import SurplusControl
    path = tmp_path / "openampere.db"
    runtime, client = start(path)
    login(client)
    backup = tmp_path / "crafted-data.db"
    backup.write_bytes(backup_of(client, tmp_path))
    db = sqlite3.connect(backup)
    with db:
        meta = {"consumers": [{"id": "a", "name": "Pumpe", "kind": "http", "url_on": "file:///etc/passwd",
                               "url_off": "http://pumpe.local/off"},
                              {"id": "b", "name": "Heizung", "kind": "shelly1", "host": "heizung.local"}],
                "grid_charging": {"enabled": True, "legal_confirmed": True, "target_soc": 500},
                "update_latest": {"version": "99.0.0", "url": "https://attacker.example/"}}
        for key, value in meta.items():
            db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, json.dumps(value)))
        row = db.execute("SELECT value FROM meta WHERE key='settings'").fetchone()
        settings = {**(json.loads(row[0]) if row else {}), "control.enabled": True, "control.dry_run": False}
        db.execute("INSERT OR REPLACE INTO meta VALUES ('settings', ?)", (json.dumps(settings),))
    db.close()
    runtime.restart_hook = lambda: None
    assert client.post("/api/backup/restore", content=backup.read_bytes()).status_code == 200

    runtime, client = restart(runtime, client)
    assert [c.name for c in SurplusControl(runtime).consumers] == ["Heizung"]
    charging = GridCharging(runtime).settings
    assert not charging.enabled and charging.target_soc != 500
    assert runtime.storage.get_meta("update_latest") is None
    entry = runtime.storage.control_log()[0]
    assert entry["action"] == "control_switches" and entry["result"] == "Datensicherung wiederhergestellt"
    assert entry["details"]["to"] == {"control.enabled": True, "control.dry_run": False}
    runtime.storage.close()
