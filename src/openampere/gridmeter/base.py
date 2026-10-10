# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""What every grid-operator provider offers: its meters and their daily energy.

A provider talks to one grid operator's customer portal. It is synchronous (it runs in a worker thread) and keeps its
own login session. Errors carry a German message that the app shows as is.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import ClassVar


class ProviderError(Exception):
    """Temporary problem (portal unreachable, unexpected answer): try again later."""


class ProviderAuthError(ProviderError):
    """The login was refused: wrong credentials or a login step OpenAmpere cannot do. Retrying does not help."""


@dataclass
class Meter:
    id: str  # stable id within the provider
    name: str  # shown in the app
    kinds: list[str] = field(default_factory=list)  # "import" (grid power) and/or "export" (feed-in)

    def to_dict(self) -> dict:
        return asdict(self)


class Provider(ABC):
    key: ClassVar[str]  # stored in the settings, e.g. "netze_bw"
    label: ClassVar[str]  # name of the grid operator
    portal: ClassVar[str]  # the customer portal whose login is used
    region: ClassVar[str] = ""  # where the operator runs the grid, helps users pick the right one

    def __init__(self, username: str, password: str) -> None:
        self.username = username
        self.password = password

    @abstractmethod
    def meters(self) -> list[Meter]:
        """The active meters of the account."""

    @abstractmethod
    def daily(self, meter: Meter, kind: str, first: date, last: date) -> dict[str, float]:
        """kWh per local day (ISO date) for first <= day < last."""

    @classmethod
    def info(cls) -> dict:
        return {"key": cls.key, "label": cls.label, "portal": cls.portal, "region": cls.region}
