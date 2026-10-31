# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Enphase IQ Gateway with IQ Batteries (#522): display only, chosen in the setup with a token from Enphase."""

import asyncio
import base64
import hashlib
import json
import ssl
import time
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from openampere import discovery
from openampere.api import create_app
from openampere.diagnostics import Diagnostics
from openampere.drivers import registry
from openampere.drivers.enphase.driver import (INFO, PRODUCTION, CertificateChanged, EnphaseDriver, TokenRejected,
                                              to_snapshot, token_expiry)
from openampere.drivers.modbus import DetectionFailed, DeviceRefused, DeviceUnreachable, friendly_error
from openampere.runtime import Runtime
from openampere.simulator import SimulatedEnphase, self_signed_tls
from openampere.storage import Storage
from conftest import login


def jwt(exp: float) -> str:
    """A token shaped like Enphase's: only its expiry matters here (the gateway checks it, OpenAmpere only reads it)."""
    def part(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")
    return f"{part({'alg': 'ES256'})}.{part({'exp': int(exp)})}.c2lnbmF0dXJl"


TLS = self_signed_tls()


async def serve(sim, tls: bool = True):
    """Like a real gateway: HTTPS with a self-signed certificate (tls=False: one that answers plain HTTP)."""
    server = await asyncio.start_server(sim.serve_client, "127.0.0.1", 0, ssl=TLS if tls else None)
    return server, server.sockets[0].getsockname()[1]


def sunny(sim):
    sim.energy.step(datetime(2026, 6, 1, 13, 0), 60)
    return sim


def test_the_expiry_of_the_token():
    assert token_expiry(jwt(1_900_000_000)) == 1_900_000_000
    assert token_expiry("not a token") is None and token_expiry("") is None


@pytest.mark.parametrize("payload", [b"[1]", b'"text"', b'{"exp": Infinity}', b'{"exp": NaN}', b'{"exp": "soon"}'])
def test_an_odd_token_has_no_expiry_and_does_not_stop_the_driver(payload):
    """A pasted text that decodes to JSON but no object, or an exp that is not a finite number: no expiry, no crash
    (the driver is made when the settings are saved and at every start)."""
    token = "x." + base64.urlsafe_b64encode(payload).decode().rstrip("=") + ".y"
    assert token_expiry(token) is None
    assert EnphaseDriver("192.0.2.1", token=token).token_expires is None


async def test_read_with_the_token_and_its_signs():
    sim = sunny(SimulatedEnphase(token="t1"))
    server, port = await serve(sim)
    async with server:
        info = await registry.detect("127.0.0.1", port, only="enphase", token="t1")
        driver = registry.create("enphase", "127.0.0.1", port, 0, timeout=3, token="t1")
        try:
            await driver.connect()
            snap = (await driver.read()).sanitize()
        finally:
            await driver.close()
    assert (info.driver, info.manufacturer, info.serial, info.firmware) == ("enphase", "Enphase", "122100000001", "D8.2.4382")
    assert not info.supports_control
    e = sim.energy
    assert e.grid_w < 0 and e.battery_w < 0  # exporting and charging
    assert snap.pv_power == pytest.approx(e.pv_w, abs=0.5)
    assert snap.grid_power == pytest.approx(e.grid_w, abs=0.5)  # + = import
    assert snap.house_power == pytest.approx(e.house_w, abs=0.5)
    assert snap.battery_power == pytest.approx(e.battery_w, abs=0.5)  # + = discharging
    assert snap.battery_soc == round(e.soc) and snap.totals.pv is None
    assert all(authorised for path, authorised in sim.requests if path != "/info")


@pytest.mark.parametrize(("token", "words"), [("", "braucht ein Zugangs-Token"), ("wrong", "lehnt das Zugangs-Token ab")])
async def test_a_missing_or_wrong_token_says_what_to_do(token, words):
    sim = SimulatedEnphase(token="t1")
    server, port = await serve(sim)
    async with server:
        with pytest.raises(TokenRejected) as caught:
            await registry.detect("127.0.0.1", port, only="enphase", token=token)
    assert words in friendly_error(caught.value) and "Modbus" not in friendly_error(caught.value)


async def test_a_gateway_without_batteries_still_shows_pv_and_grid():
    sim = sunny(SimulatedEnphase(token="t1", batteries=False))
    server, port = await serve(sim)
    async with server:
        driver = registry.create("enphase", "127.0.0.1", port, 0, timeout=3, token="t1")
        try:
            await driver.connect()
            snap = await driver.read()
        finally:
            await driver.close()
    assert snap.pv_power is not None and snap.battery_power is None and snap.battery_soc is None


@pytest.mark.parametrize(("refused", "missing", "battery", "soc"), [
    ({"/ivp/ensemble/inventory"}, set(), True, False),  # the token is refused there only
    (set(), {"/ivp/livedata/status"}, False, True),  # older software without the endpoint
])
async def test_each_battery_value_on_its_own(refused, missing, battery, soc):
    """/production.json took the token; a battery endpoint that refuses it or is missing drops only its own value."""
    sim = sunny(SimulatedEnphase(token="t1"))
    sim.refused = refused
    answer = sim.answer
    sim.answer = lambda path: None if path in missing else answer(path)
    server, port = await serve(sim)
    async with server:
        driver = registry.create("enphase", "127.0.0.1", port, 0, timeout=3, token="t1")
        try:
            await driver.connect()
            snap = await driver.read()
        finally:
            await driver.close()
    assert snap.pv_power is not None
    assert (snap.battery_power is not None, snap.battery_soc is not None) == (battery, soc)


async def test_a_new_token_is_tried_although_the_gateway_is_connected(tmp_path):
    """The owner pastes a new token while the old one still works: the test must try the new one."""
    sim = sunny(SimulatedEnphase(token="t1"))
    server, port = await serve(sim)
    async with server:
        runtime = Runtime({}, Storage(tmp_path / "t.db"))
        try:
            await runtime.update_settings({"inverter.host": "127.0.0.1", "inverter.port": port,
                                           "inverter.driver": "enphase", "inverter.token": "t1",
                                           "inverter.poll_interval": 2})
            for _ in range(150):
                if runtime.collector.connected:
                    break
                await asyncio.sleep(0.1)
            wrong = await discovery.test_connection(runtime, "127.0.0.1", port, 0, "enphase", token="typo")
            right = await discovery.test_connection(runtime, "127.0.0.1", port, 0, "enphase", token="t1")
        finally:
            await runtime.collector.stop()
    assert not wrong["ok"] and "lehnt das Zugangs-Token ab" in wrong["error"]
    assert right["ok"]


async def test_the_token_never_goes_over_plain_http():
    """A gateway that answers only plain HTTP gets no token: it would cross the network in clear text."""
    sim = sunny(SimulatedEnphase(token="t1"))
    server, port = await serve(sim, tls=False)  # plain HTTP only
    async with server:
        with pytest.raises(DetectionFailed) as caught:
            await registry.detect("127.0.0.1", port, only="enphase", token="t1")
    # not "refused": the token was never sent, a new one from Enphase would not help
    assert "nur unverschlüsselt" in friendly_error(caught.value) and "abgelaufen" not in friendly_error(caught.value)
    assert not any(authorised for _path, authorised in sim.requests)


async def test_an_idle_battery_stays_when_the_inventory_fails_once():
    """Once the gateway listed IQ Batteries, 0 W is an idle battery, also in a reading whose inventory failed."""
    sim = sunny(SimulatedEnphase(token="t1"))
    sim.energy.battery_w = 0  # idle: at its reserve or full
    server, port = await serve(sim)
    async with server:
        driver = registry.create("enphase", "127.0.0.1", port, 0, timeout=3, token="t1")
        try:
            await driver.connect()
            first = await driver.read()
            sim.refused = {"/ivp/ensemble/inventory"}
            later = await driver.read()
        finally:
            await driver.close()
    assert first.battery_power == 0 and first.battery_soc is not None
    assert later.battery_power == 0 and later.battery_soc is None  # still a battery, only without its charge


def test_inverters_count_when_the_production_meter_is_off():
    production = {"production": [{"type": "inverters", "wNow": 3200},
                                 {"type": "eim", "measurementType": "production", "activeCount": 0, "wNow": 0}],
                  "consumption": [{"measurementType": "net-consumption", "activeCount": 0, "wNow": 5}]}
    snap = to_snapshot(production, None, None, 0)
    assert snap.pv_power == 3200 and snap.grid_power is None  # no consumption meter: no grid value


async def test_another_web_server_is_no_gateway_and_nothing_listening_is_unreachable():
    async def other(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 13\r\nConnection: close\r\n\r\n<html></html>")
        await writer.drain()
        writer.close()
    server = await asyncio.start_server(other, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        with pytest.raises(DetectionFailed, match="nicht wie ein Enphase-Gateway"):
            await registry.detect("127.0.0.1", port, only="enphase", token="t1")
    with pytest.raises(DeviceUnreachable):
        await registry.detect("127.0.0.1", port, only="enphase", token="t1")


async def test_never_written():
    driver = EnphaseDriver("192.0.2.1", token="t1")
    for write in (lambda: driver.write_soc_limits(min_soc=20), lambda: driver.set_remote_power(-1000, 180),
                  lambda: driver.write_export_limit(5000)):
        with pytest.raises(DeviceRefused, match="nur die Werte an"):
            await write()


def test_every_driver_can_be_tested_in_the_setup(tmp_path):
    """The setup test once allowed only "auto", "foxess" and "saj": choosing another driver failed with a validation
    error before anything was tried."""
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    client = login(TestClient(create_app(runtime)))
    for key in registry.DRIVERS:
        answer = client.post("/api/setup/test", json={"host": "127.0.0.1", "port": 9, "driver": key})
        assert answer.status_code == 200, (key, answer.text)


async def test_the_token_is_stored_encrypted_bound_to_the_address_and_shown_by_its_expiry(tmp_path):
    token = jwt(time.time() + 20 * 86400)  # runs out in 20 days
    sim = sunny(SimulatedEnphase(token=token))
    server, port = await serve(sim)
    async with server:
        runtime = Runtime({}, Storage(tmp_path / "t.db"))
        try:
            await runtime.update_settings({"inverter.host": "127.0.0.1", "inverter.port": port,
                                           "inverter.driver": "enphase", "inverter.token": token,
                                           "inverter.poll_interval": 2})
            for _ in range(150):
                if runtime.collector.latest:
                    break
                await asyncio.sleep(0.1)
            assert runtime.collector.device.driver == "enphase"
            assert runtime.collector.poll_interval() == 15.0
            # the setup test of the stored address uses the stored token
            assert (await discovery.test_connection(runtime, "127.0.0.1", port, 0, "enphase"))["ok"]
            report = await Diagnostics(runtime).run()
        finally:
            await runtime.collector.stop()
    stored = runtime.storage.get_settings()
    assert stored["inverter.token"] and token not in json.dumps(stored)  # stored, but never in plain text
    check = {c["id"]: c for c in report["checks"]}["enphase_token"]
    assert check["status"] == "warn" and check["summary"] in ("Noch 19 Tage gültig.", "Noch 20 Tage gültig.")
    assert "." not in check["summary"].split("Noch")[0]  # no date that would tell the day the token was made
    assert token not in json.dumps(report)


async def fingerprint(port: int) -> str:
    """SHA-256 of the certificate the server on port shows (in a thread: the server runs on this event loop)."""
    pem = await asyncio.to_thread(ssl.get_server_certificate, ("127.0.0.1", port))
    return hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()


async def test_the_token_goes_only_to_the_pinned_certificate():
    """Someone in the home network who takes over the gateway's address shows another certificate (#539): nothing
    is sent to it, not even /info, and the app says what to do."""
    sim = sunny(SimulatedEnphase(token="t1"))
    server, port = await serve(sim)
    async with server:
        with pytest.raises(DeviceUnreachable) as caught:
            await registry.detect("127.0.0.1", port, only="enphase", token="t1", cert_sha256="0" * 64)
        pin = await fingerprint(port)
        info = await registry.detect("127.0.0.1", port, only="enphase", token="t1", cert_sha256=pin)
    assert "anderen Zertifikat" in friendly_error(caught.value) and "noch einmal ein" in friendly_error(caught.value)
    assert info.driver == "enphase"
    assert sim.requests[0] == (INFO, False)  # the requests of the second detection only
    assert all(path != PRODUCTION or authorised for path, authorised in sim.requests)


async def test_without_a_pin_the_first_certificate_of_a_detection_counts():
    """Trust on first use, but only for the server that answered as the gateway; a change within the detection
    stops it."""
    driver = EnphaseDriver("127.0.0.1", token="t1")
    driver._check_certificate(b"gateway")
    with pytest.raises(CertificateChanged):
        driver._check_certificate(b"someone else")
    assert driver.cert_sha256 is None  # pinned only once the detection succeeded


async def test_the_setup_pins_the_certificate_of_a_new_token_and_needs_the_token_again_for_another(tmp_path):
    sim = sunny(SimulatedEnphase(token="t1"))
    server, port = await serve(sim)
    async with server:
        runtime = Runtime({}, Storage(tmp_path / "t.db"))
        try:
            # the setup test with a typed token tells the certificate it went to, the setup saves it with the token
            tested = await discovery.test_connection(runtime, "127.0.0.1", port, 0, "enphase", token="t1")
            assert tested["ok"] and tested["cert_sha256"] == await fingerprint(port)
            await runtime.update_settings({"inverter.host": "127.0.0.1", "inverter.port": port,
                                           "inverter.driver": "enphase", "inverter.token": "t1",
                                           "inverter.cert_sha256": tested["cert_sha256"].upper()})
            assert runtime.config.inverter.cert_sha256 == await fingerprint(port)
            # a certificate never comes without the token: else a session could point the stored token elsewhere
            with pytest.raises(PermissionError, match="nur zusammen mit dem Zugangs-Token"):
                await runtime.update_settings({"inverter.cert_sha256": "a" * 64})
            with pytest.raises(ValueError, match="Fingerabdruck"):
                await runtime.update_settings({"inverter.token": "t1", "inverter.cert_sha256": "nope"})
        finally:
            await runtime.collector.stop()
    # the gateway was set up again: another certificate on the same address
    other = SimulatedEnphase(token="t1")
    server = await asyncio.start_server(other.serve_client, "127.0.0.1", port, ssl=self_signed_tls())
    async with server:
        try:
            stored = await discovery.test_connection(runtime, "127.0.0.1", port, 0, "enphase")
            again = await discovery.test_connection(runtime, "127.0.0.1", port, 0, "enphase", token="t1")
            new = await fingerprint(port)
        finally:
            await runtime.collector.stop()
    assert not stored["ok"] and "anderen Zertifikat" in stored["error"]
    assert other.requests == []  # nothing at all reached the server with the other certificate
    assert again["ok"] and again["cert_sha256"] == new != runtime.config.inverter.cert_sha256
    # another address forgets token and certificate together
    await runtime.update_settings({"inverter.host": "127.0.0.2"})
    assert (runtime.config.inverter.token, runtime.config.inverter.cert_sha256) == ("", "")
