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
import tempfile
import time
import urllib.request
from pathlib import Path

PACKAGE = "io.github.gr33ndev.openampere"
LOCAL = "http://127.0.0.1:18080"  # adb forward to the app's port 8080
SIMULATOR_PORT = 5020
SDK = Path(os.environ.get("ANDROID_HOME", ""))
EMULATOR_LOG = Path(tempfile.gettempdir()) / "openampere-smoke-emulator.log"
SIMULATOR_LOG = Path(tempfile.gettempdir()) / "openampere-smoke-simulator.log"
# the whole test gives up after this, well before the CI job's timeout (15 minutes, with setup and cleanup), so the
# logs are still printed
BUDGET_END = time.monotonic() + 9 * 60
# every wait gives up at once when one of them stopped (no KVM, a crash, the simulator's port taken)
emulator: subprocess.Popen | None = None
simulator: subprocess.Popen | None = None


def adb(*args: str, check: bool = True, timeout: float = 120) -> str:
    """check=False also for a timeout: in cleanup and log printing a hanging adb must not hide the real error."""
    try:
        return subprocess.run([str(SDK / "platform-tools" / "adb"), *args], capture_output=True, text=True,
                              check=check, timeout=timeout).stdout
    except subprocess.TimeoutExpired:
        if check:
            raise
        return ""


def wait(what: str, condition, timeout: float, every: float = 2.0):
    end = min(time.monotonic() + timeout, BUDGET_END)
    while time.monotonic() < end:
        if emulator and emulator.poll() is not None:
            sys.exit(f"FAILED: {what}: the emulator stopped (exit code {emulator.returncode})")
        if simulator and simulator.poll() is not None:
            sys.exit(f"FAILED: {what}: the simulator stopped (exit code {simulator.returncode})")
        try:
            result = condition()
        except Exception:  # noqa: BLE001 - not there yet
            result = None
        if result:
            print(f"ok: {what}", flush=True)
            return result
        time.sleep(every)
    sys.exit(f"FAILED: {what} (after {timeout:.0f} s or the time for the whole test)")


def server_pid() -> str | None:
    for line in adb("shell", "ps", "-A").splitlines():
        parts = line.split()
        if parts and parts[-1] == f"{PACKAGE}:server":
            return parts[1]
    return None


def start_emulator(image: str, avd: str) -> None:
    global emulator
    tools = SDK / "cmdline-tools" / "latest" / "bin"
    subprocess.run([str(tools / "sdkmanager"), "--install", image, "emulator", "platform-tools"], check=True,
                   input="y\n" * 10, text=True, stdout=subprocess.DEVNULL, timeout=300)
    print("ok: emulator image installed", flush=True)
    subprocess.run([str(tools / "avdmanager"), "create", "avd", "--force", "-n", avd, "-k", image, "-d", "pixel_c"],
                   check=True, input="no\n", text=True, stdout=subprocess.DEVNULL, timeout=120)
    # avdmanager and the emulator look for AVDs in different folders on some systems (on the CI runner): tell the
    # emulator where avdmanager put it
    if "ANDROID_AVD_HOME" not in os.environ:
        homes = [Path(os.environ.get("ANDROID_USER_HOME", Path.home() / ".android")) / "avd",
                 Path.home() / ".android" / "avd", Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
                 / ".android" / "avd"]
        found = next((home for home in homes if (home / f"{avd}.ini").exists()), None)
        if not found:
            sys.exit(f"FAILED: avdmanager created no {avd}.ini in {', '.join(map(str, homes))}")
        os.environ["ANDROID_AVD_HOME"] = str(found)
    # without hardware acceleration the emulator takes hours to boot: stop here instead
    accel = subprocess.run([str(SDK / "emulator" / "emulator"), "-accel-check"], capture_output=True, text=True,
                           timeout=60)
    if accel.returncode != 0:
        sys.exit(f"FAILED: no hardware acceleration for the emulator:\n{accel.stdout}{accel.stderr}")
    emulator = subprocess.Popen([str(SDK / "emulator" / "emulator"), "-avd", avd, "-no-window", "-no-audio",
                                 "-no-boot-anim", "-no-snapshot", "-gpu", "swiftshader_indirect"],
                                stdout=EMULATOR_LOG.open("w"), stderr=subprocess.STDOUT)
    wait("emulator booted", lambda: adb("shell", "getprop", "sys.boot_completed", timeout=10).strip() == "1", 300)
    # root (google_apis images allow it): to kill the server's process as Android would; adb restarts for it
    adb("root", check=False)
    wait("adb as root", lambda: adb("shell", "id", "-u", timeout=10).strip() == "0", 60)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("apk", type=Path)
    parser.add_argument("--image", default="system-images;android-30;google_apis;x86_64")
    parser.add_argument("--avd", default="openampere-smoke")
    args = parser.parse_args()

    global simulator
    EMULATOR_LOG.unlink(missing_ok=True)  # only this run's
    simulator = subprocess.Popen([sys.executable, "-m", "openampere.simulator", "--port", str(SIMULATOR_PORT),
                                  "--speed", "20"], stdout=SIMULATOR_LOG.open("w"), stderr=subprocess.STDOUT)
    try:
        time.sleep(2)  # an import error or a taken port shows before minutes go into booting the emulator
        if simulator.poll() is not None:
            sys.exit(f"FAILED: the simulator did not start (exit code {simulator.returncode})")
        start_emulator(args.image, args.avd)
        adb("install", "-r", str(args.apk))
        # as if the owner said yes to the app's first question (leave it out of battery optimisation); otherwise
        # that system dialog is in front instead of the app
        adb("shell", "dumpsys", "deviceidle", "whitelist", f"+{PACKAGE}")
        adb("shell", "am", "start", "-n", f"{PACKAGE}/.MainActivity")
        adb("forward", "tcp:18080", "tcp:8080")
        wait("server answers in the app", lambda: urllib.request.urlopen(f"{LOCAL}/api/health", timeout=3).status == 200,
             180)
        status = json.load(urllib.request.urlopen(f"{LOCAL}/api/status", timeout=5))
        if status.get("android") is not True:
            sys.exit(f"FAILED: the server does not run in Android mode: {status.get('android')}")
        print(f"ok: version {status['version']} in Android mode", flush=True)
        page = urllib.request.urlopen(f"{LOCAL}/", timeout=5).read().decode()
        if '<div id="root">' not in page:
            sys.exit("FAILED: the server does not deliver the web app")
        wait("the app shows the web app in front",
             lambda: any(f"{PACKAGE}/.MainActivity" in line and "ResumedActivity" in line
                         for line in adb("shell", "dumpsys", "activity", "activities").splitlines()), 30)

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
    except BaseException:
        for log in (SIMULATOR_LOG, EMULATOR_LOG):
            if log.exists():
                print(f"--- {log.name}", log.read_text(errors="replace")[-3000:], sep="\n", flush=True)
        if emulator and emulator.poll() is None:
            print(adb("logcat", "-d", "-s", "OpenAmpere:*", "python.stderr:*", check=False, timeout=30)[-6000:],
                  flush=True)
        raise
    finally:
        simulator.terminate()
        if emulator:
            adb("emu", "kill", check=False, timeout=30)
            try:
                emulator.wait(timeout=60)
            except subprocess.TimeoutExpired:
                emulator.kill()


if __name__ == "__main__":
    main()
