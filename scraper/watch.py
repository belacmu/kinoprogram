"""Where films can be watched at home (streaming, free, with ads, rent, buy), for the page of a film with no showings here.

Looked up for every film someone has wanted to see (film_marks: put on a watchlist at some point, signed in or not; the mark is
private and stays), matched on TMDB. One TMDB request per film gives every country's services and release dates; one row per country
the site covers is kept in Supabase's watch_options (also when there's nothing, so it isn't asked again before its time), with the
country's digital release date in options["digital"] ("YYYY-MM-DD": the next one if there's one ahead, else the latest; "" if none).
TMDB's data comes from JustWatch, which the site credits.

When a film is asked again (next_check): every RECHECK_DAYS while it has no services anywhere (sooner, the day after a digital
release date ahead of it); every RECHECK_FOUND_DAYS once it has some; never again once its digital release is over GIVE_UP_YEARS
past and it still has none. Never-checked films come first; at most BUDGET a run.

Usage from build.py, after filmids.sync():  watch.update(reg, countries)   # never raises
"""
import sys
import urllib.error
from datetime import date, datetime, timedelta, timezone

import external
import filmids

RECHECK_DAYS = 14
RECHECK_FOUND_DAYS = 60
GIVE_UP_YEARS = 2
DIGITAL = 4  # TMDB's release type for a digital release
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


def digital_date(release_dates, country, today):
    """The country's digital release date from TMDB's release_dates: the next one ahead of today, else the latest; "" if none."""
    days = sorted({(d.get("release_date") or "")[:10] for r in release_dates if r.get("iso_3166_1") == country
                   for d in r.get("release_dates") or [] if d.get("type") == DIGITAL and (d.get("release_date") or "")[:10]})
    ahead = [d for d in days if d > today]
    return ahead[0] if ahead else (days[-1] if days else "")


def next_check(state, now):
    """When to ask about a film again, from what its last check found (state: {checked, found, digital: [dates]}), or None: never."""
    checked, today = state["checked"], now.date().isoformat()
    if state["found"]:
        return checked + timedelta(days=RECHECK_FOUND_DAYS)
    ahead = sorted(d for d in state["digital"] if d > today)
    if ahead:  # the day after its digital release, if that comes before the usual recheck
        after = datetime.fromisoformat(ahead[0]).replace(tzinfo=timezone.utc) + timedelta(days=1)
        return min(checked + timedelta(days=RECHECK_DAYS), after)
    if state["digital"] and max(state["digital"]) < (now.date() - timedelta(days=365 * GIVE_UP_YEARS)).isoformat():
        return None  # released digitally long ago and still nowhere: stop asking
    return checked + timedelta(days=RECHECK_DAYS)


def due(candidates, states, now, budget=BUDGET):
    """The films to ask about this run: never checked first, then the most overdue."""
    never = sorted(f for f in candidates if f not in states)
    when = {f: next_check(states[f], now) for f in candidates if f in states}
    overdue = sorted((f for f, t in when.items() if t is not None and t <= now), key=lambda f: when[f])
    return (never + overdue)[:budget]


def candidates(reg, marked):
    """Film ids (as they are now) that someone has wanted to see, matched on TMDB."""
    out = set()
    for i in marked:
        fid = reg.film_of(i) or (reg.canon(i) if i in reg.films else None)
        if fid and reg.films.get(fid, {}).get("tmdb"):
            out.add(fid)
    return out


def states_from(rows):
    """watch_options rows -> {film id: {checked (the oldest of its rows), found (services in any country), digital: [dates]}}."""
    out = {}
    for r in rows:
        st = out.setdefault(r["film_id"], {"checked": _when(r["checked_at"]), "found": False, "digital": []})
        st["checked"] = min(st["checked"], _when(r["checked_at"]))
        opts = r.get("options") or {}
        st["found"] |= any(opts.get(k) for _, k in KINDS)
        if opts.get("digital"):
            st["digital"].append(opts["digital"])
    return out


def _when(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)


def update(reg, countries):
    if not filmids._config()[0] or reg.source != "supabase" or not external.tmdb_enabled():
        return
    try:
        try:
            marked = [r["film_id"] for r in filmids._rows("film_marks", "select=film_id")]
            rows = filmids._rows("watch_options", "select=film_id,checked_at,options")
        except urllib.error.HTTPError as e:
            if e.code in (400, 404):
                print("  where to watch: the database has no film_marks / watch_options yet (re-run supabase/schema.sql); skipped")
                return
            raise
        now = datetime.now(timezone.utc)
        states = states_from(rows)
        want = candidates(reg, marked)
        todo = due(want, states, now)
        out, found, dated = [], 0, 0
        for fid in todo:
            try:
                d = external.tmdb_get(f"/movie/{reg.films[fid]['tmdb']}", append_to_response="watch/providers,release_dates")
            except Exception as e:
                print(f"  ! where to watch {reg.films[fid].get('title')}: {e}", file=sys.stderr)
                continue
            providers = (d.get("watch/providers") or {}).get("results") or {}
            releases = (d.get("release_dates") or {}).get("results") or []
            any_found = any_dated = False
            for c in countries:
                opts, link = options_for(providers.get(c) or {})
                any_found |= bool(opts)
                dig = digital_date(releases, c, now.date().isoformat())
                if dig:
                    opts["digital"] = dig
                    any_dated |= dig > now.date().isoformat()
                out.append({"film_id": fid, "country": c, "options": opts, "link": link})
            found += any_found
            dated += any_dated and not any_found
        if out:
            filmids._request("rpc/sync_watch_options", {"p": out}, timeout=120)
        print(f"  where to watch: {len(want)} wanted films; checked {len(todo)} ({sum(1 for f in todo if f not in states)} new): "
              f"{found} with services, {dated} more with a digital release ahead")
    except Exception as e:
        print(f"  ! where to watch failed: {e}", file=sys.stderr)
