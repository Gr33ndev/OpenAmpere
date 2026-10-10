#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
# SPDX-License-Identifier: MIT
# Takes the README screenshots from the demo (made-up values) with headless Chrome, iPhone size at 2x.
# Usage (from the repository root, after `npm ci` in web/): scripts/screenshots.sh
set -euo pipefail
cd "$(dirname "$0")/.."

CHROME=${CHROME:-"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"}
OUT=docs/screenshots
PORT=8095

(cd web && npm run -s build:demo)
python3 -m http.server "$PORT" -d _site >/dev/null 2>&1 &
server=$!
trap 'kill $server' EXIT
sleep 1

# headless Chrome has a minimum window width, so the demo runs in a centred phone-sized frame that is cropped afterwards
cat > _site/screenshot.html <<'HTML'
<!doctype html><meta charset="utf-8"><style>html,body{margin:0;background:#000}iframe{border:0;width:390px;height:844px;display:block;margin:0 auto}</style>
<iframe></iframe><script>document.querySelector("iframe").src = "demo/index.html?screenshot#/" + location.hash.slice(1)</script>
HTML

shot() {  # name route [dark]
  local scheme=()
  [ "${3:-}" = dark ] && scheme=(--blink-settings=preferredColorScheme=0)
  "$CHROME" --headless=new --disable-gpu --hide-scrollbars --window-size=600,844 --force-device-scale-factor=2 \
    --virtual-time-budget=6000 ${scheme[@]+"${scheme[@]}"} --screenshot="$OUT/$1.png" \
    "http://localhost:$PORT/screenshot.html#$2" >/dev/null 2>&1
  sips -c 1688 780 "$OUT/$1.png" >/dev/null  # macOS: keep the centred 390 x 844 frame at 2x
  echo "$OUT/$1.png"
}

mkdir -p "$OUT"
shot overview dashboard
shot devices devices
shot report report
shot overview-dark dashboard dark
