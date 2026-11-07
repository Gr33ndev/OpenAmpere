<!-- SPDX-FileCopyrightText: Copyright the OpenAmpere contributors -->
<!-- SPDX-License-Identifier: MIT -->
# OpenAmpere for Android

The Android app (#567) runs the unchanged server of OpenAmpere on a tablet and shows the web app. It is a way to run
OpenAmpere for owners without a Linux server. The guide for owners is [docs/android.de.md](../docs/android.de.md)
(English: [docs/android.md](../docs/android.md)).

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
scripts/android-wheels.sh                    # pydantic-core, cryptography, cffi into android/wheels/ (minutes)
cd android && ./gradlew assembleDebug        # app/build/outputs/apk/debug/app-debug.apk
```

Chaquopy's package index has Android builds of most packages, but not of `pydantic-core` (Rust, needed by FastAPI),
and `cryptography` and `cffi` only in old versions. `scripts/android-wheels.sh` builds these three in the versions of
`requirements.lock`, like the Docker image has them, for each ABI against Chaquopy's Python: `cryptography` against
the OpenSSL that Chaquopy's Python brings along (headers and libraries from its package on Maven Central), so the
app has only one OpenSSL, and `cffi` against Chaquopy's `chaquopy-libffi`. `pyyaml` comes as Chaquopy's build. pip
checks every file that goes into the APK: the hashes of `requirements.lock`, and in `app/build.gradle.kts` those of
Chaquopy's builds and of the wheels built here. The script checks its downloads as well (the source archives against
`requirements.lock`, Chaquopy's Python and libffi against fixed hashes); maturin comes from
`android/requirements-build.lock` (`scripts/lock.sh`), the `cffi` that writes cryptography's C code on the computer
from `requirements.lock`.

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

## Updates

The app has no updater (#610). Up to 0.40 an `Updater` in the app downloaded and installed new versions; Google Play
Protect classifies every app from outside the Play Store with `REQUEST_INSTALL_PACKAGES` or
`UPDATE_PACKAGES_WITHOUT_USER_ACTION` as harmful (`generic_malware`), even a new release it has never seen, and blocks
its installation (tests in #608). So the manifest asks for neither, and the server in Android mode never sees an
updater (`updates.py`): the app shows a new version with a link to the release, and the owner installs the APK like the
first time. Never add either permission again, nor a library that brings one along: check the merged manifest of a
release APK (`aapt2 dump permissions`).

What is left of `Updater`: after every update `BootReceiver` reports "done" in the web app (so a failure the 0.40
updater left, e.g. refused by Play Protect, does not stay on the update card after an update by hand), and after the
update a 0.40 app starts for the next version (its marker `update/installing`), if the app was in front (a tablet on
the wall), brings it back (before Android 10 by itself, from Android 10 through a notification "Version …
is installed", since Android does not let an app come to the front by itself). `BootReceiver` starts the server again
after every update, also one installed by hand.

To see Play Protect's verdict on an APK, use an emulator with a Google Play image (`google_apis_playstore`), sign in to
a Google account there, copy the APK to `Download` and install it from the Files app; `adb logcat | grep VerifyApps`
shows the `JIT scan response`.

## Smoke test

`scripts/android_smoke_test.py <apk>` starts an emulator (`--image`, default Android 11 x86_64; on an Apple computer
`system-images;android-30;google_apis;arm64-v8a`), installs the APK and checks that the server starts in Android
mode, reads the simulator on the computer (`10.0.2.2`), shows live values and comes back after its process was
killed. CI runs it on every change (job `android-emulator`, with the runner's KVM). It needs `openampere` installed
for the simulator and a `google_apis` image (it uses `adb root` to kill the process as Android would).
