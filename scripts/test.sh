#!/bin/sh
# Every test: the scraper's film ids and cards, the app's syntax, the database schema, and the app in a browser.
#   sh scripts/test.sh
# The database tests need Postgres: a local install (started and thrown away by supabase/tests/run.sh), or DATABASE_URL pointing at
# a scratch server (as in CI). Never the real Supabase project.
set -eu
cd "$(dirname "$0")/.."
echo "== scraper (film ids, cards, details, where to watch, recovery)"
python3 scraper/test_filmids.py | tail -1
python3 scraper/test_watch.py | tail -1
python3 scraper/test_details.py | tail -1
python3 scraper/test_recover.py | tail -1
echo "== app (syntax)"
node --check site/app.js && echo "site/app.js ok"
echo "== database (schema, cards, resolve_films, pruning, plans)"
sh supabase/tests/run.sh
echo "== app in a browser (Not showing section, unlisted films' pages)"
if NODE_PATH="${NODE_PATH:-$(npm root -g 2>/dev/null)}" node -e "require('playwright')" 2>/dev/null; then
  out=$(NODE_PATH="${NODE_PATH:-$(npm root -g)}" node scripts/test-app.js 2>&1) || { echo "$out"; exit 1; }
  echo "$out" | tail -1
else
  echo "skipped: Playwright isn't installed (npm install playwright && npx playwright install chromium)"
fi
