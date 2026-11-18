"""Step 3 of what the update knows about films (SPEC.md, "Film information"): each film's Letterboxd page and average rating.

Per film, by its exact IMDb or TMDB id (Letterboxd resolves them itself: letterboxd.com/imdb/…, /tmdb/…), kept on the film's card
(lb: the slug Letterboxd returned, lbRating, lbAt: when it was last asked). Listed films then take theirs from their cards.

When a film is asked:
  - never asked: now (listed films first);
  - listed now: again after LISTED_DAYS;
  - otherwise: again after OTHER_DAYS (ratings of films no longer showing barely move).
At most BUDGET a run; if Letterboxd refuses (403, 429), no more this run. A film Letterboxd doesn't have (404) counts as asked.

Usage from build.py, after details.update and before the films are written:
    letterboxd.update(reg, listed, legacy)   # never raises
    letterboxd.apply(reg, films)                     # each region's listed films
"""
import sys
import urllib.error
from datetime import datetime, timedelta, timezone

import external

BUDGET = 120
LISTED_DAYS = 7
OTHER_DAYS = 90


def migrate(reg, legacy, today):
    """Once: ratings asked before this step existed become the card's. `legacy` holds what the matching cache kept per listing
    ({listing id: {lbSlug, lbRating, rated}}); cards filled by the old one-off lookup have lbChecked."""
    n = 0
    for fid, f in reg.films.items():
        m = f.get("meta") or {}
        if f.get("merged") or m.get("lbAt"):
            continue
        rec = next((legacy[a] for a, t in reg.aliases.items() if t == fid and a in legacy and legacy[a].get("rated")), None)
        if not rec and not m.get("lbChecked"):
            continue
        m = dict(m)
        if rec:
            if rec.get("lbSlug"):
                m["lb"] = rec["lbSlug"]
            if rec.get("lbRating"):
                m["lbRating"] = rec["lbRating"]
            m["lbAt"] = rec["rated"]
        else:
            m["lbAt"] = m["lbChecked"]
        m.pop("lbChecked", None)
        f["meta"] = m
        n += 1
    if n:
        print(f"  letterboxd: {n} films' earlier ratings moved to their cards")


def due(reg, listed, today, budget=BUDGET):
    """The films to ask about this run: never asked (listed first), then the longest ago among those due."""
    never, stale = [], []
    week = (today - timedelta(days=LISTED_DAYS)).isoformat()
    quarter = (today - timedelta(days=OTHER_DAYS)).isoformat()
    for fid, f in sorted(reg.films.items()):
        if f.get("merged") or not (f.get("imdb") or f.get("tmdb")):
            continue
        at = (f.get("meta") or {}).get("lbAt")
        if not at:
            never.append((fid not in listed, fid))
        elif at <= (week if fid in listed else quarter):
            stale.append((at, fid))
    return ([f for _, f in sorted(never)] + [f for _, f in sorted(stale)])[:budget]


def update(reg, listed, legacy, now=None):
    """Ask Letterboxd about the films that are due; cards updated in place. Never raises."""
    now = now or datetime.now(timezone.utc)
    today = now.date().isoformat()
    try:
        migrate(reg, legacy or {}, today)
        todo = due(reg, listed, now.date())
        asked = rated = 0
        for fid in todo:
            f = reg.films[fid]
            try:
                slug, rating = external.letterboxd({"imdb": f.get("imdb"), "tmdb": f.get("tmdb")})
            except urllib.error.HTTPError as e:
                if e.code in (403, 429):
                    print(f"  ! Letterboxd refused ({e.code}); no more ratings this run", file=sys.stderr)
                    break
                if e.code != 404:
                    print(f"  ! letterboxd {f.get('title')}: {e}", file=sys.stderr)
                    continue
                slug, rating = None, None  # not on Letterboxd: asked, nothing there
            except Exception as e:
                print(f"  ! letterboxd {f.get('title')}: {e}", file=sys.stderr)
                continue
            m = dict(f.get("meta") or {})
            if slug:
                m["lb"] = slug
            if rating:
                m["lbRating"] = rating
            m["lbAt"] = today
            f["meta"] = m
            reg.touched.add(fid)
            asked += 1
            rated += bool(rating)
        if todo:
            print(f"  letterboxd: {asked} of {len(todo)} due asked ({rated} with a rating)")
    except Exception as e:
        print(f"  ! letterboxd failed: {e}", file=sys.stderr)


def apply(reg, films):
    """A region's matched films (a conflicting match has no ext) take their Letterboxd page and rating from their cards."""
    for f in films:
        ext = f.get("ext")
        if not ext:
            continue
        m = (reg.films.get(reg.canon(f.get("id") or "")) or {}).get("meta") or {}
        ext["lb"] = m.get("lb") or ""  # only a slug Letterboxd itself returned
        if m.get("lbRating"):
            ext["lbRating"] = m["lbRating"]
