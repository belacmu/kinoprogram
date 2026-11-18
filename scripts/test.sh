#!/bin/sh
# Every test: the scraper's film ids and cards, the app's syntax, and the database schema.
#   sh scripts/test.sh
# The database tests need Postgres: a local install (started and thrown away by supabase/tests/run.sh), or DATABASE_URL pointing at
# a scratch server (as in CI). Never the real Supabase project.
set -eu
cd "$(dirname "$0")/.."
echo "== scraper (film ids, cards)"
python3 scraper/test_filmids.py | tail -1
echo "== app (syntax)"
node --check site/app.js && echo "site/app.js ok"
echo "== database (schema, cards, resolve_films, pruning, plans)"
sh supabase/tests/run.sh
