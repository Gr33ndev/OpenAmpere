# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Google Play Protect blocks every app from outside the Play Store that may install packages as harmful (#608, #610):
the Android app must never ask for these permissions again."""
from pathlib import Path
import xml.etree.ElementTree as ET

MANIFEST = Path(__file__).resolve().parents[1] / "android/app/src/main/AndroidManifest.xml"
ANDROID = "{http://schemas.android.com/apk/res/android}"
BLOCKED = {"android.permission.REQUEST_INSTALL_PACKAGES", "android.permission.UPDATE_PACKAGES_WITHOUT_USER_ACTION",
           "android.permission.INSTALL_PACKAGES", "android.permission.REQUEST_DELETE_PACKAGES"}


def test_the_app_cannot_install_packages():
    asked = {p.get(f"{ANDROID}name") for p in ET.parse(MANIFEST).getroot().iter("uses-permission")}
    assert asked, "no permissions found: is the manifest still where this test looks?"
    assert not asked & BLOCKED
