# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Running in the Android app on a tablet (#567).

The app starts the unchanged server inside its own process with OPENAMPERE_ANDROID set. There are no helper
containers (updates, Tailscale), no Docker and no install.sh, and the tablet is not a server: it runs on its own
battery, is switched off, put to sleep or taken out of the house. Docker installations and the Home Assistant app
never have the variable, so nothing here changes them."""
from __future__ import annotations

import os

ENV = "OPENAMPERE_ANDROID"


def active() -> bool:
    return os.environ.get(ENV, "") not in ("", "0")
