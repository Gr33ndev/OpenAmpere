#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
# Builds the project website into _site/: landing page (site/) + demo of the web app (_site/demo/).
# Usage (from the repository root, after `npm ci` in web/): scripts/build-site.sh
# The pages are rendered by scripts/build-site-pages.mjs from the templates in site/pages/ and the texts in
# site/locales/<lang>/: German at the root (index.html, faq.html, impressum.html), every other language of the site
# or of the web app (web/src/locales/) in _site/<lang>/. A new language needs only a new folder, see that script.
# The site comes from the working tree, but install.sh, tailscale.sh and updater.sh (plus their SHA256SUMS)
# come from the newest stable release tag, so a change on main reaches new installations only with a
# release. SCRIPTS_REF=<git ref> takes them from another ref instead (e.g. SCRIPTS_REF=HEAD). Without a
# v* tag (e.g. a shallow clone) they come from the working tree.
set -euo pipefail
cd "$(dirname "$0")/.."

scripts="install.sh tailscale.sh updater.sh"
scripts_ref=${SCRIPTS_REF:-$(git tag -l 'v*' --sort=-v:refname 2>/dev/null | grep -v -- - | head -n 1 || true)}

rm -rf _site
(cd web && npm run build:demo)
node scripts/build-site-pages.mjs _site
cp site/style.css _site/
cp web/public/icon.svg _site/
if [ -n "$scripts_ref" ]; then
  echo "install scripts from $scripts_ref"
  for f in $scripts; do git show "$scripts_ref:scripts/$f" > "_site/$f"; done
else
  echo "warning: no release tag found, install scripts from the working tree" >&2
  for f in $scripts; do cp "scripts/$f" _site/; done
fi
sha256() { if command -v sha256sum >/dev/null; then sha256sum "$@"; else shasum -a 256 "$@"; fi; }
(cd _site && sha256 $scripts > SHA256SUMS && cat SHA256SUMS)
mkdir -p _site/fonts
fonts=web/node_modules/@fontsource-variable/dm-sans
cp "$fonts/files/dm-sans-latin-wght-normal.woff2" "$fonts/files/dm-sans-latin-ext-wght-normal.woff2" _site/fonts/
cp "$fonts/LICENSE" _site/fonts/OFL.txt
touch _site/.nojekyll
echo "site built in _site/"
