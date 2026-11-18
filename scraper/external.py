"""Matching (step 1 of the film information; details.py is step 2): which film each listing is.

Find each film's IMDb / Letterboxd / Rotten Tomatoes / Metacritic pages (via Wikidata, plus TMDB
when TMDB_READ_TOKEN or TMDB_API_KEY is set) and its Letterboxd average rating. Results are cached in state/external.json so each film is looked up
once, not on every run.

Matching is deliberately conservative: a Wikidata item must have an IMDb id, must not be a TV
series/book/etc., its release year must be within ±1 of ours (the Norwegian premiere year when
Filmweb has no production year), and its running time must be within 10 minutes of ours when both are
known. A film with no year at all gets no Wikidata match; on TMDB it matches only the single film with exactly
its title, and only if that film is well known or new (see NO_YEAR_*). When Wikidata gives an IMDb id but TMDB's
title search finds nothing, TMDB is asked for that IMDb id directly. A listing with a wrong or missing year (a
re-release listed with its re-release year) can still match the single film of any year with exactly its title,
one of its directors and a running time within 10 minutes. Titles are compared with accents folded and
listing suffixes ("- Tamil", "- Filmklubben") removed. Ties between same-title candidates go to the
single one with a confirmed running time, else the single one first released in our year. If more than one item fits and
none has exactly our title, we link nothing (the site then shows a Letterboxd search link).
"""
import difflib
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "state" / "external.json"
UA = {"User-Agent": "kinoprogram/1.0 (https://github.com/belacmu/kinoprogram; personal non-commercial use)"}
RECHECK_MATCHED_DAYS = 90
RECHECK_MISSING_DAYS = 7    # new releases often get a Wikidata entry a little later
RATING_DAYS = 7
RUNTIME_SLACK = 10          # minutes
OTHER_LANGS = "en|nb|no|sv|da|de|fr|es|it|pt|nl|fi|pl"
ENGLISH = "Q1860"
MAX_LOOKUPS = 60            # per run; Wikidata rate-limits shared GitHub servers, so go slowly
LOOKUP_PAUSE = 1.0          # seconds between Wikidata requests
MAX_RATINGS = 120

# Programme items that aren't single films: don't bother looking them up.
SKIP = re.compile(r"\b(opera|ballet|rbo:|met opera|filmhistorie|kortfilm|shorts?\b|trilogi|maraton|konsert|"
                  r"concert|mystery screening|mystery movie|quiz|foredrag|samtale|kortfilmer)\b|\|", re.I)
NOT_FILM = {"Q5398426", "Q21191270", "Q1259759", "Q526877", "Q7725634", "Q482994", "Q134556",
            "Q7889", "Q15416", "Q3464665", "Q63952888", "Q117467246"}  # series, episode, book, album, game, …


def _json(url, headers=UA):
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as r:
        return json.load(r)


FOLD = str.maketrans({"ı": "i", "ø": "o", "æ": "ae", "ß": "ss", "đ": "d", "ł": "l", "œ": "oe", "þ": "th"})


def _norm(s):
    """A title for comparing: lower case, accents and special letters folded (Arkadaşım = Arkadasim, Ørnen = Ornen),
    punctuation as spaces."""
    s = unicodedata.normalize("NFKD", (s or "").lower().translate(FOLD))
    return re.sub(r"[^0-9a-z]+", " ", "".join(c for c in s if not unicodedata.combining(c))).strip()


# Listing suffixes that aren't part of the film's title: "Scene - Tamil", "Om - Tamil Film", "Sorcerer - Filmklubben".
LISTING_SUFFIX = re.compile(r"\s+[-–]\s+(?:(?:Tamil|Telugu|Malayalam|Hindi|Kannada|Punjabi|Bengali|Marathi|Gujarati|Urdu)"
                            r"(?:\s+(?:Film|Movie|Version))?|Filmklubben)\s*$", re.I)


def search_title(t):
    """The title to look a film up by: without a trailing "(1977)" or a listing suffix."""
    return LISTING_SUFFIX.sub("", re.sub(r"\s*\(\d{4}\)\s*$", "", t or "")).strip()


LANG_CODES = {"Q9027": "sv", "Q9035": "da", "Q188": "de", "Q150": "fr", "Q1321": "es", "Q652": "it",
              "Q5146": "pt", "Q7411": "nl", "Q1412": "fi", "Q809": "pl", "Q9043": "nb", "Q25167": "nb"}


def minutes(q):
    """Wikidata duration quantity -> minutes."""
    try:
        amount = float(q["amount"])
    except (KeyError, ValueError):
        return 0
    unit = q.get("unit", "")
    if unit.endswith("/Q25235"):   # hour
        amount *= 60
    elif unit.endswith("/Q11574"):  # second
        amount /= 60
    elif not unit.endswith("/Q7727"):  # not minutes
        return 0
    return round(amount)


def spelling_variant(a, b):
    """True when two titles are just different spellings/transliterations of each other."""
    return difflib.SequenceMatcher(None, _norm(a), _norm(b)).ratio() >= 0.8


def english_title(labels, original_langs, original_titles):
    """The English label, unless it's just the untranslated original title of a non-English film."""
    en = labels.get("en", {}).get("value")
    if not en or ENGLISH in original_langs:
        return en
    originals = {_norm(t) for t in original_titles}
    originals |= {_norm(labels[c]["value"]) for q in original_langs if (c := LANG_CODES.get(q)) and c in labels}
    if _norm(en) in originals or any(spelling_variant(en, o) for o in originals):
        return None
    return en


def wikidata(title, year, runtime=0):
    """Return the one confident match for (title, year), or None. Year is required."""
    if not year:
        return None
    q = search_title(title)
    found = _json("https://www.wikidata.org/w/api.php?" + urllib.parse.urlencode({
        "action": "wbsearchentities", "search": q, "language": "en", "uselang": "en",
        "type": "item", "limit": 10, "format": "json"}))
    ids = [x["id"] for x in found.get("search", [])]
    # Label search ranks by popularity, so a new film can lose to an old one with the same title
    # (Resident Evil 2026 vs 2002). Also run a full-text search for title + year (descriptions read
    # "2026 film directed by …"), limited to items with an IMDb id.
    time.sleep(LOOKUP_PAUSE)
    full = _json("https://www.wikidata.org/w/api.php?" + urllib.parse.urlencode({
        "action": "query", "list": "search", "srsearch": f'"{q}" {year} haswbstatement:P345',
        "srlimit": 20, "format": "json"}))
    ids += [x["title"] for x in full.get("query", {}).get("search", []) if x["title"] not in ids]
    ids = ids[:50]  # wbgetentities limit
    if not ids:
        return None
    ents = _json("https://www.wikidata.org/w/api.php?" + urllib.parse.urlencode({
        "action": "wbgetentities", "ids": "|".join(ids), "props": "claims|labels|aliases",
        "languages": OTHER_LANGS, "format": "json"}))["entities"]
    hits = []
    for i in ids:
        claims = ents[i].get("claims", {})

        def vals(p):
            return [c["mainsnak"].get("datavalue", {}).get("value") for c in claims.get(p, [])]

        imdb_all = [v for v in vals("P345") if isinstance(v, str) and v.startswith("tt")]
        imdb = imdb_all[0] if imdb_all else None
        kinds = {v.get("id") for v in vals("P31") if isinstance(v, dict)}
        if not imdb or kinds & NOT_FILM:
            continue
        years = {v["time"][1:5] for v in vals("P577") if isinstance(v, dict) and "time" in v}
        if not any(y.isdigit() and abs(int(y) - int(year)) <= 1 for y in years):
            continue
        mins = [minutes(v) for v in vals("P2047") if isinstance(v, dict)]
        mins = [m for m in mins if m]
        if runtime and mins and not any(abs(m - runtime) <= RUNTIME_SLACK for m in mins):
            continue  # same title and year but a different film (or a very different cut)
        first_year = min((y for y in years if y.isdigit()), default="")
        labels = ents[i].get("labels", {})
        names = {_norm(l["value"]) for l in labels.values()}
        names |= {_norm(a["value"]) for al in ents[i].get("aliases", {}).values() for a in al}
        hits.append({"imdb": imdb, "imdbAll": imdb_all,
                     "lb": next(iter(vals("P6127")), None),
                     "rt": next(iter(vals("P1258")), None),
                     "mc": next(iter(vals("P1712")), None),
                     "en": english_title(labels, {v.get("id") for v in vals("P364") if isinstance(v, dict)},
                                         [v["text"] for v in vals("P1476") if isinstance(v, dict) and v.get("language") != "en"]),
                     "v": 4,
                     "exact": _norm(q) in names,
                     "runtimeOk": bool(runtime and mins),          # runtime known on both sides and matching
                     "yearExact": first_year == str(year)})
    if len(hits) > 1:
        hits = [h for h in hits if h["exact"]]
    # Ties between same-title films (Taxi Driver 1976 vs a 1977 Malayalam film): take the single
    # candidate whose running time is confirmed, else the single one first released in our year.
    for key in ("runtimeOk", "yearExact"):
        if len(hits) > 1 and sum(h[key] for h in hits) == 1:
            hits = [h for h in hits if h[key]]
    if len(hits) != 1:
        return None
    h = hits[0]
    for k in ("exact", "runtimeOk", "yearExact"):
        h.pop(k)
    return h


# ---------------------------------------------------------------- TMDB (optional, needs a key)

TMDB = "https://api.themoviedb.org/3"
TMDB_VERSION = 3  # bump to re-check films on TMDB after a rule change (3: title folding, suffixes, no-year, IMDb, director)
# A film with no year matches only a TMDB film that is the single one with exactly its title, and is either well known
# or new (released within this many days, or coming): a new film named like an old one must not get the old one's page.
NO_YEAR_MIN_VOTES = 1000
NO_YEAR_RECENT_DAYS = 365


def tmdb_enabled():
    return bool(os.environ.get("TMDB_READ_TOKEN") or os.environ.get("TMDB_API_KEY"))


def tmdb_get(path, **params):
    headers = {**UA, "Accept": "application/json"}
    if os.environ.get("TMDB_READ_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["TMDB_READ_TOKEN"]
    else:
        params["api_key"] = os.environ["TMDB_API_KEY"]
    return _json(f"{TMDB}{path}?{urllib.parse.urlencode(params)}", headers)


def english_from_tmdb(d, titles):
    """TMDB's English title, unless it's only the original title or a transliteration of ours."""
    title, orig = d.get("title") or "", d.get("original_title") or ""
    # TMDB falls back to the original title when there's no English one; don't call that English.
    en = title if d.get("original_language") == "en" or _norm(title) != _norm(orig) else None
    if en and d.get("original_language") != "en" and any(spelling_variant(en, t) for t in titles):
        en = None  # "Matloob Aaeleyan" for "Matloob Aelian" is a transliteration, not a translation
    return en


def tmdb(f, today=None):
    """Return the one confident TMDB match for a film, or None. Same rules as wikidata(), plus a
    tie-break on the Norwegian release date in TMDB equalling Filmweb's premiere date. Without a year,
    only a single exact-title film that is well known or new (NO_YEAR_*)."""
    year = film_year(f)
    titles = [search_title(t) for t in (f["alt"], f["title"]) if t]
    ours = {_norm(t) for t in titles}
    langs = ("nb-NO", "en-US") + (("es-ES",) if f["id"].startswith(("cl-", "mk-")) else ())
    cands = {}
    for t in titles:
        for lang in langs:
            for m in tmdb_get("/search/movie", query=t, language=lang, include_adult="false").get("results", [])[:20]:
                y = (m.get("release_date") or "")[:4]
                if year and not (y.isdigit() and abs(int(y) - int(year)) <= 1):
                    continue
                if not year and not ({_norm(m.get("title")), _norm(m.get("original_title"))} & ours):
                    continue  # no year to narrow by: only candidates whose own title is ours
                cands.setdefault(m["id"], m)
    hits = []
    for mid in list(cands)[:8]:
        d = tmdb_get(f"/movie/{mid}", language="en-US",
                     append_to_response="external_ids,translations,release_dates,alternative_titles")
        names = {_norm(d.get("title")), _norm(d.get("original_title"))}
        names |= {_norm(t["data"].get("title")) for t in d.get("translations", {}).get("translations", []) if t["data"].get("title")}
        names |= {_norm(a.get("title")) for a in d.get("alternative_titles", {}).get("titles", [])}
        if not ours & names:
            continue  # TMDB search is fuzzy; we need an exact title in some language
        rt = d.get("runtime") or 0
        if f["runtime"] and rt and abs(rt - f["runtime"]) > RUNTIME_SLACK:
            continue
        no_dates = {x["release_date"][:10] for c in d.get("release_dates", {}).get("results", [])
                    if c.get("iso_3166_1") == "NO" for x in c.get("release_dates", [])}
        hits.append({"tmdb": mid, "imdb": (d.get("external_ids") or {}).get("imdb_id") or None,
                     "en": english_from_tmdb(d, titles),
                     "lang": d.get("original_language") or None,
                     "premiereOk": bool(f.get("premiere") and f["premiere"] in no_dates),
                     "runtimeOk": bool(f["runtime"] and rt),
                     "yearExact": bool(year) and (d.get("release_date") or "")[:4] == str(year),
                     "votes": d.get("vote_count") or 0, "released": d.get("release_date") or ""})
    if not year:
        h = hits[0] if len(hits) == 1 else None  # two films with this title and no year to tell them apart: none
        recent = (today or datetime.now()) - timedelta(days=NO_YEAR_RECENT_DAYS)
        if h and not (h["votes"] >= NO_YEAR_MIN_VOTES or not h["released"] or h["released"] >= recent.strftime("%Y-%m-%d")):
            h = None  # an older, little-known film: ours could be a new one TMDB doesn't have yet
    else:
        for k in ("premiereOk", "runtimeOk", "yearExact"):
            if len(hits) > 1 and sum(h[k] for h in hits) == 1:
                hits = [h for h in hits if h[k]]
        h = hits[0] if len(hits) == 1 else None
    if h:
        return {k: h[k] for k in ("tmdb", "imdb", "en", "lang")}
    # No match by year (it may be a re-release year, or missing): the director can still pin the film down.
    return tmdb_by_director(f, titles, langs)


def directors(text):
    """Director names from a listing: "Vittorio De Sica", "Joel Coen, Ethan Coen", "Joel y Ethan Coen" -> name sets."""
    parts = re.split(r"\s*(?:,|;|/|&|\band\b|\bog\b|\by\b)\s*", text or "")
    return [set(_norm(p).split()) for p in parts if _norm(p)]


def same_director(ours, theirs):
    """True when one of our directors is one of TMDB's: every word of our name is in theirs (middle names may be missing)."""
    return any(o and (o <= t or t <= o) and len(o & t) >= 2 or (o == t and o) for o in ours for t in theirs)


def tmdb_by_director(f, titles, langs):
    """For a listing whose year is wrong or missing (a re-release listed with its re-release year): the single TMDB
    film, of any year, with exactly our title, one of our directors, and a running time within RUNTIME_SLACK."""
    ours_dir = directors(f.get("director"))
    if not ours_dir:
        return None
    ours = {_norm(t) for t in titles}
    cands = {}
    for t in titles:
        for lang in langs:
            for m in tmdb_get("/search/movie", query=t, language=lang, include_adult="false").get("results", [])[:20]:
                if {_norm(m.get("title")), _norm(m.get("original_title"))} & ours:
                    cands.setdefault(m["id"], m)
    hits = []
    for mid in list(cands)[:8]:
        d = tmdb_get(f"/movie/{mid}", language="en-US", append_to_response="external_ids,credits")
        rt = d.get("runtime") or 0
        if f["runtime"] and rt and abs(rt - f["runtime"]) > RUNTIME_SLACK:
            continue
        theirs = [set(_norm(c.get("name")).split()) for c in (d.get("credits") or {}).get("crew", []) if c.get("job") == "Director"]
        if same_director(ours_dir, theirs):
            hits.append({"tmdb": mid, "imdb": (d.get("external_ids") or {}).get("imdb_id") or None,
                         "en": english_from_tmdb(d, titles), "lang": d.get("original_language") or None})
    return hits[0] if len(hits) == 1 else None  # a director's two films of the same title: can't tell, no match


def tmdb_by_imdb(imdb, f):
    """The TMDB film for an IMDb id that Wikidata matched (an exact lookup, no title guessing), or None."""
    found = tmdb_get(f"/find/{imdb}", external_source="imdb_id", language="en-US").get("movie_results") or []
    if len(found) != 1:
        return None
    d = found[0]
    titles = [search_title(t) for t in (f["alt"], f["title"]) if t]
    return {"tmdb": d["id"], "imdb": imdb, "en": english_from_tmdb(d, titles), "lang": d.get("original_language") or None}


def letterboxd(rec):
    """Fetch the Letterboxd page; return (slug, average rating out of 5 or None)."""
    # Always go via the IMDb/TMDB id: Letterboxd resolves them itself, whereas Wikidata's stored
    # Letterboxd slug can be out of date (e.g. a film's working title).
    url = (f"https://letterboxd.com/imdb/{rec['imdb']}/" if rec.get("imdb")
           else f"https://letterboxd.com/tmdb/{rec['tmdb']}/")
    # Letterboxd allows showing its ratings and puts them in each film page's JSON-LD for that:
    # https://support.letterboxd.com/hc/en-us/articles/17815623056911
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        final, page = r.geturl(), r.read().decode("utf-8", "replace")
    slug = (re.search(r"letterboxd\.com/film/([^/]+)/", final) or [None, None])[1]
    m = (re.search(r'"aggregateRating"\s*:\s*\{[^}]*"ratingValue"\s*:\s*([\d.]+)', page)
         or re.search(r'name="twitter:data2" content="([\d.]+) out of 5"', page))
    return slug, (round(float(m[1]), 2) if m else None)


def film_year(f):
    """Production year, or for new releases without one, the year of the Norwegian premiere."""
    return f["year"] or (f["premiere"][:4] if f.get("premiere") else "")


def enrich(films, now, keep_prefixes=()):
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    today = now.strftime("%Y-%m-%d")

    def stale(date, days):
        return not date or date < (now - timedelta(days=days)).strftime("%Y-%m-%d")

    # Films whose source gives exact ids (MovieScout): use them, no title matching needed.
    for f in films:
        known = f.get("knownIds") or {}
        if not (known.get("imdb") or known.get("tmdb")):
            continue
        old = next((cache[i] for i in f["ids"] if i in cache), {})
        same = old.get("imdb") and old.get("imdb") == known.get("imdb")
        rec = {**(old if same else {}), "imdb": known.get("imdb") or None, "tmdb": known.get("tmdb") or old.get("tmdb"),
               "imdbAll": [known["imdb"]] if known.get("imdb") else [], "v": 4, "checked": today,
               "tmdbChecked": today, "tv": TMDB_VERSION, "source": "moviescout"}
        rec.pop("conflict", None)
        for i in f["ids"]:
            cache[i] = rec

    todo = []
    for f in films:
        rec = next((cache[i] for i in f["ids"] if i in cache), None)
        if SKIP.search(f["title"]):
            continue
        if rec is None or rec.get("conflict") or rec.get("v") not in (3, 4) or (not rec.get("imdb") and rec.get("v") != 4) \
                or stale(rec.get("checked"), RECHECK_MATCHED_DAYS if rec.get("imdb") else RECHECK_MISSING_DAYS):
            todo.append(f)
    todo.sort(key=lambda f: f["status"] != "on_sale")  # what you can book now first
    todo = todo[:MAX_LOOKUPS]

    def look(f):
        for t in [f["alt"], f["title"]]:
            if t:
                time.sleep(LOOKUP_PAUSE)
                hit = wikidata(t, film_year(f), f["runtime"])
                if hit:
                    return hit
        return {}

    looked = 0
    for f in todo:
        try:
            hit = look(f)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                print("  ! Wikidata asked us to slow down; continuing next run", file=sys.stderr)
                break
            print(f"  ! wikidata {f['title']}: {e}", file=sys.stderr)
            continue
        except Exception as e:
            print(f"  ! wikidata {f['title']}: {e}", file=sys.stderr)
            continue
        looked += 1
        old = next((cache[i] for i in f["ids"] if i in cache), {})
        if hit and not old.get("conflict") and old.get("imdb") in hit["imdbAll"]:
            # The film we already had: keep what TMDB and Letterboxd added (TMDB's IMDb id wins for duplicates).
            rec = {**old, **hit, "imdb": old["imdb"], "en": hit["en"] or old.get("en")}
            if hit["en"]:
                rec.pop("enFrom", None)
        elif hit:
            rec = dict(hit)  # a different film than before: TMDB checks it again (no "tv")
        elif old.get("tmdb"):
            # Not on Wikidata but matched on TMDB: keep that match, without anything Wikidata had said.
            rec = {k: v for k, v in old.items() if k not in ("imdbAll", "lb", "rt", "mc", "conflict")}
        else:
            rec = {k: old[k] for k in ("tmdbChecked", "tv") if k in old}
        # "v" on misses too, or they'd be looked up again every run and use up MAX_LOOKUPS.
        rec.update(v=4, checked=today)
        for i in f["ids"]:
            cache[i] = rec

    # TMDB pass (when a key is configured): fills films Wikidata doesn't know yet, and
    # cross-checks Wikidata's matches. If the two disagree, the film gets no direct links.
    tmdb_new = tmdb_checked = 0
    if tmdb_enabled():
        for f in films:
            if SKIP.search(f["title"]):
                continue
            old = next((cache[i] for i in f["ids"] if i in cache), {})
            # A film matched under the old rules keeps its match until its normal re-check; one not matched is tried again.
            if (old.get("tv") == TMDB_VERSION or (old.get("tmdb") and old.get("tv") == 2)) and \
                    not stale(old.get("tmdbChecked"), RECHECK_MATCHED_DAYS if old.get("tmdb") else RECHECK_MISSING_DAYS):
                continue
            try:
                t = tmdb(f, now)
                if not t and old.get("imdb") and not old.get("conflict"):
                    t = tmdb_by_imdb(old["imdb"], f)
            except urllib.error.HTTPError as e:
                print(f"  ! tmdb {f['title']}: {e}", file=sys.stderr)
                if e.code in (401, 429):
                    break
                continue
            except Exception as e:
                print(f"  ! tmdb {f['title']}: {e}", file=sys.stderr)
                continue
            tmdb_checked += 1
            rec = {**old, "tmdbChecked": today, "tv": TMDB_VERSION}
            rec.pop("conflict", None)
            if t:
                same = not old.get("imdb") or not t["imdb"] or t["imdb"] in (old.get("imdbAll") or [old["imdb"]])
                if not same:
                    print(f"  ? conflict, no links: {f['title']} ({film_year(f)}): Wikidata {old['imdb']} vs TMDB {t['imdb']}")
                    rec["conflict"] = [old["imdb"], t["imdb"]]
                else:
                    if not old.get("imdb"):
                        tmdb_new += 1
                        print(f"  + TMDB: {f['title']} ({film_year(f)}) -> {t['imdb'] or 'tmdb ' + str(t['tmdb'])}"
                              + (f" \"{t['en']}\"" if t["en"] and _norm(t["en"]) != _norm(f["title"]) else ""))
                        for k in ("lbSlug", "lbRating", "rated"):
                            rec.pop(k, None)
                    rec["tmdb"] = t["tmdb"]
                    rec["lang"] = t.get("lang") or rec.get("lang")
                    # IMDb sometimes has duplicate ids for one film; prefer TMDB's (Letterboxd uses it).
                    rec["imdb"] = t["imdb"] or rec.get("imdb")
                    rec["en"] = (rec.get("en") if rec.get("enFrom") != "tmdb" else None) or t["en"]
                    if rec["en"] and rec["en"] == t["en"]:
                        rec["enFrom"] = "tmdb"
            for i in f["ids"]:
                cache[i] = rec

    # Letterboxd ratings, refreshed weekly for films we've matched.
    rate = []
    for f in films:
        rec = next((cache[i] for i in f["ids"] if i in cache), None)
        if rec and (rec.get("imdb") or rec.get("tmdb")) and not rec.get("conflict") \
                and stale(rec.get("rated"), RATING_DAYS) and rec not in rate:
            rate.append(rec)
    blocked = False
    for rec in rate[:MAX_RATINGS]:
        if blocked:
            break
        try:
            rec["lbSlug"], rec["lbRating"] = letterboxd(rec)
            rec["rated"] = today
        except urllib.error.HTTPError as e:
            if e.code in (403, 429):
                print(f"  ! Letterboxd refused ({e.code}); skipping ratings this run", file=sys.stderr)
                blocked = True
            elif e.code == 404:
                rec["rated"] = today
        except Exception as e:
            print(f"  ! letterboxd {rec.get('imdb')}: {e}", file=sys.stderr)

    matched = 0
    for f in films:
        rec = next((cache[i] for i in f["ids"] if i in cache), None)
        if rec:
            for i in f["ids"]:
                cache[i] = rec
        if rec and (rec.get("imdb") or rec.get("tmdb")) and not rec.get("conflict"):
            matched += 1
            f["ext"] = {k: rec.get(k) for k in ("imdb", "tmdb", "rt", "mc", "lbRating", "lang") if rec.get(k)}
            en = rec.get("en") or ""
            if en and len(en) < 120 and _norm(en) not in (_norm(f["title"]), _norm(f["alt"])):
                f["ext"]["en"] = en
            f["ext"]["lb"] = rec.get("lbSlug") or ""  # only a slug Letterboxd itself returned
    # Details (poster, description, …) are the details step's now (details.py), kept on each film's card. What this cache held of
    # them goes to details.migrate once, through `legacy`, and is dropped here.
    legacy = {}
    for k, rec in cache.items():
        got = {x: rec.pop(x) for x in ("posterPath", "overview") if x in rec}
        if got:
            legacy[k] = {**got, "lang": rec.get("lang"), "tmdbChecked": rec.get("tmdbChecked")}

    # Keep the cache to films we still list.
    live = {i for f in films for i in f["ids"]}
    # (ids of regions not built in this run, `keep_prefixes`, are left alone.)
    CACHE.write_text(json.dumps({k: v for k, v in sorted(cache.items()) if k in live or k.startswith(tuple(keep_prefixes))},
                                ensure_ascii=False, indent=0))
    print(f"  links for {matched}/{len(films)} films ({looked} looked up on Wikidata, "
          f"{tmdb_checked} checked on TMDB ({tmdb_new} new), {min(len(rate), MAX_RATINGS)} ratings refreshed)")
    return legacy


# ---------------------------------------------------------------- watchlist films from before films were kept
RECOVER_BUDGET = 40  # films created per run (one or two TMDB requests each)


def recover_films(reg, ids, history, now_iso):
    """Watchlist ids no film is known by: films that left the cinemas before the film registry began (2026-11-14), so the database
    never had them. The listing's TMDB/IMDb match from git history (state/listing-history.json) identifies the film: an id of a film
    known under another id becomes its alias; otherwise the film is created under that id from TMDB (title and year here; its card is
    fetched by details.py). Ids with no match in the history stay unknown. Returns the ids of the films created."""
    unknown = sorted({i for i in ids if i and i not in reg.aliases and i not in reg.films})
    created, linked = [], 0
    for i in unknown:
        ext = history.get(i) or {}
        tmdb, imdb = ext.get("tmdb"), ext.get("imdb")
        if not (tmdb or imdb):
            continue
        known = reg.by_ext("tmdb", tmdb) or reg.by_ext("imdb", imdb)
        if known:  # e.g. the same film from another cinema, recovered just before
            reg.add_alias(i, known, "history")
            linked += 1
            continue
        if len(created) >= RECOVER_BUDGET or not tmdb_enabled():
            continue
        try:
            if not tmdb:
                tmdb = next(iter((tmdb_get(f"/find/{imdb}", external_source="imdb_id").get("movie_results") or [])), {}).get("id")
                if not tmdb:
                    continue
                known = reg.by_ext("tmdb", tmdb)
                if known:
                    reg.add_alias(i, known, "history")
                    linked += 1
                    continue
            d = tmdb_get(f"/movie/{tmdb}", language="en-US")
        except Exception as e:
            print(f"  ! recovering {i}: {e}", file=sys.stderr)
            continue
        title = (d.get("title") or d.get("original_title") or "").strip()
        if not title:
            continue
        reg.films[i] = {"title": title, "year": (d.get("release_date") or "")[:4], "tmdb": tmdb, "imdb": imdb or d.get("imdb_id"),
                        "merged": None, "first": now_iso, "meta": {}}
        reg.add_alias(i, i, "listing")
        reg.touched.add(i)
        created.append(i)
    left = sum(1 for i in unknown if i not in reg.aliases)
    if created or linked or left:
        print(f"  watchlist films from before the registry: {len(created)} recovered, {linked} linked to known films, {left} still unknown")
    return created


# ---------------------------------------------------------------- Letterboxd for films no longer listed
LB_CARD_BUDGET = 40  # Letterboxd pages per run


def fill_letterboxd(reg, listed):
    """Films not in this run's listings whose card has no Letterboxd slug or rating (those that left before cards kept them, and
    recovered watchlist films): look the film up on Letterboxd once by its exact IMDb/TMDB id, for its page and average rating. Stops
    for the run if Letterboxd refuses."""
    todo = [(fid, f) for fid, f in sorted(reg.films.items()) if fid not in listed and not f.get("merged")
            and (f.get("imdb") or f.get("tmdb")) and not (f.get("meta") or {}).get("lbChecked")
            and not ((f.get("meta") or {}).get("lb") and (f.get("meta") or {}).get("lbRating"))]
    done = 0
    for fid, f in todo[:LB_CARD_BUDGET]:
        m = dict(f.get("meta") or {})
        try:
            slug, rating = letterboxd({"imdb": f.get("imdb"), "tmdb": f.get("tmdb")})
            if slug:
                m["lb"] = slug
            if rating:
                m["lbRating"] = rating
        except urllib.error.HTTPError as e:
            if e.code in (403, 429):
                print(f"  ! Letterboxd refused ({e.code}); film cards' ratings skipped this run", file=sys.stderr)
                break
            if e.code != 404:
                continue
        except Exception as e:
            print(f"  ! letterboxd card {f.get('title')}: {e}", file=sys.stderr)
            continue
        m["lbChecked"] = datetime.now().strftime("%Y-%m-%d")  # once; a film listed again gets its rating from the listing
        f["meta"] = m
        done += 1
    if done:
        print(f"  film cards: {done} looked up on Letterboxd ({max(0, len(todo) - done)} to go)")
