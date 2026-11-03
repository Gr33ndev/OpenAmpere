# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""The license list of what only the Android app ships (#567), read from the APK."""
import importlib.util
import io
import json
import zipfile

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("android_licenses", ROOT / "scripts" / "android_licenses.py")
android_licenses = importlib.util.module_from_spec(spec)
spec.loader.exec_module(android_licenses)


def imy(packages: dict[str, tuple[str, str]]) -> bytes:
    """A requirements-*.imy as Chaquopy writes it: a zip with the installed packages and their dist-info."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        for name, (version, license_text) in packages.items():
            folder = f"{name.replace('-', '_')}-{version}.dist-info"
            z.writestr(f"{folder}/METADATA", f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
                                             "License-Expression: MIT\n")
            z.writestr(f"{folder}/licenses/LICENSE", license_text)
    return buffer.getvalue()


def test_only_what_the_server_list_does_not_have(tmp_path):
    server = json.loads(android_licenses.SERVER_JSON.read_text(encoding="utf-8"))
    known = server["groups"][-1]["packages"][0]  # a package of the server in the same version
    apk = tmp_path / "app.apk"
    with zipfile.ZipFile(apk, "w") as z:
        z.writestr("assets/chaquopy/requirements-common.imy",
                   imy({known["name"]: (known["version"], "same"), "only-android": ("1.0", "MIT text")}))
        z.writestr("assets/chaquopy/requirements-arm64-v8a.imy", imy({"only-android": ("1.0", "MIT text")}))
    packages = android_licenses.only_android(apk)
    names = [p["name"] for p in packages]
    assert "Python" in names and "Chaquopy" in names  # the native parts from android/licenses/
    assert names.count("only-android") == 1 and known["name"] not in names
    extra = next(p for p in packages if p["name"] == "only-android")
    assert extra["version"] == "1.0" and extra["license"] == "MIT" and extra["texts"] == ["MIT text"]


def test_every_native_part_has_its_text():
    for entry in json.loads((android_licenses.COMPONENTS / "components.json").read_text(encoding="utf-8")):
        assert (android_licenses.COMPONENTS / entry["file"]).read_text(encoding="utf-8").strip(), entry["name"]
