<!-- SPDX-FileCopyrightText: Copyright the OpenAmpere contributors -->
<!-- SPDX-License-Identifier: MIT -->
# OpenAmpere for Android

The Android app (#567) runs the unchanged server of OpenAmpere on a tablet and shows the web app. It is the fourth way
to run OpenAmpere, for owners without a Linux server.

## How it works

- **Server:** [Chaquopy](https://chaquo.com/chaquopy/) puts Python 3.11 and the `openampere` package (with the built
  web app) into the APK. `ServerService` runs it in a foreground service in a process of its own (`:server`) with a
  permanent notification and keeps the CPU and Wi-Fi awake. When the server stops (crash, or a restart it asked for,
  such as after a restored backup), only that process is started again, by an exact alarm 5 s later: from Android 12
  only an exact alarm may start a foreground service from the background. A server that fails three times within a
  minute of starting (another app on port 8080, a database of a newer version) is not started again: the reason is in
  a notification and on the app's page until the app is opened again or the tablet starts. `BootReceiver` starts it
  after the tablet was switched on and after an update.
- **Android mode:** `openampere_app.py` sets `OPENAMPERE_ANDROID` (see `src/openampere/android.py`), the data folder in
  the app's private storage and the fixed port 8080. Other devices in the home network open the web app at the
  tablet's address with port 8080, as on the other ways.
- **Texts:** notification, starting page and messages come from `web/src/locales/<lang>/android.json` like every text
  of the app; the build turns them into Android resources (`R.string.<group>_<name>`), `npm run check:i18n` checks them.
- **App:** `MainActivity` shows the web app of `127.0.0.1:8080` in a WebView. Links to other sites open in the browser;
  files the web app offers are saved where the owner picks (Android's "save as" dialog, no storage permission).
- **Minimum:** Android 7.0 (API 24), 32-bit and 64-bit ARM. The WebView of old tablets can be old: the web app must not
  need newer browser features than Chrome 91 (the emulator image below).

## Building

Needs JDK 17, the Android SDK (`ANDROID_HOME`) with NDK 27.2.12479018, Rust (rustup), Node.js and Python 3.11.

```bash
cd web && npm ci && npm run build && cd ..   # the web app goes into the Python package
scripts/android-wheels.sh                    # pydantic-core for Android into android/wheels/ (a few minutes)
cd android && ./gradlew assembleDebug        # app/build/outputs/apk/debug/app-debug.apk
```

Chaquopy's package index has Android builds of most packages, but not of `pydantic-core` (Rust, needed by FastAPI);
`scripts/android-wheels.sh` builds it for each ABI against Chaquopy's Python. `cryptography` and `cffi` come in the
older versions Chaquopy has (pyproject.toml allows them), `pyyaml` as Chaquopy's build. pip checks every file that
goes into the APK: the hashes of `requirements.lock`, and in `app/build.gradle.kts` those of Chaquopy's builds and of
the wheels built here. The script checks its downloads as well; maturin comes from `android/requirements-build.lock`
(`scripts/lock.sh`).

## Trying it in the emulator

```bash
sdkmanager "emulator" "system-images;android-30;google_apis;arm64-v8a"
avdmanager create avd -n openampere -k "system-images;android-30;google_apis;arm64-v8a" -d pixel_c
emulator -avd openampere &
adb install -r app/build/outputs/apk/debug/app-debug.apk
adb forward tcp:18080 tcp:8080      # the web app of the emulator at http://127.0.0.1:18080 on the computer
```

The emulator reaches the computer at `10.0.2.2`: start the simulator there (`python -m openampere.simulator --port
5020`) and set up the inverter `10.0.2.2`, port 5020. Debug builds can be inspected with Chrome on the computer
(`chrome://inspect`). An Android 14 image (`system-images;android-34;google_apis;arm64-v8a`) shows the limits
of newer versions, e.g. starting the server from the background.

## Release

The release workflow builds the APK from the version tag, signs it with the release key and attaches it as
`openampere-<version>.apk` with its checksum and build provenance (docs/android-signing.md). The license list of what
only the app ships comes from the APK: `python3 scripts/android_licenses.py <apk>` writes
`web/public/third-party-licenses-android.json` (shown on the tablet's licenses page) and `THIRD_PARTY_LICENSES.md`
here; CI checks both. It lists the native parts (`licenses/components.json`, versions updated by hand with Chaquopy or
Kotlin) and the Chaquopy builds pinned in `app/build.gradle.kts`, so the weekly update of `requirements.lock` never
changes it; regenerate it after changing those pins.

## Not yet

The built-in updater and an emulator test in CI (step 4 of #567), the guide and the website (step 5).
