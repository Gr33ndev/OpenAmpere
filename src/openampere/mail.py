# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Notifications by e-mail through the owner's own mail server (SMTP, #594), next to ntfy.

Only smtplib and email from the standard library. The connection is always encrypted, with STARTTLS or with TLS from
the start, and the certificate is checked: the login goes to the server the owner entered and nowhere else, never in
plain text. A server without STARTTLS is refused instead of sending the password unencrypted. The whole send has a
time limit (outbound.Deadline), so a server that answers byte by byte cannot hold it up.
"""

from __future__ import annotations

import contextlib
import re
import smtplib
import socket
import ssl
from dataclasses import dataclass
from datetime import datetime, tzinfo
from typing import NoReturn
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid

from .outbound import Deadline

SECURITIES = ("starttls", "tls")
TIMEOUT_S = 15.0  # for each step of the conversation with the server
DEADLINE_S = 60.0  # for the whole send
# the name OpenAmpere greets the server with (EHLO): smtplib would look up and send the name of this computer, which
# can take seconds and tells the mail server more than it needs
LOCAL_NAME = "localhost.localdomain"
# no full check of RFC 5322, enough to catch typing errors; no spaces, commas or angle brackets, which would let one
# field hold several addresses or a display name
ADDRESS = re.compile(r"[^@\s<>,;\"']+@[^@\s<>,;\"']+\.[^@\s<>,;\"'.]+")
HOST = re.compile(r"[A-Za-z0-9_.-]+|\[[0-9A-Fa-f:.]+\]")
FOOTER = ("Diese E-Mail hat OpenAmpere gesendet. Welche Nachrichten kommen, stellst du in der App unter "
          "Mehr → Benachrichtigungen ein.")


class MailError(Exception):
    """A send that failed, with a German text for the owner (server messages: locales/de.json "mail")."""


@dataclass
class MailSettings:
    host: str
    port: int
    security: str
    user: str
    password: str
    sender: str
    recipients: list[str]


def settings(notify) -> MailSettings | None:
    """The mail settings of config.NotifyConfig, None while no mail server is entered (e-mail switched off)."""
    if not notify.mail_host:
        return None
    sender = notify.mail_from or (notify.mail_user if ADDRESS.fullmatch(notify.mail_user) else "")
    return MailSettings(notify.mail_host, notify.mail_port, notify.mail_security, notify.mail_user,
                        notify.mail_password, sender, [r for r in notify.mail_to if r] or ([sender] if sender else []))


def message(config: MailSettings, title: str, text: str, ts: float, tz: tzinfo) -> EmailMessage:
    """Plain text: readable in every mail program and on every watch."""
    mail = EmailMessage()
    mail["Subject"] = f"OpenAmpere: {title}"
    mail["From"] = config.sender
    mail["To"] = ", ".join(config.recipients)
    mail["Date"] = format_datetime(datetime.fromtimestamp(ts, tz))  # when it happened, also for a late delivery
    # with the domain of the sender: make_msgid would otherwise put in the name of this computer
    mail["Message-ID"] = make_msgid("openampere", domain=config.sender.rpartition("@")[2])
    mail["Auto-Submitted"] = "auto-generated"  # RFC 3834: no automatic answers such as out-of-office replies
    mail.set_content(f"{text}\n\n-- \n{FOOTER}\n", cte="quoted-printable")
    return mail


class _SMTP(smtplib.SMTP):
    def __init__(self, deadline: Deadline, host: str, **kwargs) -> None:
        self._deadline = deadline
        super().__init__(**kwargs)
        self._host = host  # the name the certificate is checked against (also for STARTTLS)

    def _get_socket(self, host, port, timeout):  # noqa: ANN001 - smtplib's signature
        return self._deadline.connect((host, port), timeout, self.source_address)


class _SMTPSSL(smtplib.SMTP_SSL, _SMTP):
    """TLS from the start; SMTP_SSL wraps the socket of _SMTP._get_socket."""

    def __init__(self, deadline: Deadline, host: str, **kwargs) -> None:
        self._deadline = deadline
        smtplib.SMTP_SSL.__init__(self, **kwargs)
        self._host = host


def check(config: MailSettings) -> None:
    """Raises MailError for settings that cannot work, before a connection is made."""
    if not config.host or not HOST.fullmatch(config.host):
        raise MailError("Bitte den Namen des Mailservers eintragen, zum Beispiel smtp.example.de, ohne https:// und "
                        "ohne Port.")
    if not config.sender:
        raise MailError("Bitte eine Absenderadresse eintragen, meist die E-Mail-Adresse des Postfachs.")
    if not config.recipients:
        raise MailError("Bitte mindestens eine Empfängeradresse eintragen.")
    if config.user and not config.password:
        raise MailError("Bitte das Passwort des Postfachs eintragen.")


def send(config: MailSettings, title: str, text: str, ts: float, tz: tzinfo, *, timeout: float = TIMEOUT_S,
         deadline: float = DEADLINE_S, context: ssl.SSLContext | None = None) -> None:
    """Sends one message; raises MailError with a text for the owner. `context`: for tests with an own certificate."""
    check(config)
    mail = message(config, title, text, ts, tz)
    context = context or ssl.create_default_context()
    cut = Deadline(deadline)
    try:
        if config.security == "tls":
            smtp: _SMTP = _SMTPSSL(cut, config.host, local_hostname=LOCAL_NAME, timeout=timeout, context=context)
        else:
            smtp = _SMTP(cut, config.host, local_hostname=LOCAL_NAME, timeout=timeout)
        try:
            smtp.connect(config.host, config.port)
            if config.security != "tls":
                smtp.starttls(context=context)  # raises when the server offers no STARTTLS: never in plain text
            if config.user:
                smtp.login(config.user, config.password)
            smtp.send_message(mail)
        finally:
            with contextlib.suppress(Exception):
                smtp.quit()
            smtp.close()
    except Exception as err:  # noqa: BLE001 - every error becomes a text for the owner
        fail(err, cut.expired)
    finally:
        cut.close()


def fail(err: BaseException, expired: bool = False) -> NoReturn:
    """Raises MailError with a German text for the owner: what went wrong and what to check. Without addresses or the
    server's answer, which may hold e-mail addresses (the text is shown in the app and written to the log)."""
    if isinstance(err, MailError):
        raise err
    if isinstance(err, smtplib.SMTPAuthenticationError):
        raise MailError("Der Mailserver hat Benutzername oder Passwort abgelehnt. Bitte beide prüfen. Manche Anbieter "
                        "verlangen für Programme ein eigenes App-Passwort oder dass SMTP in den Einstellungen des "
                        "Postfachs eingeschaltet ist.")
    if isinstance(err, smtplib.SMTPNotSupportedError):
        if "STARTTLS" in str(err).upper():
            raise MailError("Der Mailserver bietet an diesem Port keine Verschlüsselung (STARTTLS) an. Ohne sie sendet "
                            "OpenAmpere das Passwort nicht. Bitte Port 465 mit „TLS“ oder Port 587 mit „STARTTLS“ "
                            "versuchen.")
        raise MailError("Der Mailserver erlaubt an diesem Port keine Anmeldung. Bitte Port und Verschlüsselung prüfen: "
                        "üblich sind Port 587 mit „STARTTLS“ oder Port 465 mit „TLS“.")
    if isinstance(err, smtplib.SMTPSenderRefused):
        raise MailError("Der Mailserver nimmt die Absenderadresse nicht an. Meist muss sie die E-Mail-Adresse des "
                        "Postfachs sein, mit dem du dich anmeldest.")
    if isinstance(err, smtplib.SMTPRecipientsRefused):
        raise MailError("Der Mailserver hat die Empfängeradressen abgelehnt. Bitte die Adressen prüfen.")
    if expired or isinstance(err, TimeoutError):
        raise MailError("Der Mailserver antwortet nicht. Bitte Name und Port prüfen und ob die Verschlüsselung zum Port "
                        "passt (587: „STARTTLS“, 465: „TLS“). Manche Router oder Internetanbieter sperren ausgehende "
                        "E-Mails.")
    if isinstance(err, ssl.SSLCertVerificationError):
        raise MailError("Das Zertifikat des Mailservers ist ungültig oder passt nicht zu seinem Namen. Bitte den Namen "
                        "so eintragen, wie ihn dein Anbieter angibt.")
    if isinstance(err, ssl.SSLError):
        raise MailError("Die Verschlüsselung passt nicht zum Port. Üblich sind Port 587 mit „STARTTLS“ oder Port 465 "
                        "mit „TLS“.")
    if isinstance(err, smtplib.SMTPServerDisconnected):
        raise MailError("Der Mailserver hat die Verbindung beendet. Oft passt die Verschlüsselung nicht zum Port (587: "
                        "„STARTTLS“, 465: „TLS“).")
    if isinstance(err, smtplib.SMTPResponseException):
        raise MailError(f"Der Mailserver hat die E-Mail abgelehnt (Fehler {err.smtp_code}).")
    if isinstance(err, socket.gaierror):
        raise MailError("Den Mailserver gibt es unter diesem Namen nicht. Bitte den Namen prüfen, zum Beispiel "
                        "smtp.example.de.")
    if isinstance(err, ConnectionRefusedError):
        raise MailError("Der Mailserver nimmt an diesem Port keine Verbindung an. Üblich sind Port 587 mit „STARTTLS“ "
                        "oder Port 465 mit „TLS“.")
    if isinstance(err, OSError):
        raise MailError("OpenAmpere erreicht den Mailserver nicht. Bitte Name, Port und die Internetverbindung prüfen.")
    raise MailError("Die E-Mail ließ sich nicht senden.")
