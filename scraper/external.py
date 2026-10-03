"""Find each film's IMDb / Letterboxd / Rotten Tomatoes / Metacritic pages (via Wikidata, plus TMDB
when TMDB_READ_TOKEN or TMDB_API_KEY is set) and its Letterboxd average rating. Results are cached in state/external.json so each film is looked up
once, not on every run.

Matching is deliberately conservative: a Wikidata item must have an IMDb id, must not be a TV
series/book/etc., its release year must be within ±1 of ours (the Norwegian premiere year when
Filmweb has no production year; no year at all means no match), and its running time must be
within 10 minutes of ours when both are known. Ties between same-title candidates go to the
single one with a confirmed running time, else the single one first released in our year. If more than one item fits and
none has exactly our title, we link nothing (the site then shows a Letterboxd search link).
"""
import difflib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "state" / "external.json"
UA = {"User-Agent": "kinoprogram/1.0 (https://github.com/belacmu/kinoprogram; personal non-commercial use)"}
BROWSER_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                            "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"}
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


def _norm(s):
    return re.sub(r"[^0-9a-zæøåäöüéè]+", " ", (s or "").lower()).strip()


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
    q = re.sub(r"\s*\(\d{4}\)\s*$", "", title)
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
TMDB_VERSION = 2  # bump to re-check every film on TMDB after a rule change


def tmdb_enabled():
    return bool(os.environ.get("TMDB_READ_TOKEN") or os.environ.get("TMDB_API_KEY"))


def tmdb_get(path, **params):
    headers = {**UA, "Accept": "application/json"}
    if os.environ.get("TMDB_READ_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["TMDB_READ_TOKEN"]
    else:
        params["api_key"] = os.environ["TMDB_API_KEY"]
    return _json(f"{TMDB}{path}?{urllib.parse.urlencode(params)}", headers)


def tmdb(f):
    """Return the one confident TMDB match for a film, or None. Same rules as wikidata(), plus a
    tie-break on the Norwegian release date in TMDB equalling Filmweb's premiere date."""
    year = film_year(f)
    if not year:
        return None
    titles = [t for t in (f["alt"], f["title"]) if t]
    ours = {_norm(re.sub(r"\s*\(\d{4}\)\s*$", "", t)) for t in titles}
    cands = {}
    for t in titles:
        for lang in ("nb-NO", "en-US"):
            for m in tmdb_get("/search/movie", query=t, language=lang, include_adult="false").get("results", [])[:20]:
                y = (m.get("release_date") or "")[:4]
                if y.isdigit() and abs(int(y) - int(year)) <= 1:
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
        title, orig = d.get("title") or "", d.get("original_title") or ""
        # TMDB falls back to the original title when there's no English one; don't call that English.
        en = title if d.get("original_language") == "en" or _norm(title) != _norm(orig) else None
        if en and d.get("original_language") != "en" and any(spelling_variant(en, t) for t in titles):
            en = None  # "Matloob Aaeleyan" for "Matloob Aelian" is a transliteration, not a translation
        hits.append({"tmdb": mid, "imdb": (d.get("external_ids") or {}).get("imdb_id") or None, "en": en,
                     "lang": d.get("original_language") or None,
                     "premiereOk": bool(f.get("premiere") and f["premiere"] in no_dates),
                     "runtimeOk": bool(f["runtime"] and rt),
                     "yearExact": (d.get("release_date") or "")[:4] == str(year)})
    for k in ("premiereOk", "runtimeOk", "yearExact"):
        if len(hits) > 1 and sum(h[k] for h in hits) == 1:
            hits = [h for h in hits if h[k]]
    if len(hits) != 1:
        return None
    return {k: hits[0][k] for k in ("tmdb", "imdb", "en", "lang")}


def letterboxd(rec):
    """Fetch the Letterboxd page; return (slug, average rating out of 5 or None)."""
    # Always go via the IMDb/TMDB id: Letterboxd resolves them itself, whereas Wikidata's stored
    # Letterboxd slug can be out of date (e.g. a film's working title).
    url = (f"https://letterboxd.com/imdb/{rec['imdb']}/" if rec.get("imdb")
           else f"https://letterboxd.com/tmdb/{rec['tmdb']}/")
    req = urllib.request.Request(url, headers=BROWSER_UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        final, page = r.geturl(), r.read().decode("utf-8", "replace")
    slug = (re.search(r"letterboxd\.com/film/([^/]+)/", final) or [None, None])[1]
    m = re.search(r'name="twitter:data2" content="([\d.]+) out of 5"', page)
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
            if old.get("tv") == TMDB_VERSION and \
                    not stale(old.get("tmdbChecked"), RECHECK_MATCHED_DAYS if old.get("tmdb") else RECHECK_MISSING_DAYS):
                continue
            try:
                t = tmdb(f)
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

    # Original language (TMDB) for Costa del Sol films: it tells a Spanish-language film from a dubbed one.
    if tmdb_enabled():
        for f in [f for f in films if f["id"].startswith("cl-")][:40]:
            rec = next((cache[i] for i in f["ids"] if i in cache), None)
            if rec and rec.get("tmdb") and not rec.get("lang") and not rec.get("conflict"):
                try:
                    rec["lang"] = tmdb_get(f"/movie/{rec['tmdb']}", language="en-US").get("original_language") or ""
                except Exception as e:
                    print(f"  ! tmdb language {f['title']}: {e}", file=sys.stderr)

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
    # Posters from TMDB where the film is matched there: instead of Cinemateket's and Revier's wide stills and of missing posters, and as a
    # fallback (`poster2`, used by the page if the first image fails to load) for Westman and Costa del Sol, whose image
    # links can go dead (e.g. MovieScout's Avengers: Endgame Encore).
    if tmdb_enabled():
        budget = 150   # lookups per run; the Oslo stills come first, then Westman and Costa del Sol
        for f in films:
            poster = f.get("poster") or ""
            replace = not poster or "vrs.gd" in poster or "evbuc.com" in poster
            fallback = not replace and f["id"].startswith(("https-moviescout", "lm-", "cc-", "cl-"))
            if not (replace or fallback):
                continue
            rec = next((cache[i] for i in f["ids"] if i in cache), None)
            if not rec or not rec.get("tmdb") or rec.get("conflict"):
                continue
            if "posterPath" not in rec and budget > 0:
                budget -= 1
                try:
                    rec["posterPath"] = tmdb_get(f"/movie/{rec['tmdb']}", language="en-US").get("poster_path") or ""
                except Exception as e:
                    print(f"  ! tmdb poster {f['title']}: {e}", file=sys.stderr)
            if rec.get("posterPath"):
                url = "https://image.tmdb.org/t/p/w342" + rec["posterPath"]
                if replace:
                    f["poster"] = url
                else:
                    f["poster2"] = url

    # Keep the cache to films we still list.
    live = {i for f in films for i in f["ids"]}
    # (ids of regions not built in this run, `keep_prefixes`, are left alone.)
    CACHE.write_text(json.dumps({k: v for k, v in sorted(cache.items()) if k in live or k.startswith(tuple(keep_prefixes))},
                                ensure_ascii=False, indent=0))
    print(f"  links for {matched}/{len(films)} films ({looked} looked up on Wikidata, "
          f"{tmdb_checked} checked on TMDB ({tmdb_new} new), {min(len(rate), MAX_RATINGS)} ratings refreshed)")
