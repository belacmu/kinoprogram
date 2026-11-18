"""Everything TMDB knows about a film, in one request per film, kept on the film's card.

The update has three steps for what it knows about films (SPEC.md, "Film information"):
  1. Matching (external.enrich): which film a listing is (TMDB/IMDb ids, Rotten Tomatoes/Metacritic, English title), per listing.
  2. Details (here): per film, one TMDB request, /movie/{id} with credits, release dates and watch providers: English title (for
     films that have none from matching), poster, description, running time, director, original language, where to watch in
     every country and its digital release dates. Kept on the film's card (films.meta) and, for where to watch, watch_options.
  3. Letterboxd (external.fill_letterboxd / enrich's weekly ratings): the film's Letterboxd page and rating.

When a film is fetched (due()):
  - never fetched: now (recovered watchlist films and listed films first);
  - listed now: again after REFRESH_DAYS;
  - wanted (film_marks): as where to watch needs it (watch.next_check: every 14 days while nothing is found, the day after a
    digital release ahead, every 60 days once something is, never after a digital release long past with still nothing);
  - otherwise never again: the film keeps what it has.
At most BUDGET requests a run.

Usage from build.py:
    pending = details.update(reg, listed, legacy, countries, first=recovered)   # fetch what's due; cards updated; never raises
    details.apply(reg, films)                                         # each region's listed films take their card's details
    ... filmids.sync(...)
    details.flush(pending)                                            # where-to-watch rows, once the films are in the database
"""
import sys
import urllib.error
from datetime import datetime, timedelta, timezone

import external
import filmids
import watch

BUDGET = 250
REFRESH_DAYS = 30
POSTER = "https://image.tmdb.org/t/p/w342"
BLURB_MAX = filmids.BLURB_MAX
# Posters: TMDB's replaces a missing one and Cinemateket's / Revier's wide stills; for Westman and Costa del Sol, whose image links
# can go dead, it's the fallback (poster2) the page uses if the first image fails.
STILLS = ("vrs.gd", "evbuc.com")
FALLBACK_PREFIXES = ("https-moviescout", "lm-", "cc-", "cl-", "mk-")
LANG_PREFIXES = ("cl-", "mk-")  # Costa del Sol's listings


def from_tmdb(d, titles):
    """TMDB's /movie answer -> the card's details ("" / 0 for what it doesn't have)."""
    directors = [c.get("name") for c in (d.get("credits") or {}).get("crew") or [] if c.get("job") == "Director" and c.get("name")]
    return {"en": external.english_from_tmdb(d, titles) or "",
            "tmdbPoster": POSTER + d["poster_path"] if d.get("poster_path") else "",
            "blurb": (d.get("overview") or "").strip()[:BLURB_MAX], "runtime": d.get("runtime") or 0,
            "director": ", ".join(directors[:2]), "lang": d.get("original_language") or ""}


def store(film, got, today):
    """Put a fetch's details on the film's card. TMDB's description, poster and language replace what was there (TMDB is their
    source); the English title, running time and director only fill gaps (the listing's and matching's come first)."""
    m = dict(film.get("meta") or {})
    for k in ("blurb", "tmdbPoster", "lang"):
        if got.get(k):
            m[k] = got[k]
    for k in ("en", "runtime", "director"):
        if got.get(k) and not m.get(k):
            m[k] = got[k]
    if not m.get("poster") and got.get("tmdbPoster"):
        m["poster"] = got["tmdbPoster"]
    m["tmdbAt"] = today
    film["meta"] = m


def migrate(reg, legacy, today):
    """Once: details fetched before this step existed become the card's, so they aren't fetched again. `legacy` holds what the
    matching cache used to keep per listing ({listing id: {posterPath, overview, lang}}); cards filled by the old fill_cards
    (tmdbCard) already have theirs."""
    n = 0
    for fid, f in reg.films.items():
        m = f.get("meta") or {}
        if f.get("merged") or m.get("tmdbAt"):
            continue
        rec = next((legacy[a] for a, t in reg.aliases.items() if t == fid and a in legacy), None)
        if not rec and not m.get("tmdbCard"):
            continue
        m = dict(m)
        if rec:
            if rec.get("posterPath"):
                m["tmdbPoster"] = POSTER + rec["posterPath"]
            if rec.get("overview") and not m.get("blurb"):
                m["blurb"] = rec["overview"][:BLURB_MAX]
            if rec.get("lang") and not m.get("lang"):
                m["lang"] = rec["lang"]
        elif str(m.get("poster", "")).startswith(POSTER):
            m["tmdbPoster"] = m["poster"]
        m["tmdbAt"] = (rec or {}).get("tmdbChecked") or m.pop("tmdbCard", None) or today
        m.pop("tmdbCard", None)
        f["meta"] = m
        n += 1
    if n:
        print(f"  film details: {n} films' earlier details moved to their cards")


def due(reg, listed, marked, watch_states, now, first=(), budget=BUDGET):
    """The films to fetch this run, in order: never fetched (`first`, e.g. recovered films, then listed ones, then the rest), where to
    watch due, listed and stale."""
    today = now.date()
    never, wanted, stale = [], [], []
    for fid, f in sorted(reg.films.items()):
        if f.get("merged") or not f.get("tmdb"):
            continue
        at = (f.get("meta") or {}).get("tmdbAt")
        if not at:
            never.append((0 if fid in first else 1 if fid in listed else 2, fid))
            continue
        if fid in marked:
            st = watch_states.get(fid)
            t = watch.next_check(st, now) if st else now
            if t is not None and t <= now:
                wanted.append((t, fid))
                continue
        if fid in listed and at <= (today - timedelta(days=REFRESH_DAYS)).isoformat():
            stale.append((at, fid))
    order = [f for _, f in sorted(never)] + [f for _, f in sorted(wanted)] + [f for _, f in sorted(stale)]
    return order[:budget]


def update(reg, listed, legacy, countries, now=None, first=()):
    """Fetch what's due (cards updated in place) and return the where-to-watch rows to write after the sync. Never raises."""
    now = now or datetime.now(timezone.utc)
    today = now.date().isoformat()
    rows = []
    try:
        migrate(reg, legacy or {}, today)
        if not external.tmdb_enabled():
            return rows
        marked, watch_states = set(), {}
        if filmids._config()[0] and reg.source == "supabase":
            try:
                marked = {reg.canon(r["film_id"]) for r in filmids._rows("film_marks", "select=film_id")}
                watch_states = watch.states_from(filmids._rows("watch_options", "select=film_id,checked_at,options"))
            except urllib.error.HTTPError as e:
                if e.code not in (400, 404):
                    raise
        todo = due(reg, listed, marked, watch_states, now, first=set(first))
        fetched = found = 0
        for fid in todo:
            f = reg.films[fid]
            try:
                d = external.tmdb_get(f"/movie/{f['tmdb']}", language="en-US", append_to_response="credits,release_dates,watch/providers")
            except urllib.error.HTTPError as e:
                print(f"  ! film details {f.get('title')}: {e}", file=sys.stderr)
                if e.code in (401, 429):
                    break
                continue
            except Exception as e:
                print(f"  ! film details {f.get('title')}: {e}", file=sys.stderr)
                continue
            store(f, from_tmdb(d, [f.get("title") or ""]), today)
            reg.touched.add(fid)
            fetched += 1
            providers = (d.get("watch/providers") or {}).get("results") or {}
            releases = (d.get("release_dates") or {}).get("results") or []
            any_found = False
            for c in countries:
                opts, link = watch.options_for(providers.get(c) or {})
                any_found |= bool(opts)
                dig = watch.digital_date(releases, c, today)
                if dig:
                    opts["digital"] = dig
                rows.append({"film_id": fid, "country": c, "options": opts, "link": link})
            found += any_found
        if todo or fetched:
            print(f"  film details: {fetched} of {len(todo)} due fetched from TMDB ({found} with somewhere to watch)")
    except Exception as e:
        print(f"  ! film details failed: {e}", file=sys.stderr)
    return rows


def apply(reg, films):
    """A region's listed films take their details from their cards: TMDB's poster where the cinema's is missing or a still (or as
    the fallback), TMDB's description, and (Costa del Sol, where it tells a Spanish film from a dubbed one) the original language if
    matching didn't give it. Only films matched on TMDB get TMDB's: the film has a TMDB id, and its listing a match (a conflicting
    match has no ext). Whether TMDB's poster is the fallback goes by the listing's own id (f["listing"],
    kept by build.py before film ids are worked out), as it always has: a film's id may come from another region's listing."""
    for f in films:
        src = f.pop("listing", None) or f.get("id") or ""
        ext = f.get("ext") or {}
        film = reg.films.get(reg.canon(f.get("id") or "")) or {}
        card = film.get("meta") or {}
        if not ext or not (ext.get("tmdb") or film.get("tmdb")):
            f["blurb"] = ""
            continue
        f["blurb"] = card.get("blurb") or ""
        if card.get("lang") and not ext.get("lang") and src.startswith(LANG_PREFIXES):
            ext["lang"] = card["lang"]
        poster, tp = f.get("poster") or "", card.get("tmdbPoster") or ""
        if not tp:
            continue
        if not poster or any(s in poster for s in STILLS):
            f["poster"] = tp
        elif src.startswith(FALLBACK_PREFIXES):
            f["poster2"] = tp


def flush(rows):
    """Write the where-to-watch rows (after the sync, so films created this run exist). Never raises."""
    if not rows or not filmids._config()[0]:
        return
    try:
        filmids._request("rpc/sync_watch_options", {"p": rows}, timeout=120)
    except urllib.error.HTTPError as e:
        print(f"  ! where to watch: writing failed ({e.code})", file=sys.stderr)
    except Exception as e:
        print(f"  ! where to watch: writing failed: {e}", file=sys.stderr)
