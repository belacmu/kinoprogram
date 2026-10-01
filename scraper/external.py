"""Find each film's IMDb / Letterboxd / Rotten Tomatoes / Metacritic pages (via Wikidata) and its
Letterboxd average rating. Results are cached in state/external.json so each film is looked up
once, not on every run.

Matching is deliberately conservative: a Wikidata item must have an IMDb id, must not be a TV
series/book/etc., and its release year must be within ±1 of ours. If more than one item fits and
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


def english_title(labels, original_langs, original_titles):
    """The English label, unless it's just the untranslated original title of a non-English film."""
    en = labels.get("en", {}).get("value")
    if not en or ENGLISH in original_langs:
        return en
    originals = {_norm(t) for t in original_titles}
    originals |= {_norm(labels[c]["value"]) for q in original_langs if (c := LANG_CODES.get(q)) and c in labels}
    return None if _norm(en) in originals else en


def wikidata(title, year):
    """Return the one confident match for (title, year), or None."""
    q = re.sub(r"\s*\(\d{4}\)\s*$", "", title)
    found = _json("https://www.wikidata.org/w/api.php?" + urllib.parse.urlencode({
        "action": "wbsearchentities", "search": q, "language": "en", "uselang": "en",
        "type": "item", "limit": 10, "format": "json"}))
    ids = [x["id"] for x in found.get("search", [])]
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
        if year and not any(y.isdigit() and abs(int(y) - int(year)) <= 1 for y in years):
            continue
        labels = ents[i].get("labels", {})
        names = {_norm(l["value"]) for l in labels.values()}
        names |= {_norm(a["value"]) for al in ents[i].get("aliases", {}).values() for a in al}
        hits.append({"imdb": imdb,
                     "lb": next(iter(vals("P6127")), None),
                     "rt": next(iter(vals("P1258")), None),
                     "mc": next(iter(vals("P1712")), None),
                     "en": english_title(labels, {v.get("id") for v in vals("P364") if isinstance(v, dict)},
                                         [v["text"] for v in vals("P1476") if isinstance(v, dict) and v.get("language") != "en"]),
                     "v": 2,
                     "exact": _norm(q) in names})
    if len(hits) > 1:
        hits = [h for h in hits if h["exact"]]
    if len(hits) != 1 or (not year and not hits[0]["exact"]):
        return None
    h = hits[0]
    h.pop("exact")
    return h


def letterboxd(rec):
    """Fetch the Letterboxd page; return (slug, average rating out of 5 or None)."""
    url = f"https://letterboxd.com/film/{rec['lb']}/" if rec.get("lb") else f"https://letterboxd.com/imdb/{rec['imdb']}/"
    req = urllib.request.Request(url, headers=BROWSER_UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        final, page = r.geturl(), r.read().decode("utf-8", "replace")
    slug = (re.search(r"letterboxd\.com/film/([^/]+)/", final) or [None, rec.get("lb")])[1]
    m = re.search(r'name="twitter:data2" content="([\d.]+) out of 5"', page)
    return slug, (round(float(m[1]), 2) if m else None)


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
        if rec is None or (rec.get("imdb") and rec.get("v") != 2) \
                or stale(rec.get("checked"), RECHECK_MATCHED_DAYS if rec.get("imdb") else RECHECK_MISSING_DAYS):
            todo.append(f)
    todo.sort(key=lambda f: f["status"] != "on_sale")  # what you can book now first
    todo = todo[:MAX_LOOKUPS]

    def look(f):
        for t in [f["alt"], f["title"]]:
            if t:
                time.sleep(LOOKUP_PAUSE)
                hit = wikidata(t, f["year"])
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
            f["ext"]["lb"] = rec.get("lbSlug") or rec.get("lb") or ""
    # Keep the cache to films we still list.
    live = {i for f in films for i in f["ids"]}
    CACHE.write_text(json.dumps({k: v for k, v in sorted(cache.items()) if k in live},
                                ensure_ascii=False, indent=0))
    print(f"  links for {matched}/{len(films)} films ({looked} looked up, {min(len(rate), MAX_RATINGS)} ratings refreshed)")
