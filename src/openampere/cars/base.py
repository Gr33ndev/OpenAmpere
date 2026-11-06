# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""What every source of car data offers (#401): the cars of an account and their data as it arrives.

A source talks to one manufacturer's service with the owner's login. It is synchronous (it runs in a worker thread)
and keeps its own login session. Errors carry a German message that the app shows as is.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from typing import ClassVar


class SourceError(Exception):
    """Temporary problem (service unreachable, unexpected answer): try again later. .message is the text written for
    the owner, shown in the app."""

    def __init__(self, message: str, wait_s: float | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.wait_s = wait_s  # the source says when to look again (e.g. a daily limit), else the usual back-off


class SourceAuthError(SourceError):
    """The login was refused: wrong credentials or a login step OpenAmpere cannot do. Retrying does not help."""


class SourceSetupError(SourceError):
    """The owner still has to do something at the manufacturer (consent, data request): asked again less often."""


@dataclass
class CarData:
    """What one delivery of the source says about the car; None: not in this delivery."""
    ts: float  # when the source delivered the values
    soc: float | None = None  # state of charge of the drive battery, %
    range_km: float | None = None
    odometer_km: float | None = None
    plugged: bool | None = None
    charging: bool | None = None
    target_soc: float | None = None  # the charge limit set in the car

    def empty(self) -> bool:
        return all(v is None for k, v in asdict(self).items() if k != "ts")


@dataclass
class Delivery:
    """The answer of one look at the source: new data, oldest first, and when the newest delivery was made (also one
    without data, e.g. while the car sleeps), to plan the next look."""
    data: list[CarData]
    newest: float | None = None


EMAIL = re.compile(r"[^@\s]{1,128}@[^@\s]{1,125}")


class Source(ABC):
    key: ClassVar[str]  # stored with the car, e.g. "vw_euda"
    label: ClassVar[str]
    # "password": e-mail and password of the brand account. "device": a client ID, and the owner confirms the login
    # in a browser (start_login, finish_login); the source's login token is kept in the place of the password, and the
    # source may renew it (it sets self.password, the cars store the new one)
    login_kind: ClassVar[str] = "password"
    every_s: ClassVar[float | None] = None  # look at this pace instead of after each expected delivery

    def __init__(self, brand: str, username: str, password: str) -> None:
        self.brand = brand
        self.username = username
        self.password = password

    @abstractmethod
    def vehicles(self) -> list[dict]:
        """The cars of the account: {"vin": …, "name": …}."""

    @abstractmethod
    def fetch(self, vin: str, after: float) -> Delivery:
        """Data delivered after `after` (0: the latest few deliveries)."""

    @classmethod
    @abstractmethod
    def info(cls) -> dict:
        """For the app: key, label, login and the brands with their names."""

    @classmethod
    def valid_username(cls, username: str) -> bool:
        return bool(EMAIL.fullmatch(username))

    @classmethod
    def start_login(cls, brand: str, username: str) -> dict:
        """Only login "device": the address and code the owner confirms in a browser, plus what finish_login needs
        (kept by OpenAmpere, never sent to the app); "expires_at" and "interval" in seconds."""
        raise NotImplementedError

    @classmethod
    def finish_login(cls, brand: str, username: str, pending: dict) -> str | None:
        """Only login "device": the login to keep once the owner confirmed it, None while the source still waits."""
        raise NotImplementedError
