#!/usr/bin/env bash
# Builds the project website into _site/: landing page (site/) + demo of the web app (_site/demo/).
# Usage (from the repository root, after `npm ci` in web/): scripts/build-site.sh
set -euo pipefail
cd "$(dirname "$0")/.."

rm -rf _site
(cd web && npm run build:demo)
cp site/index.html site/impressum.html site/style.css _site/
cp web/public/icon.svg _site/
cp scripts/install.sh scripts/updater.sh _site/
mkdir -p _site/fonts
fonts=web/node_modules/@fontsource-variable/dm-sans
cp "$fonts/files/dm-sans-latin-wght-normal.woff2" "$fonts/files/dm-sans-latin-ext-wght-normal.woff2" _site/fonts/
cp "$fonts/LICENSE" _site/fonts/OFL.txt
touch _site/.nojekyll
echo "site built in _site/"
