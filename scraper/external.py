"""Find each film's IMDb / Letterboxd / Rotten Tomatoes / Metacritic pages (via Wikidata) and its
Letterboxd average rating. Results are cached in state/external.json so each film is looked up
once, not on every run.

Matching is deliberately conservative: a Wikidata item must have an IMDb id, must not be a TV
series/book/etc., its release year must be within ±1 of ours (the Norwegian premiere year when
Filmweb has no production year; no year at all means no match), and its running time must be
within 10 minutes of ours when both are known. Ties between same-title candidates go to the
single one with a confirmed running time, else the single one first released in our year. If more than one item fits and
none has exactly our title, we link nothing (the site then shows a Letterboxd search link).
"""
import json
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
                  r"concert|mystery screening|quiz|foredrag|samtale|kortfilmer)\b|\|", re.I)
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


def english_title(labels, original_langs, original_titles):
    """The English label, unless it's just the untranslated original title of a non-English film."""
    en = labels.get("en", {}).get("value")
    if not en or ENGLISH in original_langs:
        return en
    originals = {_norm(t) for t in original_titles}
    originals |= {_norm(labels[c]["value"]) for q in original_langs if (c := LANG_CODES.get(q)) and c in labels}
    return None if _norm(en) in originals else en


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

        imdb = next((v for v in vals("P345") if isinstance(v, str) and v.startswith("tt")), None)
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
        hits.append({"imdb": imdb,
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


def letterboxd(rec):
    """Fetch the Letterboxd page; return (slug, average rating out of 5 or None)."""
    # Always go via the IMDb id: Letterboxd resolves it itself, whereas Wikidata's stored
    # Letterboxd slug can be out of date (e.g. a film's working title).
    url = f"https://letterboxd.com/imdb/{rec['imdb']}/"
    req = urllib.request.Request(url, headers=BROWSER_UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        final, page = r.geturl(), r.read().decode("utf-8", "replace")
    slug = (re.search(r"letterboxd\.com/film/([^/]+)/", final) or [None, None])[1]
    m = re.search(r'name="twitter:data2" content="([\d.]+) out of 5"', page)
    return slug, (round(float(m[1]), 2) if m else None)


def film_year(f):
    """Production year, or for new releases without one, the year of the Norwegian premiere."""
    return f["year"] or (f["premiere"][:4] if f.get("premiere") else "")


def enrich(films, now):
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    today = now.strftime("%Y-%m-%d")

    def stale(date, days):
        return not date or date < (now - timedelta(days=days)).strftime("%Y-%m-%d")

    todo = []
    for f in films:
        rec = next((cache[i] for i in f["ids"] if i in cache), None)
        if SKIP.search(f["title"]):
            continue
        if rec is None or rec.get("v") not in (3, 4) or (not rec.get("imdb") and rec.get("v") != 4) \
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
        keep = {k: old[k] for k in ("lbSlug", "lbRating", "rated") if k in old and old.get("imdb") == hit.get("imdb")}
        rec = {**keep, **hit, "checked": today}
        for i in f["ids"]:
            cache[i] = rec

    # Letterboxd ratings, refreshed weekly for films we've matched.
    rate = []
    for f in films:
        rec = next((cache[i] for i in f["ids"] if i in cache), None)
        if rec and rec.get("imdb") and stale(rec.get("rated"), RATING_DAYS) and rec not in rate:
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
        if rec and rec.get("imdb"):
            matched += 1
            f["ext"] = {k: rec.get(k) for k in ("imdb", "rt", "mc", "lbRating") if rec.get(k)}
            en = rec.get("en") or ""
            if en and _norm(en) not in (_norm(f["title"]), _norm(f["alt"])) and len(en) < 120:
                f["ext"]["en"] = en
            f["ext"]["lb"] = rec.get("lbSlug") or ""  # only a slug Letterboxd itself returned
    # Keep the cache to films we still list.
    live = {i for f in films for i in f["ids"]}
    CACHE.write_text(json.dumps({k: v for k, v in sorted(cache.items()) if k in live},
                                ensure_ascii=False, indent=0))
    print(f"  links for {matched}/{len(films)} films ({looked} looked up, {min(len(rate), MAX_RATINGS)} ratings refreshed)")
