#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
"""Smoke test of the Android app (#567) in an emulator, in CI and on a computer.

Installs the APK, starts the app, and checks what an owner relies on: the server starts in the app, reads an inverter
in the home network (the simulator on this computer, which the emulator reaches as 10.0.2.2), and comes back after
Android killed its process. Needs the Android SDK (ANDROID_HOME) with an emulator image, and openampere installed
here for the simulator.

    python3 scripts/android_smoke_test.py android/app/build/outputs/apk/debug/app-debug.apk \\
        --image "system-images;android-30;google_apis;x86_64"
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

PACKAGE = "io.github.gr33ndev.openampere"
LOCAL = "http://127.0.0.1:18080"  # adb forward to the app's port 8080
SIMULATOR_PORT = 5020
SDK = Path(os.environ.get("ANDROID_HOME", ""))


def adb(*args: str, check: bool = True) -> str:
    return subprocess.run([str(SDK / "platform-tools" / "adb"), *args], capture_output=True, text=True,
                          check=check).stdout


def wait(what: str, condition, timeout: float, every: float = 2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            result = condition()
        except Exception:  # noqa: BLE001 - not there yet
            result = None
        if result:
            print(f"ok: {what}", flush=True)
            return result
        time.sleep(every)
    sys.exit(f"FAILED: {what} (after {timeout:.0f} s)")


def server_pid() -> str | None:
    for line in adb("shell", "ps", "-A").splitlines():
        parts = line.split()
        if parts and parts[-1] == f"{PACKAGE}:server":
            return parts[1]
    return None


def start_emulator(image: str, avd: str) -> subprocess.Popen:
    tools = SDK / "cmdline-tools" / "latest" / "bin"
    subprocess.run([str(tools / "sdkmanager"), "--install", image, "emulator", "platform-tools"], check=True,
                   input="y\n" * 10, text=True, stdout=subprocess.DEVNULL)
    subprocess.run([str(tools / "avdmanager"), "create", "avd", "--force", "-n", avd, "-k", image, "-d", "pixel_c"],
                   check=True, input="no\n", text=True, stdout=subprocess.DEVNULL)
    emulator = subprocess.Popen([str(SDK / "emulator" / "emulator"), "-avd", avd, "-no-window", "-no-audio",
                                 "-no-boot-anim", "-no-snapshot", "-gpu", "swiftshader_indirect"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    adb("wait-for-device")
    wait("emulator booted", lambda: adb("shell", "getprop", "sys.boot_completed").strip() == "1", 600)
    # root (google_apis images allow it): to kill the server's process as Android would; adb restarts for it
    adb("root", check=False)
    adb("wait-for-device")
    wait("adb as root", lambda: adb("shell", "id", "-u").strip() == "0", 60)
    return emulator


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("apk", type=Path)
    parser.add_argument("--image", default="system-images;android-30;google_apis;x86_64")
    parser.add_argument("--avd", default="openampere-smoke")
    args = parser.parse_args()

    simulator = subprocess.Popen([sys.executable, "-m", "openampere.simulator", "--port", str(SIMULATOR_PORT),
                                  "--speed", "20"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    emulator = start_emulator(args.image, args.avd)
    try:
        adb("install", "-r", str(args.apk))
        adb("shell", "am", "start", "-n", f"{PACKAGE}/.MainActivity")
        adb("forward", "tcp:18080", "tcp:8080")
        wait("server answers in the app", lambda: urllib.request.urlopen(f"{LOCAL}/api/health", timeout=3).status == 200,
             180)
        status = json.load(urllib.request.urlopen(f"{LOCAL}/api/status", timeout=5))
        if status.get("android") is not True:
            sys.exit(f"FAILED: the server does not run in Android mode: {status.get('android')}")
        print(f"ok: version {status['version']} in Android mode", flush=True)

        # set up like an owner: a password (made here, used only in this emulator), then the inverter
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        headers = {"content-type": "application/json", "x-openampere": "1", "origin": LOCAL}

        def send(method: str, path: str, body: dict) -> None:
            request = urllib.request.Request(f"{LOCAL}{path}", data=json.dumps(body).encode(), method=method,
                                             headers=headers)
            opener.open(request, timeout=10).read()

        send("POST", "/api/auth/setup", {"password": secrets.token_urlsafe(18)})
        send("PUT", "/api/settings", {"inverter.host": "10.0.2.2", "inverter.port": SIMULATOR_PORT})
        wait("the inverter (simulator) is read",
             lambda: json.load(urllib.request.urlopen(f"{LOCAL}/api/status", timeout=5))["connected"], 120)
        wait("live values arrive",
             lambda: json.load(urllib.request.urlopen(f"{LOCAL}/api/live", timeout=5)).get("pv_power") is not None,
             60)

        # Android kills the process (low memory, a crash): the server must come back by itself
        before = wait("server process found", server_pid, 30)
        adb("shell", "kill", "-9", before)
        wait("server comes back after its process was killed",
             lambda: server_pid() not in (None, before)
             and urllib.request.urlopen(f"{LOCAL}/api/health", timeout=3).status == 200, 120)
        wait("and reads the inverter again",
             lambda: json.load(urllib.request.urlopen(f"{LOCAL}/api/status", timeout=5))["connected"], 120)
        print("smoke test passed", flush=True)
    except SystemExit:
        print(adb("logcat", "-d", "-s", "OpenAmpere:*", "python.stderr:*", check=False)[-6000:], flush=True)
        raise
    finally:
        adb("emu", "kill", check=False)
        emulator.wait(timeout=60)
        simulator.terminate()


if __name__ == "__main__":
    main()
