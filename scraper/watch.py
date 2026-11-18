"""Where films can be watched instead (streaming, free, with ads, rent, buy), for the page of a film with no showings.

Only films that need it are looked up: no showings in any location's data, and marked as wanted (film_marks: someone put it on a
watchlist at some point, signed in or not; the mark is private and stays). Each is asked once when it first needs it, then again
after RECHECK_DAYS while it still does. One TMDB request per film gives every country; one row per country the site covers is kept (also when there's
nothing, so it isn't asked again) in Supabase's watch_options. TMDB's data comes from JustWatch, which the site credits.

Usage from build.py, after filmids.sync():  watch.update(reg, films_with_showings, countries)   # never raises
"""
import sys
import urllib.error
from datetime import datetime, timedelta, timezone

import external
import filmids

RECHECK_DAYS = 14
BUDGET = 100  # TMDB requests per run (the first runs work through a backlog)
KINDS = (("flatrate", "stream"), ("free", "free"), ("ads", "ads"), ("rent", "rent"), ("buy", "buy"))


def options_for(country_data):
    """TMDB's entry for one country -> ({kind: [{name, logo}]}, link). Services in TMDB's order, each once per kind."""
    out = {}
    for src, kind in KINDS:
        seen, items = set(), []
        for p in sorted(country_data.get(src) or [], key=lambda p: p.get("display_priority", 999)):
            name = (p.get("provider_name") or "").strip()
            if name and name not in seen:
                seen.add(name)
                items.append({"name": name, "logo": p.get("logo_path") or ""})
        if items:
            out[kind] = items
    link = country_data.get("link") or ""
    return out, link if link.startswith("https://") else ""


def due(candidates, checked, now, budget=BUDGET):
    """The films to ask about this run: never checked first, then the longest ago (only those checked RECHECK_DAYS or more ago)."""
    cutoff = now - timedelta(days=RECHECK_DAYS)
    never = sorted(f for f in candidates if f not in checked)
    stale = sorted((f for f in candidates if f in checked and checked[f] <= cutoff), key=lambda f: checked[f])
    return (never + stale)[:budget]


def candidates(reg, marked, with_showings):
    """Film ids (as they are now) that someone has wanted to see, with no showings anywhere, matched on TMDB."""
    out = set()
    for i in marked:
        fid = reg.film_of(i) or (reg.canon(i) if i in reg.films else None)
        if fid and fid not in with_showings and reg.films.get(fid, {}).get("tmdb"):
            out.add(fid)
    return out


def _when(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)


def update(reg, with_showings, countries):
    if not filmids._config()[0] or reg.source != "supabase" or not external.tmdb_enabled():
        return
    try:
        try:
            marked = [r["film_id"] for r in filmids._rows("film_marks", "select=film_id")]
            rows = filmids._rows("watch_options", "select=film_id,checked_at")
        except urllib.error.HTTPError as e:
            if e.code in (400, 404):
                print("  where to watch: the database has no film_marks / watch_options yet (re-run supabase/schema.sql); skipped")
                return
            raise
        checked = {}
        for r in rows:  # a film's rows are written together; the oldest counts
            t = _when(r["checked_at"])
            checked[r["film_id"]] = min(t, checked.get(r["film_id"], t))
        want = candidates(reg, marked, with_showings)
        todo = due(want, checked, datetime.now(timezone.utc))
        out = []
        for fid in todo:
            try:
                res = external.tmdb_get(f"/movie/{reg.films[fid]['tmdb']}/watch/providers").get("results") or {}
            except Exception as e:
                print(f"  ! where to watch {reg.films[fid].get('title')}: {e}", file=sys.stderr)
                continue
            for c in countries:
                opts, link = options_for(res.get(c) or {})
                out.append({"film_id": fid, "country": c, "options": opts, "link": link})
        if out:
            filmids._request("rpc/sync_watch_options", {"p": out}, timeout=120)
        print(f"  where to watch: {len(want)} wanted films with no showings; checked {len(todo)} "
              f"({sum(1 for f in todo if f not in checked)} new)")
    except Exception as e:
        print(f"  ! where to watch failed: {e}", file=sys.stderr)
