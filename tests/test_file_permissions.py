# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""The database and its copies are only for the app user (GHSA-p5pf-45jm-94c3)."""

from openampere.storage import Storage


def test_the_database_and_its_copies_are_only_for_the_app_user(tmp_path):
    """GHSA-p5pf-45jm-94c3: older versions created them readable for every user of the host."""
    import os
    import stat
    path = tmp_path / "openampere.db"
    Storage(path).close()
    for name in (path, tmp_path / "openampere.db.before-restore-20260101-000000"):
        name.touch()
        os.chmod(name, 0o644)
    storage = Storage(path)
    for name in tmp_path.glob("openampere.db*"):
        assert stat.S_IMODE(os.stat(name).st_mode) & 0o077 == 0, name
    storage.close()
