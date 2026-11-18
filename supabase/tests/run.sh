#!/bin/sh
# Database tests: applies stubs.sql and schema.sql (twice: it must be safe to re-run) to a fresh, empty database, then test.sql.
#   DATABASE_URL=postgres://user@host:port/postgres sh supabase/tests/run.sh   # a server to make a scratch database on (CI)
#   sh supabase/tests/run.sh                                                   # no DATABASE_URL: starts a throwaway local Postgres
# Never point DATABASE_URL at the real Supabase project: the tests make and drop their own database "cinecrab_test" there.
set -eu
cd "$(dirname "$0")"
DB=cinecrab_test

if [ -z "${DATABASE_URL:-}" ]; then
  BIN=$(ls -d /usr/lib/postgresql/*/bin 2>/dev/null | sort -V | tail -1)
  [ -n "$BIN" ] || BIN=$(dirname "$(command -v initdb || echo /nonexistent/initdb)")
  [ -x "$BIN/initdb" ] || { echo "No DATABASE_URL and no local Postgres (initdb) found" >&2; exit 1; }
  TMP=$(mktemp -d /var/tmp/cinecrab-pg.XXXXXX)
  RUN="sh -c"
  if [ "$(id -u)" = 0 ]; then chown postgres "$TMP"; RUN="su postgres -c"; fi   # Postgres won't run as root
  $RUN "$BIN/initdb -D $TMP/data -A trust -U postgres >/dev/null && $BIN/pg_ctl -D $TMP/data -o '-k $TMP -p 5499 -c listen_addresses=' -l $TMP/log -w start >/dev/null"
  trap '$RUN "$BIN/pg_ctl -D $TMP/data -m fast stop >/dev/null" || true; rm -rf "$TMP"' EXIT
  DATABASE_URL="postgresql://postgres@/postgres?host=$TMP&port=5499"
fi

psql "$DATABASE_URL" -q -v ON_ERROR_STOP=1 -c "drop database if exists $DB" -c "create database $DB"
URL=$(printf '%s' "$DATABASE_URL" | sed -E "s#/[^/?]*(\?|$)#/$DB\1#")
q() { psql "$URL" -q -v ON_ERROR_STOP=1 -X "$@"; }
q -f stubs.sql >/dev/null
for i in 1 2; do PGOPTIONS='-c client_min_messages=warning' q -f ../schema.sql >/dev/null; done
echo "schema.sql applied twice"
PGOPTIONS='-c client_min_messages=warning' q -f test.sql
psql "$DATABASE_URL" -q -c "drop database $DB"
