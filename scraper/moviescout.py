"""MovieScout, shared by the two Manitoba regions (Westman and Winnipeg).

Public showtimes API, used with MovieScout's permission for this small personal project on the condition that we
poll it sparingly. One fetch a day for both regions, at one request a second, cached in state/moviescout.json; every
other run that day reads the cache. CinemaClock covers about the next week at every cinema, so MovieScout is only
asked about what CinemaClock can't see:

- Discovery: the films playing near a point between the two regions (Portage la Prairie) on a date, one request per
  date (`movies/list/near`, which returns at most 10 films). Dates a week to 30 days out daily; later dates, out to
  120 days, weekly, and daily on the Thursday/Friday of each "coming soon" release. A full answer (10 films) is
  asked again per city for dates two weeks or more out; closer than that it's the cinemas' regular week, which
  CinemaClock shows within days.
- Times: for each film and date found, the showings near each city (`showtimes?movie_id&date&lat&lng`), fetched when
  first seen and again when the date is 10 days away.
- A film showing near one city only: whether the other city has scheduled it since (`showtimes/first-showing`, one
  request per such film and city), since the lists by date don't change when it does.
- Strand (Melita) and Roxy (Neepawa), which CinemaClock doesn't list and which are outside the Brandon search: per
  theatre, the next 14 days, each date every 3 days.
- The national "coming soon" list (1-2 requests) and each film's IMDb/TMDB ids (once per film).
- A cinema whose CinemaClock page failed: its next week per theatre instead, at most once a day.

MovieScout's start_time is the cinema's local wall-clock time despite the "Z" suffix (checked against Landmark's own
data and CinemaClock), so it isn't converted; dates are asked from local midnight.
"""
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from sources import film, split_year  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state" / "moviescout.json"
API = "https://api.moviescout.ca/v1"
UA = {"User-Agent": "Mozilla/5.0 (kinoprogram; small personal project, once a day; +https://github.com/belacmu/kinoprogram)"}
PAUSE = 1.0            # seconds between requests: MovieScout asked us not to overload them
MAX_REQUESTS = 150     # per day; spreads the first fill (and any catching up) over a few days
MIDPOINT = (49.9728, -98.2920)                       # Portage la Prairie: one search covers both regions
CITIES = {"brandon": (49.8485, -99.9501), "winnipeg": (49.8951, -97.1384)}
CAP = 10               # movies/list/near returns at most this many films
DISCOVER_FROM = 7      # days ahead; CinemaClock covers the week before
DAILY_UNTIL = 30       # dates up to this many days ahead are checked every day, later ones weekly
SPLIT_FROM = 14        # a full answer this far out or more is asked again per city
HORIZON = 120
REFRESH_AT = 10        # a film's showings on a date are fetched again once the date is this close
PER_THEATRE = {22464: "Strand (Melita)", 22467: "Roxy (Neepawa)"}
PER_THEATRE_DAYS = 14
PER_THEATRE_EVERY = 3  # days between refreshes of each date
FALLBACK_DAYS = DISCOVER_FROM
FORGET_NAMES_DAYS = 120
ROW_FIELDS = ("id", "movie_id", "movie_base_id", "theatre_id", "name", "format", "start_time", "url", "release_year",
              "audio_lang", "subtitles", "duration_mins", "img")


class Budget:
    def __init__(self, limit):
        self.limit, self.used, self.errors = limit, 0, 0

    def get(self, path, raw=False):
        """GET a path (JSON, or the text if `raw`; "" for a 404 then), or None once the day's budget is spent or
        after 3 errors."""
        if self.used >= self.limit or self.errors >= 3:
            return None
        time.sleep(PAUSE)
        self.used += 1
        req = urllib.request.Request(API + path, headers=UA)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read().decode().strip() if raw else json.load(r)
        except urllib.error.HTTPError as e:
            if raw and e.code == 404:  # first-showing: no showing near there
                return ""
            self.errors += 1
            print(f"  ! MovieScout {path}: {e}", file=sys.stderr)
            return None
        except Exception as e:
            self.errors += 1
            print(f"  ! MovieScout {path}: {e}", file=sys.stderr)
            if self.errors >= 3:
                print("  ! MovieScout: stopping after 3 errors; using what we have", file=sys.stderr)
            return None


def norm(title):
    t = split_year(title)[0].lower()
    t = re.sub(r"^(the|a|an)\s+", "", t)
    return re.sub(r"[^0-9a-z]+", " ", t).strip()


def _q(d):
    return f"date={d.isoformat()}T00%3A00%3A00"


def _ll(point):
    return f"lat={point[0]}&lng={point[1]}"


def _rows(rows, theatre_id=None):
    return [{**{f: r.get(f) for f in ROW_FIELDS}, **({"theatre_id": theatre_id} if theatre_id else {})} for r in rows]


def _learn(cache, mid, name, year, today):
    """Remember a film's MovieScout id by title, so a film CinemaClock lists keeps its MovieScout id."""
    title, in_title = split_year(name)
    cands = cache["names"].setdefault(norm(title), [])
    for c in cands:
        if c["id"] == mid:
            c["last"] = today
            return
    cands.append({"id": mid, "year": in_title or str(year or ""), "last": today})


def _our_theatres():
    """{city: the MovieScout theatre ids its region shows}."""
    import westman
    import winnipeg
    return {"brandon": set(westman.MS_THEATRES), "winnipeg": set(winnipeg.MS_THEATRES)}


def load():
    cache = json.loads(STATE.read_text()) if STATE.exists() else {}
    cache.setdefault("fetchedOn", "")
    for k in ("movies", "names", "near", "filmdays", "theatredays", "fallbackOn", "log", "oneRegion"):
        cache.setdefault(k, {})
    cache.setdefault("upcoming", [])
    if "days" in cache:
        # Before October 2026 every theatre was fetched day by day; keep what it taught us about ids.
        for rows in cache.pop("days").values():
            for r in rows:
                _learn(cache, r["movie_base_id"] or r["movie_id"], r["name"], r.get("release_year"),
                       cache["fetchedOn"] or date.today().isoformat())
        cache["fetchedOn"] = ""  # and fetch in the new way today, even if the old way already did
    return cache


def save(cache):
    STATE.write_text(json.dumps(cache, ensure_ascii=False, separators=(",", ":")))


def release_watch(cache, today):
    """Thursday and Friday of each chain release on the "coming soon" list (previews start Thursday)."""
    out = set()
    for m in cache["upcoming"]:
        if m.get("movieglu_id") and not m.get("indie") and m.get("release_date"):
            d = date.fromisoformat(m["release_date"][:10])
            out |= {d, d - timedelta(days=1)}
    return {d for d in out if DISCOVER_FROM <= (d - today).days < HORIZON}


def refresh(now, fallback=()):
    """Do today's MovieScout fetch if it hasn't happened (and fetch `fallback` theatre ids' next week if their
    CinemaClock page failed). Returns the cache."""
    cache = load()
    today = now.date()
    t = today.isoformat()
    cache["near"] = {d: v for d, v in cache["near"].items() if d >= t}
    cache["filmdays"] = {k: v for k, v in cache["filmdays"].items() if k.rsplit("|", 1)[1] >= t}
    cache["theatredays"] = {k: v for k, v in cache["theatredays"].items() if k.split("|")[1] >= t}
    b = Budget(MAX_REQUESTS - cache["log"].get(t, 0))

    for tid in fallback:  # CinemaClock failed for this cinema: its next week from MovieScout instead
        if cache["fallbackOn"].get(str(tid)) == t:
            continue
        for offset in range(FALLBACK_DAYS):
            day = today + timedelta(days=offset)
            rows = b.get(f"/showtimes?theatre_id={tid}&{_q(day)}")
            if rows is not None:
                cache["theatredays"][f"{tid}|{day.isoformat()}"] = {"fetched": t, "rows": _rows(rows, tid)}
        cache["fallbackOn"][str(tid)] = t
        print(f"  MovieScout: next {FALLBACK_DAYS} days of theatre {tid} (its CinemaClock page failed)")

    if cache["fetchedOn"] != t:
        _daily(cache, today, b)
        cache["fetchedOn"] = t
    if b.used:
        cache["log"][t] = cache["log"].get(t, 0) + b.used
        cache["log"] = dict(sorted(cache["log"].items())[-14:])
        print(f"  MovieScout: {b.used} requests ({cache['log'][t]} today)")
    save(cache)
    return cache


def _daily(cache, today, b):
    t = today.isoformat()
    counts = dict.fromkeys(("upcoming", "theatres", "one region", "discovery", "times", "ids"), 0)

    # National "coming soon" list (about 3 months ahead; 50 per page, normally 1-2 pages).
    upcoming, offset = [], 0
    while offset < 300:
        page = b.get(f"/movies?lang=en&version_type=Standard&upcoming=true&{_q(today)}"
                     f"&status=ok&limit=50&offset={offset}&min_duration=1&max_duration=600")
        if page is None:
            upcoming = None
            break
        counts["upcoming"] += 1
        upcoming += page.get("movies") or []
        offset += 50
        if offset >= (page.get("total") or 0):
            break
    if upcoming is not None:
        cache["upcoming"] = [{k: m.get(k) for k in ("id", "name", "release_date", "duration_mins", "imdb_title_id", "tmdb_id",
                                                     "movieglu_id", "indie", "directors", "poster_imgs", "synopsis")}
                             for m in upcoming]

    # Strand and Roxy, theatre by theatre (first: the budget can run out further down on a catching-up day).
    for tid in PER_THEATRE:
        for offset in range(PER_THEATRE_DAYS):
            day = today + timedelta(days=offset)
            k = f"{tid}|{day.isoformat()}"
            if k in cache["theatredays"] and day.toordinal() % PER_THEATRE_EVERY != today.toordinal() % PER_THEATRE_EVERY:
                continue
            rows = b.get(f"/showtimes?theatre_id={tid}&{_q(day)}")
            if rows is None:
                continue
            counts["theatres"] += 1
            cache["theatredays"][k] = {"fetched": t, "rows": _rows(rows, tid)}

    # Films parked for a city (no showings at our cinemas near it, though listed near the midpoint): has that city
    # scheduled them since? The lists by date don't change when the second city adds a film on dates the first one
    # already plays, so ask for its first showing near the city: daily for a film playing in the other region, weekly
    # for one at none of our cinemas (e.g. only at the Cinematheque, which comes from its own site).
    ours = _our_theatres()
    playing = {k.rsplit("|", 1)[0] for k, v in cache["filmdays"].items()
               if any(r["theatre_id"] in ours[k.split("|")[0]] for r in v["rows"])}
    listed_now = {str(mid) for e in cache["near"].values()
                  for mids in [e["films"], *(e.get("cities") or {}).values()] for mid in mids}
    firsts = {}  # "city|film" -> first showing near the city ("" if none), as asked today
    for k, v in sorted(cache["oneRegion"].items()):
        city, mid = k.split("|")
        if mid not in listed_now:
            del cache["oneRegion"][k]
            continue
        elsewhere = any(f"{c}|{mid}" in playing for c in CITIES if c != city)
        if not elsewhere and (today - date.fromisoformat(v["checked"])).days < 7:
            continue
        first = b.get(f"/showtimes/first-showing?film_id={mid}&{_ll(CITIES[city])}&date={t}", raw=True)
        if first is None:
            continue
        counts["one region"] += 1
        firsts[k] = first[:10]
        v["checked"] = t
        if first[:10] != v["first"]:  # something new near this city: unpark and fetch the film's dates there again
            del cache["oneRegion"][k]
            cache["filmdays"] = {fk: fv for fk, fv in cache["filmdays"].items() if not fk.startswith(k + "|")}

    # Date by date, nearest first: which films play near the two regions (if the date is due today), then each
    # film's showings near each city (when first seen, and again 10 days out). Whatever the budget doesn't reach
    # is done another day.
    watch = release_watch(cache, today)
    for offset in range(DISCOVER_FROM, HORIZON):
        day = today + timedelta(days=offset)
        d = day.isoformat()
        prev = cache["near"].get(d)
        due = (not prev or offset <= DAILY_UNTIL or day in watch or day.toordinal() % 7 == today.toordinal() % 7
               or (today - date.fromisoformat(prev["checked"])).days >= 8)  # catch up after missed days
        found = b.get(f"/movies/list/near?{_q(day)}&{_ll(MIDPOINT)}") if due else None
        if found is not None:
            counts["discovery"] += 1
            entry = {"checked": t, "capped": len(found) >= CAP, "films": [m.get("base_id") or m["id"] for m in found]}
            for m in found:
                _learn(cache, m.get("base_id") or m["id"], m["name"], (m.get("release_date") or "")[:4], t)
            if entry["capped"] and offset >= SPLIT_FROM:  # too many to see from the midpoint: ask each city
                entry["cities"] = {}
                for city, point in CITIES.items():
                    got = b.get(f"/movies/list/near?{_q(day)}&{_ll(point)}")
                    if got is not None:
                        counts["discovery"] += 1
                        entry["cities"][city] = [m.get("base_id") or m["id"] for m in got]
            cache["near"][d] = entry
        entry = cache["near"].get(d)
        if not entry or (entry["capped"] and offset < SPLIT_FROM):
            continue  # not checked yet, or the cinemas' regular week, which CinemaClock shows within days
        per_city = entry.get("cities") or {c: entry["films"] for c in CITIES}
        listed = {f"{c}|{mid}|{d}" for c, mids in per_city.items() for mid in mids}
        if not entry["capped"] or entry.get("cities"):  # a complete list: drop films no longer playing that day
            cache["filmdays"] = {k: v for k, v in cache["filmdays"].items() if not k.endswith(f"|{d}") or k in listed}
        for k in sorted(listed):
            have = cache["filmdays"].get(k)
            if have and not (offset <= REFRESH_AT and (day - date.fromisoformat(have["fetched"])).days > REFRESH_AT):
                continue
            city, mid, _ = k.split("|")
            cm = f"{city}|{mid}"
            if cm in cache["oneRegion"] or (cm in firsts and (not firsts[cm] or d < firsts[cm])):
                continue  # parked, or before its first showing near this city
            rows = b.get(f"/showtimes?movie_id={mid}&{_q(day)}&{_ll(CITIES[city])}")
            if rows is None:
                continue
            counts["times"] += 1
            cache["filmdays"][k] = {"fetched": t, "rows": _rows(rows)}
            if any(r["theatre_id"] in ours[city] for r in rows):
                playing.add(cm)
            elif cm not in playing:  # nothing at our cinemas near this city yet: where does it start there?
                if cm not in firsts:
                    first = b.get(f"/showtimes/first-showing?film_id={mid}&{_ll(CITIES[city])}&date={t}", raw=True)
                    if first is None:
                        continue
                    counts["one region"] += 1
                    firsts[cm] = first[:10]
                if not firsts[cm] or firsts[cm] == d:  # none at all, or only at a cinema we don't show: park it
                    cache["oneRegion"][cm] = {"checked": t, "first": firsts[cm]}

    # Every film seen: remember its id by title; exact IMDb/TMDB ids once per film.
    rows = [r for v in list(cache["filmdays"].values()) + list(cache["theatredays"].values()) for r in v["rows"]]
    for r in rows:
        _learn(cache, r["movie_base_id"] or r["movie_id"], r["name"], r.get("release_year"), t)
    known = {str(m["id"]): m for m in cache["upcoming"]}
    ids = {str(r["movie_base_id"] or r["movie_id"]) for r in rows} | set(known)
    for mid in sorted(ids - set(cache["movies"]))[:60]:
        m = known.get(mid)
        if not m:
            m = b.get(f"/movies/{mid}?lang=en")
            if m is None:
                continue
            counts["ids"] += 1
            m = m.get("movie", m)
        cache["movies"][mid] = {"imdb": m.get("imdb_title_id") or "", "tmdb": m.get("tmdb_id") or "",
                                "directors": m.get("directors") or []}
    cutoff = (today - timedelta(days=FORGET_NAMES_DAYS)).isoformat()
    cache["names"] = {k: kept for k, v in sorted(cache["names"].items()) if (kept := [c for c in v if c["last"] >= cutoff])}
    print("  MovieScout today: " + ", ".join(f"{k} {v}" for k, v in counts.items()))


def films(cache, theatres, covered):
    """Films with showings at `theatres` ({MovieScout theatre id: (name, link, note)}).

    `covered` is {cinema name: last date CinemaClock lists}: MovieScout's showings at that cinema up to that date are
    left out, since CinemaClock's are newer (MovieScout's were fetched when the date was far off)."""
    seen, out = set(), {}
    for v in list(cache["filmdays"].values()) + list(cache["theatredays"].values()):
        for r in v["rows"]:
            sid = r.get("id") or (r["theatre_id"], r["start_time"], r["movie_id"])  # a showing can be in both lists
            if r["theatre_id"] not in theatres or sid in seen:
                continue
            seen.add(sid)
            cinema, link, note = theatres[r["theatre_id"]]
            t = datetime.fromisoformat(r["start_time"][:19])
            if t.strftime("%Y-%m-%d") <= covered.get(cinema, ""):
                continue
            mid = r["movie_base_id"] or r["movie_id"]
            subs = r.get("subtitles") or ""
            name, year_in_title = split_year(r["name"])  # "Halloween (1978)": a re-release
            info = cache["movies"].get(str(mid), {})
            f = out.setdefault(mid, film(
                director=", ".join(info.get("directors") or []),
                knownIds={k: info[k] for k in ("imdb", "tmdb") if info.get(k)},
                title=name, year=year_in_title or str(r["release_year"] or ""), runtime=r.get("duration_mins") or 0,
                poster=f"https://cdn.moviescout.ca/{r['img']}" if r.get("img") else "",
                links=[{"label": "MovieScout", "url": f"https://moviescout.ca/movies/{mid}"}],
            ))
            f["shows"].append({
                "t": t.strftime("%Y-%m-%dT%H:%M"), "cinema": cinema, "screen": "",
                "tags": [x for x in [r.get("format") if r.get("format") not in (None, "Standard") else "",
                                     f"{r['audio_lang']} audio" if r.get("audio_lang") not in (None, "English") else "",
                                     f"{subs} subtitles" if subs else ""] if x],
                "note": note, "ticket": r.get("url") or link, "status": "", "dub": False,
                "en": subs.lower().startswith("english"),
            })
    return list(out.values())


def link(films_, cache):
    """Give films from other sources (CinemaClock) their MovieScout id when the title is known, so a film keeps the
    same id (watchlist, "new" tracking) whichever source lists it, and gets exact IMDb/TMDB ids."""
    for f in films_:
        if any(l["label"] == "MovieScout" for l in f["links"]):
            continue
        n = norm(f["title"])
        # Same title, or one is the other plus words ("Rolling Loud" / "Rolling Loud the Movie").
        cands = cache["names"].get(n) or [c for k, v in cache["names"].items() if min(len(n.split()), len(k.split())) >= 2
                                           and (k.startswith(n + " ") or n.startswith(k + " ")) for c in v]
        # Years within one, or MovieScout listing a re-release under its re-release year
        # ("Avengers: Endgame" 2019 / "Avengers Endgame: Encore" 2026).
        match = [c for c in cands if not c["year"] or not f["year"] or abs(int(c["year"]) - int(f["year"])) <= 1
                 or (int(c["year"]) > int(f["year"]) and int(c["year"]) >= int(cache["fetchedOn"][:4] or 0) - 1)]
        if len(match) != 1:
            continue
        mid = match[0]["id"]
        f["links"].insert(0, {"label": "MovieScout", "url": f"https://moviescout.ca/movies/{mid}"})
        info = cache["movies"].get(str(mid), {})
        f["knownIds"] = f.get("knownIds") or {k: info[k] for k in ("imdb", "tmdb") if info.get(k)}
    return films_


def region_films(now, cc_theatres, theatres):
    """One Manitoba region's films: CinemaClock (`cc_theatres`, {slug: name}) for about the next week, MovieScout
    (`theatres`, as in films()) beyond it and for what CinemaClock doesn't list, and the "coming soon" list.
    MovieScout's films come first, so a film's id stays its MovieScout one."""
    import cinemaclock
    cc, covered, failed = [], {}, set()
    for slug, name in cc_theatres.items():
        try:
            fs = cinemaclock.fetch_theatre(slug, name)
            print(f"  CinemaClock {name}: {len(fs)} films")
            cc += fs
            dates = [s["t"][:10] for f in fs for s in f["shows"]]
            if dates:
                covered[name] = max(dates)
        except Exception as e:
            failed.add(name)
            print(f"  ! CinemaClock {name}: {e}", file=sys.stderr)
    out = []
    try:
        cache = refresh(now, fallback=[tid for tid, (name, _, _) in theatres.items() if name in failed])
        ms = films(cache, theatres, covered)
        print(f"  MovieScout: {len(ms)} films, {sum(len(f['shows']) for f in ms)} showings")
        up = upcoming_films(cache)
        print(f"  MovieScout coming soon (chain releases, Canada): {len(up)} films")
        out = ms + up
        link(cc, cache)
    except Exception as e:  # CinemaClock alone still gives the next week
        print(f"  ! MovieScout: {e}", file=sys.stderr)
    return out + cc


def upcoming_films(cache):
    """Chain releases coming to Canadian cinemas (MovieScout's national list), as announced films.

    Not specific to a region: they're shown as "opening in Canada" until a cinema here schedules them. "Chain
    release" = has a MovieGlu id (MovieScout's feed for chains like Landmark and Cineplex) and isn't flagged indie;
    that keeps out the arthouse/Québec titles that won't reach Manitoba.
    """
    out = []
    for m in cache["upcoming"]:
        if not m.get("movieglu_id") or m.get("indie") or not m.get("release_date"):
            continue
        poster = (m.get("poster_imgs") or [""])[0]
        out.append(film(
            title=m["name"], year=m["release_date"][:4], runtime=m.get("duration_mins") or 0,
            director=", ".join(m.get("directors") or []), blurb=m.get("synopsis") or "",
            poster=f"https://cdn.moviescout.ca/{poster}" if poster else "",
            links=[{"label": "MovieScout", "url": f"https://moviescout.ca/movies/{m['id']}"}],
            premiere=m["release_date"], premiereConfirmed=True, scope="Canada",
            knownIds={k2: v for k2, v in (("imdb", m.get("imdb_title_id")), ("tmdb", m.get("tmdb_id"))) if v},
        ))
    return out
