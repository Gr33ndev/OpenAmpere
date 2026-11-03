#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Generate the license list of what only the Android app (#567) ships, from a built APK.

The APK has the same Python packages as the server (from requirements.lock), except Chaquopy's Android builds that
android/app/build.gradle.kts pins (cryptography, cffi, pyyaml and the libraries they bring along), plus native parts
only Android needs: Python itself, Chaquopy, OpenSSL, SQLite, the Kotlin standard library (android/licenses/
components.json with their texts). Chaquopy's builds, with the texts from the APK, and the native parts go into
  - web/public/third-party-licenses-android.json (the app shows it on its licenses page on the tablet) and
  - android/THIRD_PARTY_LICENSES.md.

Usage (after building the APK, see android/README.md):
    python3 scripts/android_licenses.py android/app/build/outputs/apk/debug/app-debug.apk          # write the files
    python3 scripts/android_licenses.py android/app/build/outputs/apk/debug/app-debug.apk --check  # fail if outdated
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import zipfile
from email.parser import HeaderParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ANDROID_JSON = ROOT / "web" / "public" / "third-party-licenses-android.json"
ANDROID_MD = ROOT / "android" / "THIRD_PARTY_LICENSES.md"
COMPONENTS = ROOT / "android" / "licenses"
GRADLE = ROOT / "android" / "app" / "build.gradle.kts"
LICENSE_NAMES = ("LICENSE", "LICENCE", "COPYING", "NOTICE", "AUTHORS")
TITLE = "Nur in der Android-App"


def apk_packages(apk: Path) -> list[dict]:
    """The Python packages Chaquopy put into the APK (assets/chaquopy/requirements-*.imy, zip files)."""
    found: dict[str, dict] = {}
    with zipfile.ZipFile(apk) as outer:
        for name in sorted(n for n in outer.namelist() if n.startswith("assets/chaquopy/requirements-")):
            with zipfile.ZipFile(io.BytesIO(outer.read(name))) as inner:
                files = inner.namelist()
                for meta_name in (n for n in files if n.endswith(".dist-info/METADATA")):
                    folder = meta_name.rsplit("/", 1)[0]
                    meta = HeaderParser().parsestr(inner.read(meta_name).decode("utf-8", "replace"))
                    key = meta["Name"].lower().replace("_", "-")
                    if key in found:
                        continue
                    license_name = meta.get("License-Expression") or ""
                    if not license_name:
                        classifiers = [c.split("::")[-1].strip() for c in meta.get_all("Classifier") or []
                                       if c.startswith("License")]
                        declared = (meta.get("License") or "").splitlines()
                        license_name = ", ".join(classifiers) or (declared[0][:60] if declared else "")
                    texts = [inner.read(f).decode("utf-8", "replace").strip() for f in sorted(files)
                             if f.startswith(folder + "/") and not f.endswith("/")
                             and any(part in f.rsplit("/", 1)[-1].upper() for part in LICENSE_NAMES)]
                    url = meta.get("Home-page") or ""
                    for entry in meta.get_all("Project-URL") or []:
                        label, _, link = entry.partition(",")
                        if not url and label.strip().lower() in ("homepage", "source", "repository", "source code"):
                            url = link.strip()
                    found[key] = {"name": meta["Name"], "version": meta["Version"], "license": license_name,
                                  "url": url, "texts": list(dict.fromkeys(texts))}
    return sorted(found.values(), key=lambda p: p["name"].lower())


def components() -> list[dict]:
    entries = json.loads((COMPONENTS / "components.json").read_text(encoding="utf-8"))
    return [{"name": e["name"], "version": e["version"], "license": e["license"], "url": e["url"],
             "texts": [(COMPONENTS / e["file"]).read_text(encoding="utf-8").strip()]} for e in entries]


def chaquopy_builds() -> set[str]:
    """The packages build.gradle.kts takes from Chaquopy's index instead of requirements.lock: "name" to ("version" ...
    Fixed there, so the weekly update of the lock (which changes the server list) never changes this list."""
    return {name for name in re.findall(r'^\s*"([a-z0-9-]+)" to \("[^"]+"', GRADLE.read_text(encoding="utf-8"),
                                        re.MULTILINE)}


def only_android(apk: Path) -> list[dict]:
    builds = chaquopy_builds()
    return components() + [p for p in apk_packages(apk) if p["name"].lower().replace("_", "-") in builds]


def render_md(packages: list[dict]) -> str:
    out = ["# Third-party licenses of the Android app", "",
           "What the Android app ships in addition to ../THIRD_PARTY_LICENSES.md, or in another version.", "",
           "This file is generated by `scripts/android_licenses.py`. Do not edit by hand.", "",
           "| Component | Version | License |", "|---|---|---|"]
    out += [f"| {p['name']} | {p['version']} | {p['license'] or 'see text'} |" for p in packages]
    out += ["", "---", ""]
    for p in packages:
        out += [f"## {p['name']} {p['version']}", "", f"License: {p['license'] or 'see text'}"]
        out += ([f"Source: {p['url']}"] if p["url"] else []) + [""]
        for text in p["texts"]:
            out += ["```", text, "```", ""]
    return "\n".join(out).rstrip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("apk", type=Path)
    parser.add_argument("--check", action="store_true", help="exit 1 if the generated files are outdated")
    args = parser.parse_args()
    packages = only_android(args.apk)
    outputs = {ANDROID_JSON: json.dumps({"groups": [{"id": "android", "title": TITLE, "packages": packages}]},
                                        ensure_ascii=False,
                                        indent=1) + "\n",
               ANDROID_MD: render_md(packages)}
    if args.check:
        stale = [p for p, c in outputs.items() if not p.exists() or p.read_text(encoding="utf-8") != c]
        if stale:
            sys.exit(f"out of date: {', '.join(str(p.relative_to(ROOT)) for p in stale)} - run scripts/android_licenses.py")
        print("Android licenses up to date")
        return
    for path, content in outputs.items():
        path.write_text(content, encoding="utf-8")
        print(f"wrote {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
