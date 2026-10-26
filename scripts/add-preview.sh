#!/bin/sh
# Publishes the work-in-progress branch `preview` at <site>/preview/, next to the live site (built from main).
# GitHub Pages serves one site and each deploy replaces all of it, so both deploy workflows run this after the live
# site is assembled. No `preview` branch, or one that can't be used: nothing happens and the live site deploys as normal.
# The preview shares the live site's programme data (../data) and its Supabase config, so it signs in with the same account.
# Usage (in CI, from the repo root, after scripts/write-site-config.sh and scripts/write-film-pages.py): sh scripts/add-preview.sh
set -e
dir=site/preview
rm -rf "$dir"
if ! git fetch --quiet --depth=1 origin preview 2>/dev/null; then echo "No preview branch: skipping the preview."; exit 0; fi
v=$(git rev-parse --short=8 FETCH_HEAD)
mkdir -p "$dir"
if ! git archive FETCH_HEAD site | tar -x --strip-components=1 -C "$dir" || [ ! -f "$dir/index.html" ]; then
  echo "::warning::The preview branch has no site/index.html: skipping the preview."
  rm -rf "$dir"; exit 0
fi
# Same Supabase config as the live site; programme data comes from the live site's copy, one level up.
{ cat site/config.js; echo 'window.KINO_BASE = "../";'; } > "$dir/config.js"
[ -d site/f ] && cp -r site/f "$dir/f"   # shared film links (they lead on to the live site)
sed -i -e "s#src=\"app.js\"#src=\"app.js?v=$v\"#" -e "s#href=\"style.css\"#href=\"style.css?v=$v\"#" \
       -e "s#src=\"config.js\"#src=\"config.js?v=$v\"#" \
       -e 's#<head>#<head>\n<meta name="robots" content="noindex">#' \
       -e 's#<body\([^>]*\)>#<body\1><div style="position:fixed;left:8px;bottom:8px;z-index:99999;background:\#a3213a;color:\#fff;font:700 11px/1 sans-serif;letter-spacing:.08em;padding:5px 9px;border-radius:999px;pointer-events:none">PREVIEW</div>#' \
       "$dir/index.html"
echo "Preview built from the preview branch at $v."
