<!-- SPDX-FileCopyrightText: Copyright the OpenAmpere contributors -->
<!-- SPDX-License-Identifier: MIT -->
# Signing the Android app (maintainers)

The Android app (#567) is distributed only through GitHub releases. Android installs an update only when it is
signed with the same key as the installed app, so the **release key is what owners trust**, the way cosign and the
build provenance are for the Docker image:

- **Losing the key** means no owner can update any more: everyone would have to uninstall the app, which deletes its
  data (history, settings, password), and install a new one.
- **Someone with the key** can sign an app that Android installs as an update over OpenAmpere.

## Creating the key (once)

```bash
scripts/android-key.sh openampere-release.jks
```

The script asks for a password (keytool), writes the certificate's SHA-256 to `android/signing-certificate.sha256`
and prints the commands that set the secrets of the release workflow (`ANDROID_KEYSTORE` as base64,
`ANDROID_KEYSTORE_PASSWORD`). Commit the fingerprint file. It refuses to overwrite an existing file: never make a
second key for the same app.

## Backup

Right after creating it, keep the `.jks` file and its password **offline in two places**, for example two USB sticks
or a password manager plus paper, held by different maintainers. The secrets in GitHub are not a backup: they cannot
be read back. Delete the working copy of the `.jks` file from the computer once the backup and the secrets are done.

## In the release

The `android` job of `.github/workflows/release.yml` builds the APK from the version tag, signs it with the key from
the secrets and checks that its certificate is the one in `android/signing-certificate.sha256`, so a replaced secret
fails the release instead of shipping an app no owner can update to. The release gets `openampere-<version>.apk`, its
line in `SHA256SUMS` and the build provenance:

```bash
gh attestation verify openampere-<version>.apk --repo Gr33ndev/OpenAmpere \
  --signer-workflow Gr33ndev/OpenAmpere/.github/workflows/release.yml --source-ref refs/tags/v<version>
apksigner verify --print-certs openampere-<version>.apk   # SHA-256 as in android/signing-certificate.sha256
```

The fingerprint is published in the README and on the website with the install guide (#567, step 5), so owners can
check the first install.

## If the key is ever exposed

Remove the secrets and say so in a security advisory (docs/security-advisories.md) and in the README. From Android 9
on, the key can be rotated (APK Signature Scheme v3, `apksigner rotate`): the old key signs the move to a new one,
and installed apps accept updates with the new key from then on, but someone holding the old key can still sign
updates for tablets that have not received the rotation yet. Android 7 and 8 have no rotation: owners there need a
new app id and a guide to move the data (backup in the old app, restore in the new one). A lost key cannot be
rotated at all.
