# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""What every source of car data offers (#401): the cars of an account and their data as it arrives.

A source talks to one manufacturer's service with the owner's login. It is synchronous (it runs in a worker thread)
and keeps its own login session. Errors carry a German message that the app shows as is.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from typing import ClassVar


class SourceError(Exception):
    """Temporary problem (service unreachable, unexpected answer): try again later. .message is the text written for
    the owner, shown in the app."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


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


class Source(ABC):
    key: ClassVar[str]  # stored with the car, e.g. "vw_euda"
    label: ClassVar[str]

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
        """For the app: key, label and the brands with their names."""
