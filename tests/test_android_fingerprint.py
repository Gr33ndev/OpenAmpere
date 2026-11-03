# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""The certificate fingerprint of the Android app's release key is published where owners check it (#567): every place
shows the one in android/signing-certificate.sha256, which the release workflow compares the signed APK with."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FINGERPRINT = (ROOT / "android" / "signing-certificate.sha256").read_text(encoding="utf-8").strip()
PUBLISHED = ["README.md", "README.de.md", "GOVERNANCE.md", "docs/android.md", "docs/android.de.md",
             "site/pages/index.html"]


def test_fingerprint_file_is_a_sha256():
    assert len(FINGERPRINT) == 64 and all(c in "0123456789abcdef" for c in FINGERPRINT)


@pytest.mark.parametrize("name", PUBLISHED)
def test_published_fingerprint_matches_the_release_key(name):
    assert FINGERPRINT in (ROOT / name).read_text(encoding="utf-8"), f"{name} must show {FINGERPRINT}"
