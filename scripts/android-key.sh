#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
# Creates the release key of the Android app (#567), once, on a maintainer's computer. See docs/android-signing.md:
# Android installs an update only when it is signed with the same key, so losing the key means every owner has to
# uninstall the app (and loses its data), and someone with the key can sign an update that Android accepts.
#
#   scripts/android-key.sh openampere-release.jks
#
# keytool asks for the password. Afterwards: back the file and the password up offline, set the secrets of the
# release workflow (the commands are printed) and commit android/signing-certificate.sha256.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STORE="${1:?usage: scripts/android-key.sh <file.jks>}"
[ -e "$STORE" ] && { echo "$STORE exists already: never make a second key for the same app" >&2; exit 1; }

keytool -genkeypair -keystore "$STORE" -storetype PKCS12 -alias openampere -keyalg RSA -keysize 4096 \
  -validity 36500 -dname "CN=OpenAmpere, O=OpenAmpere contributors"
fingerprint=$(keytool -list -v -keystore "$STORE" -alias openampere | sed -n 's/^[[:space:]]*SHA256: //p' \
  | tr -d ':' | tr 'A-F' 'a-f')
echo "$fingerprint" > "$ROOT/android/signing-certificate.sha256"
cat <<TEXT

Certificate SHA-256: $fingerprint
Written to android/signing-certificate.sha256 (commit it; the release workflow checks the APK against it).

Secrets of the release workflow (the password is asked for):
  base64 < "$STORE" | gh secret set ANDROID_KEYSTORE --repo Gr33ndev/OpenAmpere
  gh secret set ANDROID_KEYSTORE_PASSWORD --repo Gr33ndev/OpenAmpere

Now back up $STORE and the password offline, in two places (docs/android-signing.md).
TEXT
