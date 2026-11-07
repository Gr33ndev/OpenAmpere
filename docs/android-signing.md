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
and prints the commands for the rest. Commit the fingerprint file. It refuses to overwrite an existing file: never make
a second key for the same app.

The secrets (`ANDROID_KEYSTORE` as base64, `ANDROID_KEYSTORE_PASSWORD`) go into the GitHub environment
`android-release`, never into the repository's secrets: a repository secret reaches every workflow run of every
branch, so anyone who can push a branch could change a workflow to print it. The environment's deployment rule admits
only `v*` tags and `main` (for a run by hand), and only the `android` job of the release workflow uses it. The
script prints the commands that create the environment and set the secrets in it.

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

The fingerprint is published in the README, on the website with the install guide and in GOVERNANCE.md (#567,
step 5), so owners can check the first install. GOVERNANCE.md also says who holds the key: handing it over decides who
can ship updates to tablets, like the account and repository names do for the Docker image.

A release candidate (`v1.2.3-rc.1` to `-rc.8`, the only pre-release form `scripts/release.sh` and the workflow
accept) gets an APK as well, with a `versionCode` below the final version, so the final version is still an update
for a tablet that installed the release candidate.

## Google developer verification

Certified Android devices install and update only apps registered by a verified developer: in a few countries since
September 2026, everywhere from 2027 ([Google's guide](https://developer.android.com/developer-verification/guides)).
OpenAmpere is registered (#608):

- The maintainer has an account in the [Android Developer Console](https://android.google.com/developerconsole) (for
  apps outside Google Play), "full distribution", verified as an individual. The account for students and hobbyists
  allows only 20 devices.
- The package name `io.github.gr33ndev.openampere` is registered there with the fingerprint from
  `android/signing-certificate.sha256`.
- Ownership was proven once: the console gives a snippet that goes into `assets/adi-registration.properties` of an
  APK signed with the release key. The maintainer added it to a release APK and signed that copy on their own computer
  with the key from the offline backup; the copy was only uploaded to the console. The snippet is not committed, and
  no workflow gets the key for this.

Nothing changes for releases: every APK signed with the same key counts as registered. Only a new key (see below) or
a new package name has to be registered again, with a new proof. A device checks the registration before installing;
on a Google Play emulator image, `adb logcat` shows `VerificationCheck: … Result: Pass` for a registered app.

The developer account decides, like the key, who can ship the app to devices: GOVERNANCE.md says who holds it.

## Google Play Protect

Play Protect checks apps from outside the Play Store when they are installed and may warn or block them. Its verdict
comes from Google's servers and names no reason; the developer verification does not change it. A wrong verdict can
only be appealed with Google's [Play Protect appeal](https://support.google.com/googleplay/android-developer/contact/protectappeals)
(one per enforcement action, no answer; the APK has to be on VirusTotal first, the form asks for its hash there).
`docs/android.md` tells owners what to do when Play Protect warns. #608 has what happened with 0.39.0.

## If the key is ever exposed

Remove the secrets and say so in a security advisory (docs/security-advisories.md) and in the README. From Android 9
on, the key can be rotated (APK Signature Scheme v3, `apksigner rotate`): the old key signs the move to a new one,
and installed apps accept updates with the new key from then on, but someone holding the old key can still sign
updates for tablets that have not received the rotation yet. Android 7 and 8 have no rotation: owners there need a
new app id and a guide to move the data (backup in the old app, restore in the new one). A lost key cannot be
rotated at all.
