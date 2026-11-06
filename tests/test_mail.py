# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Notifications by e-mail (#594), against a fake mail server on 127.0.0.1: no real network."""

import base64
import datetime
import email
import functools
import ipaddress
import socket
import ssl
import threading
import time
from email import policy
from zoneinfo import ZoneInfo

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient

from openampere import mail
from openampere import notify as notify_module
from openampere.api import create_app
from openampere.config import NotifyConfig, validate
from openampere.notify import OUTBOX_MAX_AGE_S, RETRY_S, Notifier
from openampere.runtime import Runtime
from openampere.storage import Storage

from conftest import login

TZ = ZoneInfo("Europe/Berlin")
USER = "owner@example.com"
PASSWORD = "mail-pass-1234"


def certificate(folder):
    """A self-signed certificate for 127.0.0.1, and a client context that trusts it."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    cert_path, key_path = folder / "cert.pem", folder / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.load_cert_chain(cert_path, key_path)
    return server, ssl.create_default_context(cafile=str(cert_path))


class FakeServer:
    """Just enough SMTP: EHLO, STARTTLS, AUTH PLAIN (offered only once encrypted, like real servers), MAIL, RCPT,
    DATA, QUIT. silent: accepts connections and never answers."""

    def __init__(self, tls_context, *, tls=False, starttls=True, silent=False, refuse_sender=False):
        self.tls_context, self.tls, self.starttls = tls_context, tls, starttls
        self.silent, self.refuse_sender = silent, refuse_sender
        self.messages: list[email.message.EmailMessage] = []
        self.envelopes: list[tuple[str, list[str]]] = []
        self.logins: list[tuple[str, str, bool]] = []  # user, password, encrypted
        self.sock = socket.create_server(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.open: list[socket.socket] = []
        threading.Thread(target=self._accept, daemon=True).start()

    def close(self):
        self.sock.close()
        for conn in self.open:
            conn.close()

    def _accept(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            self.open.append(conn)
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        try:
            if self.silent:
                conn.recv(1)  # until the client gives up
                return
            encrypted = self.tls
            if encrypted:
                conn = self.tls_context.wrap_socket(conn, server_side=True)
            f = conn.makefile("rwb")

            def reply(*lines):
                for i, line in enumerate(lines):
                    sep = " " if i == len(lines) - 1 else "-"
                    f.write(f"{line[:3]}{sep}{line[4:]}\r\n".encode())
                f.flush()

            reply("220 fake ESMTP")
            sender, recipients = "", []
            while True:
                line = f.readline()
                if not line:
                    return
                command = line.decode().rstrip("\r\n")
                verb = command.split(" ")[0].upper()
                if verb in ("EHLO", "HELO"):
                    extensions = ["250 fake", "250 8BITMIME"]
                    if self.starttls and not encrypted:
                        extensions.append("250 STARTTLS")
                    if encrypted:
                        extensions.append("250 AUTH PLAIN")
                    reply(*extensions)
                elif verb == "STARTTLS":
                    reply("220 go ahead")
                    f.close()
                    conn = self.tls_context.wrap_socket(conn, server_side=True)
                    f = conn.makefile("rwb")
                    encrypted = True
                elif verb == "AUTH":
                    _, user, password = base64.b64decode(command.split(" ")[2]).decode().split("\0")
                    self.logins.append((user, password, encrypted))
                    reply("235 ok" if (user, password) == (USER, PASSWORD) else "535 authentication failed")
                elif verb == "MAIL":
                    sender = command.split(":", 1)[1].split(" ")[0].strip("<>")
                    reply("553 sender not allowed" if self.refuse_sender else "250 ok")
                elif verb == "RCPT":
                    recipients.append(command.split(":", 1)[1].strip("<> "))
                    reply("250 ok")
                elif verb == "DATA":
                    reply("354 go on")
                    data = b""
                    while (chunk := f.readline()) != b".\r\n":
                        data += chunk[1:] if chunk.startswith(b"..") else chunk
                    self.messages.append(email.message_from_bytes(data, policy=policy.default))
                    self.envelopes.append((sender, recipients))
                    reply("250 queued")
                elif verb == "QUIT":
                    reply("221 bye")
                    return
                else:
                    reply("250 ok")
        except (OSError, ValueError):
            return
        finally:
            conn.close()


@pytest.fixture
def tls_pair(tmp_path):
    return certificate(tmp_path)


@pytest.fixture
def server(tls_pair):
    servers = []

    def start(**kwargs):
        servers.append(FakeServer(tls_pair[0], **kwargs))
        return servers[-1]

    yield start
    for s in servers:
        s.close()


def settings(port, **changes):
    values = {"host": "127.0.0.1", "port": port, "security": "starttls", "user": USER, "password": PASSWORD,
              "sender": USER, "recipients": ["family@example.com", USER]}
    return mail.MailSettings(**{**values, **changes})


def send(config, context, **kwargs):
    mail.send(config, "Stromausfall: Notstrombetrieb", "Das Netz ist weg, das Haus läuft über den Speicher.",
              1_700_000_000, TZ, context=context, **kwargs)


def test_starttls_logs_in_encrypted_and_sends_plain_text(server, tls_pair):
    fake = server()
    send(settings(fake.port), tls_pair[1])
    assert fake.logins == [(USER, PASSWORD, True)]
    assert fake.envelopes == [(USER, ["family@example.com", USER])]
    message = fake.messages[0]
    assert message["Subject"] == "OpenAmpere: Stromausfall: Notstrombetrieb"
    assert message["From"] == USER and message["To"] == f"family@example.com, {USER}"
    assert message["Auto-Submitted"] == "auto-generated"
    assert message["Message-ID"].endswith("@example.com>")  # not the name of this computer
    # the time of the event, not of the send (a queued message is sent later)
    assert email.utils.parsedate_to_datetime(message["Date"]).timestamp() == 1_700_000_000
    body = message.get_body().get_content()
    assert "Das Netz ist weg, das Haus läuft über den Speicher." in body and "Mehr → Benachrichtigungen" in body


def test_tls_from_the_start(server, tls_pair):
    fake = server(tls=True)
    send(settings(fake.port, security="tls"), tls_pair[1])
    assert fake.logins == [(USER, PASSWORD, True)] and len(fake.messages) == 1


def test_without_user_no_login(server, tls_pair):
    fake = server()
    send(settings(fake.port, user="", password=""), tls_pair[1])
    assert fake.logins == [] and len(fake.messages) == 1


def test_wrong_password_is_explained(server, tls_pair):
    fake = server()
    with pytest.raises(mail.MailError, match="Benutzername oder Passwort abgelehnt"):
        send(settings(fake.port, password="wrong"), tls_pair[1])
    assert fake.messages == []


def test_no_password_in_plain_text_when_the_server_offers_no_starttls(server, tls_pair):
    fake = server(starttls=False)
    with pytest.raises(mail.MailError, match="keine Verschlüsselung \\(STARTTLS\\)"):
        send(settings(fake.port), tls_pair[1])
    assert fake.logins == [] and fake.messages == []


def test_an_untrusted_certificate_gets_no_password(server):
    fake = server()
    with pytest.raises(mail.MailError, match="Zertifikat des Mailservers"):
        send(settings(fake.port), ssl.create_default_context())  # does not trust the fake server
    assert fake.logins == []


def test_tls_on_a_starttls_port_is_explained(server, tls_pair):
    fake = server()  # plain greeting, the client expects TLS
    with pytest.raises(mail.MailError, match="Verschlüsselung passt nicht zum Port|Verbindung beendet"):
        send(settings(fake.port, security="tls"), tls_pair[1])


def test_refused_sender_is_explained(server, tls_pair):
    fake = server(refuse_sender=True)
    with pytest.raises(mail.MailError, match="Absenderadresse nicht an"):
        send(settings(fake.port), tls_pair[1])


def test_closed_port_is_explained(tls_pair):
    sock = socket.create_server(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    with pytest.raises(mail.MailError, match="keine Verbindung an"):
        send(settings(port), tls_pair[1])


def test_a_silent_server_is_cut_off_by_the_deadline(server, tls_pair):
    fake = server(silent=True)
    start = time.monotonic()
    with pytest.raises(mail.MailError, match="antwortet nicht"):
        send(settings(fake.port), tls_pair[1], timeout=30, deadline=0.5)
    assert time.monotonic() - start < 5


def test_settings_that_cannot_work_are_explained_before_connecting():
    with pytest.raises(mail.MailError, match="Absenderadresse"):
        mail.check(settings(25, sender=""))
    with pytest.raises(mail.MailError, match="Passwort des Postfachs"):
        mail.check(settings(25, password=""))


def test_sender_and_recipients_default_to_the_user_name():
    cfg = NotifyConfig(mail_host="smtp.example.com", mail_user=USER)
    assert (mail.settings(cfg).sender, mail.settings(cfg).recipients) == (USER, [USER])
    assert mail.settings(NotifyConfig()) is None  # no server: no e-mail


# ---- settings --------------------------------------------------------------------------------------------------

def test_validation_of_the_mail_settings():
    assert validate({"notify.mail_to": [" a@example.com ", "", "b@example.org"]}) == {
        "notify.mail_to": ["a@example.com", "b@example.org"]}
    for bad in ({"notify.mail_host": "https://smtp.example.com"}, {"notify.mail_host": "smtp.example.com:587"},
                {"notify.mail_from": "Name <a@example.com>"}, {"notify.mail_to": ["a@example.com, b@example.com"]},
                {"notify.mail_to": ["a@example.com\r\nBcc: x@example.com"]}, {"notify.mail_security": "none"},
                {"notify.mail_to": [f"r{i}@example.com" for i in range(6)]}):
        with pytest.raises(ValueError):
            validate(bad)


async def test_the_password_is_kept_like_the_other_secrets(tmp_path):
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    try:
        await runtime.update_settings({"notify.mail_host": "smtp.example.com", "notify.mail_user": USER,
                                       "notify.mail_password": PASSWORD})
        view = runtime.settings_view()
        assert view["secrets"]["notify.mail_password"] == {"set": True, "hint": None}  # nothing of a password
        assert PASSWORD not in str(view) and PASSWORD not in str(runtime.storage.get_settings())  # encrypted
        hidden = runtime.settings_view(authenticated=False)["values"]
        assert hidden["notify.mail_user"] is None and hidden["notify.mail_to"] is None
        await runtime.update_settings({"notify.mail_port": 465, "notify.mail_security": "tls"})
        assert runtime.config.notify.mail_password == PASSWORD  # the same server
        # another server: the password is not sent there unless entered again
        await runtime.update_settings({"notify.mail_host": "smtp.attacker.example"})
        assert runtime.config.notify.mail_password == ""
    finally:
        runtime.storage.close()


# ---- the notifier ----------------------------------------------------------------------------------------------

def make_notifier(tmp_path, monkeypatch, context, port, ntfy=None):
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    cfg = runtime.config.notify
    cfg.mail_host, cfg.mail_port, cfg.mail_user, cfg.mail_password = "127.0.0.1", port, USER, PASSWORD
    monkeypatch.setattr(mail, "send", functools.partial(mail.send, context=context, deadline=2))
    posts = []
    if ntfy is not None:
        cfg.ntfy_url = "https://ntfy.example/openampere-test"

        def fake_ntfy(url, token, title, message, tags=""):
            if not ntfy["up"]:
                raise OSError("down")
            posts.append((title, message))

        monkeypatch.setattr(notify_module, "send", fake_ntfy)
    return runtime, Notifier(runtime), posts


async def firmware(notifier, runtime, now):
    """A firmware update of the inverter (reported once per change), then the queued messages sent."""
    runtime.storage.set_meta("firmware_history", [{"ts": 1000.0, "old": "1.00", "new": "1.01"}])
    sent = await notifier.check(now=now)
    if notifier.delivery is not None:
        await notifier.delivery
    return sent


async def test_an_event_goes_by_e_mail_once(tmp_path, monkeypatch, server, tls_pair):
    fake = server()
    runtime, notifier, _ = make_notifier(tmp_path, monkeypatch, tls_pair[1], fake.port)
    now = time.time()
    try:
        assert await firmware(notifier, runtime, now) == ["Neue Firmware am Wechselrichter"]
        assert await firmware(notifier, runtime, now + 60) == []  # once
        assert [m["Subject"] for m in fake.messages] == ["OpenAmpere: Neue Firmware am Wechselrichter"]
        assert notifier.mail_status() == {"error": None, "since": None, "queued": 0, "paused": False}
    finally:
        runtime.storage.close()


async def test_check_does_not_wait_for_the_mail_server(tmp_path, monkeypatch, server, tls_pair):
    fake = server(silent=True)
    runtime, notifier, _ = make_notifier(tmp_path, monkeypatch, tls_pair[1], fake.port)
    try:
        runtime.storage.set_meta("firmware_history", [{"ts": 1000.0, "old": "1.00", "new": "1.01"}])
        start = time.monotonic()
        assert await notifier.check(now=time.time()) == ["Neue Firmware am Wechselrichter"]
        assert time.monotonic() - start < 1  # the grid charging in the same loop must not wait (watchdog)
        await notifier.delivery
        assert "antwortet nicht" in notifier.mail_status()["error"]
    finally:
        runtime.storage.close()


async def test_a_refused_login_is_not_tried_again_with_the_same_settings(tmp_path, monkeypatch, server, tls_pair):
    """Many failed logins can lock the owner's mailbox: after a refused one, only a change or a test e-mail retries."""
    fake = server()
    runtime, notifier, _ = make_notifier(tmp_path, monkeypatch, tls_pair[1], fake.port)
    runtime.config.notify.mail_password = "wrong"
    now = time.time()
    try:
        assert await firmware(notifier, runtime, now) == ["Neue Firmware am Wechselrichter"]
        status = notifier.mail_status()
        assert "Passwort abgelehnt" in status["error"] and status["queued"] == 1 and status["paused"]
        await firmware(notifier, runtime, now + 2 * RETRY_S)
        assert len(fake.logins) == 1  # not tried again
        runtime.config.notify.mail_password = PASSWORD  # changed: tried again at the next retry
        assert not notifier.mail_status()["paused"]
        await firmware(notifier, runtime, now + 3 * RETRY_S)
        assert len(fake.messages) == 1 and notifier.mail_status()["queued"] == 0
    finally:
        runtime.storage.close()


async def test_a_failed_e_mail_is_retried_and_its_error_shown(tmp_path, monkeypatch, server, tls_pair):
    fake = server(silent=True)  # does not answer: tried again
    runtime, notifier, _ = make_notifier(tmp_path, monkeypatch, tls_pair[1], fake.port)
    monkeypatch.setattr(mail, "send", functools.partial(mail.send.func, context=tls_pair[1], deadline=0.5))
    now = time.time()
    try:
        assert await firmware(notifier, runtime, now) == ["Neue Firmware am Wechselrichter"]
        status = notifier.mail_status()
        assert "antwortet nicht" in status["error"] and status["queued"] == 1 and not status["paused"]
        # kept over a restart
        notifier = Notifier(runtime)
        assert notifier.mail_status()["queued"] == 1
        fake = server()
        runtime.config.notify.mail_port = fake.port
        notifier.retry_at["mail"] = now + RETRY_S
        await firmware(notifier, runtime, now + 60)  # not yet: waits for the retry
        assert fake.messages == []
        await firmware(notifier, runtime, now + RETRY_S)
        assert len(fake.messages) == 1 and notifier.mail_status() == {"error": None, "since": None, "queued": 0, "paused": False}
        assert fake.messages[0]["Subject"] == "OpenAmpere: Neue Firmware am Wechselrichter"
    finally:
        runtime.storage.close()


async def test_close_stops_the_delivery(tmp_path, monkeypatch, server, tls_pair):
    fake = server(silent=True)
    runtime, notifier, _ = make_notifier(tmp_path, monkeypatch, tls_pair[1], fake.port)
    try:
        await notifier.push("Titel", "Text")
        await notifier.close()
        assert notifier.delivery.cancelled() and notifier.mail_status()["queued"] == 1  # kept for the next start
    finally:
        runtime.storage.close()


async def test_old_messages_are_dropped(tmp_path, monkeypatch, server, tls_pair):
    fake = server()
    runtime, notifier, _ = make_notifier(tmp_path, monkeypatch, tls_pair[1], fake.port)
    try:
        notifier._queue("mail", time.time() - OUTBOX_MAX_AGE_S - 1, "Alt", "alt", "")
        await notifier.deliver()
        assert fake.messages == [] and notifier.outbox == []
    finally:
        runtime.storage.close()


async def test_ntfy_down_while_e_mail_works(tmp_path, monkeypatch, server, tls_pair):
    """The event counts as sent; ntfy gets it later, with the time it happened."""
    fake = server()
    ntfy = {"up": False}
    runtime, notifier, posts = make_notifier(tmp_path, monkeypatch, tls_pair[1], fake.port, ntfy)
    now = time.time()
    try:
        assert await firmware(notifier, runtime, now) == ["Neue Firmware am Wechselrichter"]
        assert len(fake.messages) == 1 and posts == []
        ntfy["up"] = True
        await firmware(notifier, runtime, now + RETRY_S + 60)
        assert len(fake.messages) == 1  # not again
        assert posts[0][0] == "Neue Firmware am Wechselrichter" and "verspätet zugestellt" in posts[0][1]
        assert notifier.outbox == []
    finally:
        runtime.storage.close()


async def test_ntfy_alone_keeps_checking_the_event_again(tmp_path, monkeypatch):
    """Without e-mail nothing changes: a message ntfy did not take is not counted, the event is checked again."""
    ntfy = {"up": False}
    runtime, notifier, posts = make_notifier(tmp_path, monkeypatch, None, 25, ntfy)
    runtime.config.notify.mail_host = ""
    try:
        assert await firmware(notifier, runtime, time.time()) == []
        assert notifier.outbox == []
        ntfy["up"] = True
        assert await firmware(notifier, runtime, time.time()) == ["Neue Firmware am Wechselrichter"]
        assert "verspätet" not in posts[0][1]
    finally:
        runtime.storage.close()


# ---- API -------------------------------------------------------------------------------------------------------

def test_test_mail_and_status(tmp_path, monkeypatch, server, tls_pair):
    fake = server()
    runtime = Runtime({}, Storage(tmp_path / "t.db"))
    monkeypatch.setattr(mail, "send", functools.partial(mail.send, context=tls_pair[1], deadline=5))
    try:
        with TestClient(create_app(runtime)) as raw:
            client = login(raw)
            response = client.post("/api/notify/mail/test")
            assert response.status_code == 502 and response.json()["detail"] == "Bitte zuerst einen Mailserver eintragen."
            assert client.put("/api/settings", json={
                "notify.mail_host": "127.0.0.1", "notify.mail_port": fake.port, "notify.mail_user": USER,
                "notify.mail_password": "wrong-pass"}).status_code == 200
            response = client.post("/api/notify/mail/test", headers={"X-OpenAmpere-Lang": "en"})
            assert response.status_code == 502 and "refused the user name or password" in response.json()["detail"]
            status = client.get("/api/notify/status", headers={"X-OpenAmpere-Lang": "en"}).json()["mail"]
            assert "refused the user name or password" in status["error"] and status["queued"] == 0
            assert PASSWORD not in client.get("/api/settings").text
            assert client.put("/api/settings", json={"notify.mail_password": PASSWORD}).status_code == 200
            assert client.post("/api/notify/mail/test").json() == {"ok": True}
            assert client.get("/api/notify/status").json()["mail"]["error"] is None
            assert fake.messages[-1]["Subject"] == "OpenAmpere: Test-E-Mail"
    finally:
        runtime.storage.close()

