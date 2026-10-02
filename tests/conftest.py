"""Shared test helpers."""

import pytest
from fastapi.testclient import TestClient

PASSWORD = "geheim123"


def login(client: TestClient) -> TestClient:
    """Sets the access password (first run) or logs in, and adds the header every write needs."""
    client.headers.update({"x-openampere": "1"})
    status = client.get("/api/auth/status").json()
    if not status["configured"]:
        assert client.post("/api/auth/setup", json={"password": PASSWORD}).status_code == 200
    elif not status["authenticated"]:
        assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
    return client


@pytest.fixture
def authed():
    return login
