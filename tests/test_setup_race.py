# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Of two first setups at the same time only one sets the password (GHSA-m3qh-j9xv-jf9m)."""

from fastapi.testclient import TestClient

from openampere.api import create_app
from openampere.runtime import Runtime
from openampere.storage import Storage


def test_of_two_setups_at_the_same_time_only_one_sets_the_password(tmp_path):
    """GHSA-m3qh-j9xv-jf9m: the second one silently replaced the password the owner had just set."""
    from concurrent.futures import ThreadPoolExecutor
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    app = create_app(runtime)

    def setup(password):
        client = TestClient(app)
        client.headers["x-openampere"] = "1"
        return password, client.post("/api/auth/setup", json={"password": password}).status_code

    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(setup, [f"password-{i}" for i in range(8)]))
    winners = [password for password, status in results if status == 200]
    assert len(winners) == 1 and all(status in (200, 409) for _, status in results)
    client = TestClient(app)
    client.headers["x-openampere"] = "1"
    assert client.post("/api/auth/login", json={"password": winners[0]}).status_code == 200
    runtime.storage.close()
