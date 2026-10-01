#!/usr/bin/env python3
"""Fetch all sources, merge by film, track when films go on sale, write one data file per region.

Usage: python3 scraper/build.py [region ...]      (default: all regions)
Regions: oslo -> site/data/films.json, state/seen.json
         westman -> site/data/westman.json, state/seen-westman.json
Each state file holds:
  baseline    when tracking started (films on sale then are never "new")
  lastDigest  when the daily email last went out (digest.py reads and updates it)
  films       {film id: {"since": when it last became on sale, "last": last time seen on sale}}
  dated       {film id: {"first": first seen with a date, "last": last seen with a date}}
              A film is "newly announced" the first time it has a concrete date (a Filmweb
              premiere date, or showings) while not yet on sale.
"""
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))
import external  # noqa: E402
import sources  # noqa: E402
import westman  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OFF_SALE_DAYS = 7  # a film must be off sale this long before it counts as new again
FORGET_DATED_DAYS = 90  # forget announced films not seen for this long
FMT = "%Y-%m-%dT%H:%M"


ID_PATTERNS = [
    ("fw-", r"filmweb\.no/film/([^/?#]+)"),
    ("cm-", r"cinemateket\.no/filmer/([^/?#]+)"),
    ("lm-", r"landmarkcinemas\.com/movie/([^/?#]+)"),
    ("cc-", r"cinemaclock\.com/movies/([^/?#]+)"),
    ("ev-", r"evanstheatre\.ca/movie/([^/?#]+)"),
]


def film_id(url):
    for prefix, pattern in ID_PATTERNS:
        m = re.search(pattern, url)
        if m:
            return prefix + m[1]
    return re.sub(r"[^a-z0-9]+", "-", url.lower())


def key(title):
    t = title.lower()
    t = re.sub(r"\s*\((restaurert|ny restaurering)[^)]*\)", "", t)
    t = re.sub(r"\s*\(\d{4}\)\s*$", "", t)
    t = re.sub(r"\s+[–—-]\s+(\d+\s*mm|restaurert.*|4k.*|ny kopi.*)$", "", t)
    t = re.sub(r"^(the|a|an)\s+", "", t)
    return re.sub(r"[^0-9a-zæøåäöüéè]+", " ", t).strip()


def years_match(a, b):
    return not a or not b or abs(int(a) - int(b)) <= 1


def merge(primary, extra):
    films = list(primary)
    index = {}
    for f in films:
        for t in (f["title"], f["alt"]):
            if t:
                index.setdefault(key(t), []).append(f)
    for c in extra:
        cands = index.get(key(c["title"]), []) + (index.get(key(c["alt"]), []) if c["alt"] else [])
        match = next((f for f in cands if years_match(f["year"], c["year"])), None)
        if match:
            match["shows"] += c["shows"]
            match["links"] += c["links"]
            match["series"] += c["series"]
            for k in ("alt", "director", "runtime", "blurb", "poster", "year", "premiere"):
                match[k] = match[k] or c[k]
            match["knownIds"] = match.get("knownIds") or c.get("knownIds") or {}
            # Once a Westman cinema schedules a film, it's no longer only "opening in Canada".
            match["scope"] = "" if (match["shows"] or c["shows"]) else (match.get("scope") or c.get("scope", ""))
        else:
            films.append(c)
    return films


# Film types, from explicit signals only (titles, Filmweb genres/show types, running time).
STAGE = re.compile(r"\b(met opera|opera\b|rbo:|royal ballet|ballet\b|bolshoi|national theatre live|nt live|world tour|"
                   r"live in |live at |live from|live viewing|in concert|concert\b|cheering party)|konsert|\bthe play\b", re.I)
SHORTS = re.compile(r"\b(shorts?|kortfilm(er|program)?)\b", re.I)
TALKS = re.compile(r"^(filmhistorie:|fra nrk-arkivet|jack presenterer!|lansering av)|\b(foredrag|"
                   r"seminar|quiz)\b|\bpresents itself\b|debutantslipp", re.I)


def film_kind(f):
    """'stage' (opera, ballet, theatre, concerts), 'talk' (lectures, special events), 'short', or 'film'.
    Mystery movies/screenings are secret films, so they stay 'film'."""
    genres = " ".join(f.get("genres") or []).lower()
    tags = {t for s in f["shows"] for t in s["tags"]}
    if STAGE.search(f["title"]) or "Opera" in tags or "konsert" in genres:
        return "stage"
    if TALKS.search(f["title"]):
        return "talk"
    if SHORTS.search(f["title"]) or "kortfilm" in genres or 0 < (f.get("runtime") or 0) < 45:
        return "short"
    return "film"


def finalise(films, now):
    now_s = now.strftime(FMT)
    out = []
    for f in films:
        shows = {(s["t"], s["cinema"]): s for s in f["shows"] if s["t"] >= now_s}  # same showing from two sources
        f["shows"] = sorted(shows.values(), key=lambda s: s["t"])
        f["links"] = list({l["url"]: l for l in f["links"]}.values())
        f["ids"] = [film_id(l["url"]) for l in f["links"]]
        f["id"] = f["ids"][0]
        f["series"] = list(dict.fromkeys(f["series"]))
        f["kind"] = film_kind(f)
        if not f["shows"] and f.get("checkedNationwide") and f["premiere"]:
            # Checked films premiere within 14 days; by then the cinemas here have normally published
            # showings. Playing elsewhere (e.g. only at a festival in Bergen) but not here: not coming
            # here. If it later gets showings here it reappears as newly on sale, so nothing is lost.
            stale = f["premiere"] <= (now - timedelta(days=3)).strftime("%Y-%m-%d")
            if f["elsewhere"] or stale:
                continue
        if any(s["ticket"] for s in f["shows"]):
            f["status"] = "on_sale"
        elif f["shows"] or (f["premiere"] and f["premiere"] >= (now - timedelta(days=14)).strftime("%Y-%m-%d")) \
                or (not f["premiere"] and f["links"][0]["label"] == "Filmweb"):
            f["status"] = "announced"
        else:
            continue  # premiered a while ago but not playing in this city
        out.append(f)
    return out


def track(films, now, state_path):
    """Set each film's onSaleSince and update state. Returns the state dict."""
    first_run = not state_path.exists()
    state = {"baseline": None, "lastDigest": None, "films": {}} if first_run else json.loads(state_path.read_text())
    now_s = now.strftime(FMT)
    state["baseline"] = state["baseline"] or now_s
    state["lastDigest"] = state["lastDigest"] or now_s
    seen = state["films"]
    dated_first_run = "dated" not in state
    dated = state.setdefault("dated", {})
    for f in films:
        # Newly announced: first time this film has a concrete date (any status counts as
        # "known", so a film dropping from on sale back to announced doesn't trigger).
        f["announcedSince"] = ""
        if f["premiere"] or f["shows"]:
            drecs = [dated[i] for i in f["ids"] if i in dated]
            first = min((r["first"] for r in drecs), default=None)
            if not first:
                # Baseline runs and films that go straight on sale never count as announced.
                first = state["baseline"] if dated_first_run or f["status"] == "on_sale" else now_s
            for i in f["ids"]:
                dated[i] = {"first": first, "last": now_s}
            if f["status"] == "announced":
                f["announcedSince"] = first
        recs = [seen[i] for i in f["ids"] if i in seen]
        if f["status"] != "on_sale":
            f["onSaleSince"] = ""
            continue
        last = max((r["last"] for r in recs), default=None)
        gone_too_long = last and datetime.strptime(last, FMT) < datetime.strptime(now_s, FMT) - timedelta(days=OFF_SALE_DAYS)
        since = now_s if (not recs or gone_too_long) else min(r["since"] for r in recs)
        for i in f["ids"]:
            seen[i] = {"since": since, "last": now_s}
        f["onSaleSince"] = since
    # Forget films that have been off sale long enough to count as new anyway.
    cutoff = (now - timedelta(days=OFF_SALE_DAYS + 3)).strftime(FMT)
    state["films"] = {k: v for k, v in sorted(seen.items()) if v["last"] >= cutoff}
    dcut = (now - timedelta(days=FORGET_DATED_DAYS)).strftime(FMT)
    state["dated"] = {k: v for k, v in sorted(dated.items()) if v["last"] >= dcut}
    state["lastRun"] = now_s
    return state


def fetch_oslo(now):
    print("Fetching Filmweb …")
    fw = sources.fetch_filmweb("Oslo")
    print(f"  {len(fw)} films, {sum(len(f['shows']) for f in fw)} showings")
    print("Fetching Cinemateket …")
    try:
        cm = sources.fetch_cinemateket()
        print(f"  {len(cm)} films, {sum(len(f['shows']) for f in cm)} showings")
    except Exception as e:
        # Don't let one source failing wipe its films from the "seen" record: records are
        # only pruned after OFF_SALE_DAYS, so a short outage won't make films look new.
        print(f"  ! Cinemateket failed: {e}", file=sys.stderr)
        cm = []
    return merge(fw, cm)


def fetch_westman(now):
    print("Fetching Westman cinemas …")
    films = []
    for f in westman.fetch_all(now):
        films = merge(films, [f])
    return films


REGIONS = {
    "oslo": {"name": "Oslo", "tz": "Europe/Oslo", "fetch": fetch_oslo, "first": "Cinemateket",
             "data": "films.json", "state": "seen.json", "sources": "Filmweb + Cinemateket"},
    "westman": {"name": "Westman", "tz": "America/Winnipeg", "fetch": fetch_westman, "first": "Landmark Brandon",
                "data": "westman.json", "state": "seen-westman.json",
                "sources": "Landmark + CinemaClock + Evans Theatre"},
}


def main(regions=None):
    regions = regions or list(REGIONS)
    built = []
    for r in regions:
        cfg = REGIONS[r]
        now = datetime.now(ZoneInfo(cfg["tz"])).replace(tzinfo=None)
        print(f"== {cfg['name']}")
        films = finalise(cfg["fetch"](now), now)
        state_path = ROOT / "state" / cfg["state"]
        state = track(films, now, state_path)
        built.append((r, cfg, now, films, state, state_path))

    print("Looking up Letterboxd / IMDb links …")
    try:  # one call for all regions: the link cache is shared
        external.enrich([f for b in built for f in b[3]], datetime.now(ZoneInfo("Europe/Oslo")).replace(tzinfo=None))
    except Exception as e:  # links are a nice-to-have; never fail the run over them
        print(f"  ! link lookup failed: {e}", file=sys.stderr)

    for r, cfg, now, films, state, state_path in built:
        new = [f for f in films if f["onSaleSince"] and f["onSaleSince"] > state["lastDigest"]]
        ann = [f for f in films if f["announcedSince"] and f["announcedSince"] > state["lastDigest"]]
        print(f"{cfg['name']}: {sum(f['status'] == 'on_sale' for f in films)} on sale, "
              f"{sum(f['status'] == 'announced' for f in films)} announced; since last digest: "
              f"{len(new)} newly on sale, {len(ann)} newly announced")
        for f in new:
            print("  • on sale: " + f["title"])
        for f in ann:
            print("  • announced: " + f["title"])
        cinemas = sorted({s["cinema"] for f in films for s in f["shows"]},
                         key=lambda c: (c != cfg["first"], c.lower()))
        data = ROOT / "site" / "data" / cfg["data"]
        data.parent.mkdir(parents=True, exist_ok=True)
        data.write_text(json.dumps({
            "generated": now.strftime(FMT), "baseline": state["baseline"], "region": r,
            "location": cfg["name"], "tz": cfg["tz"], "sources": cfg["sources"],
            "cinemas": cinemas, "films": films,
        }, ensure_ascii=False, separators=(",", ":")))
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=1))
        print(f"Wrote {data.relative_to(ROOT)} ({data.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main(sys.argv[1:] or None)
