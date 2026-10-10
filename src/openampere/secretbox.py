# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Secrets in the database (passwords, tokens, API keys) are encrypted at rest.

AES-256-GCM with a random key in its own file next to the database (secret.key, readable only by the app's user).
A copy of the database alone, e.g. a file that was shared for help, reveals no secrets; backups leave them out
anyway. The key is not derived from the access password on purpose: OpenAmpere has to read the secrets after every
restart and nightly update without anyone logging in (grid-operator portal, evcc, ntfy).

Values are stored as "enc:v1:<base64 of nonce + ciphertext>", the setting's name is bound to the ciphertext.
Plain values from older versions are still read and encrypted on the next start.
"""

from __future__ import annotations

import base64
import logging
import os
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

log = logging.getLogger(__name__)

PREFIX = "enc:v1:"
KEY_BYTES = 32


class SecretBox:
    def __init__(self, key_path: str | Path) -> None:
        self.key_path = Path(key_path)
        self._aes: AESGCM | None = None

    def _cipher(self) -> AESGCM:
        if self._aes is None:
            self._aes = AESGCM(self._load_or_create_key())
        return self._aes

    def _load_or_create_key(self) -> bytes:
        try:
            key = self.key_path.read_bytes()
        except FileNotFoundError:
            self.key_path.parent.mkdir(parents=True, exist_ok=True)
            key = os.urandom(KEY_BYTES)
            try:  # O_EXCL: two starts at the same moment must not create two different keys
                fd = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                return self._load_or_create_key()
            with os.fdopen(fd, "wb") as f:
                f.write(key)
            log.info("created a new key for the stored secrets: %s", self.key_path)
            return key
        if len(key) != KEY_BYTES:
            raise ValueError(f"{self.key_path} ist beschädigt. Datei löschen und die Zugangsdaten in der App neu eingeben.")
        if self.key_path.stat().st_mode & 0o077:
            os.chmod(self.key_path, 0o600)
        return key

    @staticmethod
    def is_encrypted(value) -> bool:
        return isinstance(value, str) and value.startswith(PREFIX)

    def encrypt(self, name: str, value: str) -> str:
        if not value or self.is_encrypted(value):
            return value
        nonce = os.urandom(12)
        sealed = self._cipher().encrypt(nonce, value.encode("utf-8"), name.encode("utf-8"))
        return PREFIX + base64.b64encode(nonce + sealed).decode("ascii")

    def decrypt(self, name: str, value):
        if not self.is_encrypted(value):
            return value  # empty or stored by an older version
        try:
            raw = base64.b64decode(value[len(PREFIX):])
            return self._cipher().decrypt(raw[:12], raw[12:], name.encode("utf-8")).decode("utf-8")
        except (InvalidTag, ValueError):
            # another key (database copied without secret.key): the secret has to be entered again
            log.warning("cannot decrypt the stored secret %s, please enter it again in the app", name)
            return ""
