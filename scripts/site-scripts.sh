#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
# Writes install.sh, tailscale.sh and updater.sh plus their SHA256SUMS for the website into a folder.
# Usage (from the repository root): scripts/site-scripts.sh <folder>
# They come from the newest stable release tag, so a change on main reaches new installations only with a release.
# SCRIPTS_REF=<git ref> takes them from another ref instead (e.g. SCRIPTS_REF=HEAD). Without a v* tag (e.g. a
# shallow clone) they come from the working tree. Runs no code of dependencies: the website workflow calls it in the
# job that assembles the site, so the npm packages that build the site cannot change what users install (pages.yml).
set -euo pipefail
cd "$(dirname "$0")/.."

out=${1:?folder}
scripts="install.sh tailscale.sh updater.sh"
scripts_ref=${SCRIPTS_REF:-$(git tag -l 'v*' --sort=-v:refname 2>/dev/null | grep -v -- - | head -n 1 || true)}

mkdir -p "$out"
if [ -n "$scripts_ref" ]; then
  echo "install scripts from $scripts_ref"
  for f in $scripts; do git show "$scripts_ref:scripts/$f" > "$out/$f"; done
else
  echo "warning: no release tag found, install scripts from the working tree" >&2
  for f in $scripts; do cp "scripts/$f" "$out/"; done
fi
sha256() { if command -v sha256sum >/dev/null; then sha256sum "$@"; else shasum -a 256 "$@"; fi; }
(cd "$out" && sha256 $scripts > SHA256SUMS && cat SHA256SUMS)
