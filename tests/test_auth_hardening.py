# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Login lockout per device and app tokens that stay revoked."""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from openampere.apitokens import ApiTokens
from openampere.auth import MAX_FAILED_LOGINS, MAX_FAILED_TOTAL, Auth, _client_key
from openampere.storage import Storage

from conftest import PASSWORD, login
from test_security import app_client


def test_one_device_trying_passwords_does_not_lock_out_the_others(tmp_path):
    runtime, client = app_client(tmp_path)
    login(client)
    guesser = TestClient(client.app, client=("192.0.2.66", 50000), headers={"x-openampere": "1"})
    codes = [guesser.post("/api/auth/login", json={"password": f"x{i}"}).status_code for i in range(6)]
    assert codes == [401] * 5 + [429]
    owner = TestClient(client.app, client=("192.0.2.10", 50000), headers={"x-openampere": "1"})
    assert owner.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200


def test_logins_at_the_same_time_do_not_get_past_the_limit(tmp_path):
    auth = Auth(Storage(tmp_path / "t.db"))
    auth.set_password(PASSWORD)

    def guess(i):
        try:
            return auth.check_password(f"x{i}", "192.0.2.66")
        except PermissionError:
            return "refused"
    with ThreadPoolExecutor(20) as pool:
        results = list(pool.map(guess, range(20)))
    assert results.count(False) == MAX_FAILED_LOGINS  # the others were refused before the check


def test_many_addresses_do_not_make_guessing_faster(tmp_path):
    auth = Auth(Storage(tmp_path / "t.db"))
    auth.set_password(PASSWORD)
    for i in range(MAX_FAILED_TOTAL):
        assert auth.check_password("wrong", f"192.0.2.{i}") is False
    with pytest.raises(PermissionError):
        auth.check_password(PASSWORD, "192.0.2.200")


@pytest.mark.parametrize(("a", "b", "same"), [("2001:db8::1", "2001:db8::abcd", True), ("2001:db8::1", "2001:db8:0:1::1", False),
                                              ("192.0.2.1", "::ffff:192.0.2.1", True), ("192.0.2.1", "192.0.2.2", False)])
def test_a_device_is_one_address_or_one_ipv6_network(a, b, same):
    assert (_client_key(a) == _client_key(b)) is same


def test_a_revoked_app_token_stays_revoked_while_it_is_checked(tmp_path):
    """The check runs in the event loop, the revoke in a worker thread: the check must not write the old list back."""
    storage = Storage(tmp_path / "t.db")
    tokens = ApiTokens(storage)
    token, entry = tokens.create("Home Assistant", "read")
    read = storage.get_meta
    revoking: list[threading.Thread] = []

    def get_meta(key):
        value = read(key)
        if key == "api_tokens" and not revoking:  # the check has read the list: now the owner revokes
            revoking.append(threading.Thread(target=tokens.revoke, args=(entry["id"],)))
            revoking[0].start()
            revoking[0].join(0.5)
        return value
    storage.get_meta = get_meta
    tokens.check(token)  # first use: writes last_used
    revoking[0].join()
    storage.get_meta = read
    assert tokens.check(token) is None
