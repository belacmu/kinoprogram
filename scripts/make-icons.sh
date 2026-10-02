#!/bin/sh
# Redraws the home-screen/app icons in site/icons/ from scripts/icons.html, using headless Chrome (needs the network
# for the font). Run once after changing the design; the PNGs are committed.
# Usage: sh scripts/make-icons.sh
set -e
cd "$(dirname "$0")/.."
CHROME=${CHROME:-"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"}
"$CHROME" --headless --disable-gpu --virtual-time-budget=10000 --dump-dom "file://$PWD/scripts/icons.html" 2>/dev/null \
  | sed -n '/<pre id="out">/,/<\/pre>/p' | sed -e 's/.*<pre id="out">//' -e 's/<\/pre>.*//' \
  | while read -r name data; do
      [ -n "$data" ] || continue
      printf '%s' "$data" | base64 -d > "site/icons/$name"
      echo "  site/icons/$name"
    done
