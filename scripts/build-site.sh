#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
# Builds the project website into _site/: landing page (site/) + demo of the web app (_site/demo/).
# Usage (from the repository root, after `npm ci` in web/): scripts/build-site.sh
# The pages are rendered by scripts/build-site-pages.mjs from the templates in site/pages/ and the texts in
# site/locales/<lang>/: German at the root (index.html, faq.html, impressum.html), every other language of the site
# or of the web app (web/src/locales/) in _site/<lang>/. A new language needs only a new folder, see that script.
# The same script renders the device knowledge of docs/devices/ into docs/devices/ of every language (#512).
# The site comes from the working tree, install.sh, tailscale.sh and updater.sh (plus their SHA256SUMS) from
# scripts/site-scripts.sh (the newest stable release tag). SITE_SCRIPTS=skip leaves them out: the website workflow
# adds them in a job without npm.
set -euo pipefail
cd "$(dirname "$0")/.."

rm -rf _site
(cd web && npm run build:demo)
node scripts/build-site-pages.mjs _site
cp site/style.css _site/
cp web/public/icon.svg _site/
# the buttons of My Home Assistant, served from here: a visit sends nothing to my.home-assistant.io (#295)
mkdir -p _site/badges && cp site/badges/*.svg site/badges/LICENSE.md _site/badges/
[ "${SITE_SCRIPTS:-}" = skip ] || scripts/site-scripts.sh _site
mkdir -p _site/fonts
fonts=web/node_modules/@fontsource-variable/dm-sans
cp "$fonts/files/dm-sans-latin-wght-normal.woff2" "$fonts/files/dm-sans-latin-ext-wght-normal.woff2" _site/fonts/
cp "$fonts/LICENSE" _site/fonts/OFL.txt
touch _site/.nojekyll
echo "site built in _site/"
