"""Westman (Brandon / Virden area, Manitoba) sources.

- MovieScout (main source, used with MovieScout's permission for this small personal project):
  public showtimes API, Landmark Brandon in full incl. advance sales, plus small-town theatres.
  Fetched ONCE a day, one request at a time, and cached in state/moviescout.json; the other runs
  that day reuse the cache. Next 14 days daily; Landmark's later dates on a weekly rotation.

- Landmark Cinemas Brandon: its showtimes page embeds the full schedule (months ahead) as JSON.
  The site only serves Canadian visitors, so the workflow fetches it once a day through a Canadian
  VPN connection and `landmark_extract()` saves just the schedule to state/landmark-brandon.json.
  Every run then builds from that file. If it's missing or stale, CinemaClock covers Landmark.
- CinemaClock theatre pages: the other area cinemas (about a week ahead).
- Evans Theatre (Brandon University): its own site lists the whole season.
"""
import html
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from sources import film, get, split_year, text  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LANDMARK_STATE = ROOT / "state" / "landmark-brandon.json"
LANDMARK = "https://www.landmarkcinemas.com"
LANDMARK_MAX_AGE_HOURS = 36
CC = "https://www.cinemaclock.com"
# CinemaClock theatre slug -> how we name it. Landmark is listed here as the fallback.
CC_THEATRES = {
    "landmark-9-brandon": "Landmark Brandon",
    "evans-theatre": "Evans Theatre",                # merged with the Evans site; adds the year
    "gaiety-theatre": "Gaiety (Glenboro)",
    "community-theatre-carnduff": "Community Theatre (Carnduff)",
    "derrick-theatre": "Derrick (Virden)",          # no showtimes yet; picked up automatically if added
    "avalon-theatre-souris": "Avalon (Souris)",
    "moosomin-community-theatre": "Moosomin Community Theatre",
}
EVANS = "https://evanstheatre.ca"
MS_API = "https://api.moviescout.ca/v1"
MS_STATE = ROOT / "state" / "moviescout.json"
MS_UA = {"User-Agent": "Mozilla/5.0 (kinoprogram; small personal project, once a day; +https://github.com/belacmu/kinoprogram)"}
MS_PAUSE = 1.0             # seconds between requests: MovieScout asked us not to overload them
MS_NEAR_DAYS = 14          # fetched every day
MS_FAR_DAYS = 120          # Landmark only; each date between 14 and 120 days out is refreshed weekly
# MovieScout theatre id -> (our name, ticket/info link, far-ahead rotation?)
MS_THEATRES = {
    37255: ("Landmark Brandon", "https://www.landmarkcinemas.com/showtimes/brandon", True),
    22439: ("Gaiety (Glenboro)", "https://moviescout.ca/theatres/glenboro-gaiety-theatre-22439", False),
    22464: ("Strand (Melita)", "https://moviescout.ca/theatres/strand-theatre-melita-22464", False),
    22482: ("Avalon (Souris)", "https://moviescout.ca/theatres/avalon-theatre-22482", False),
    22467: ("Roxy (Neepawa)", "https://moviescout.ca/theatres/roxy-theatre-neepawa-22467", False),
    22500: ("Derrick (Virden)", "https://moviescout.ca/theatres/derrick-theatre-22500", False),
}
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
FORMAT_SUFFIX = re.compile(r"\s*\((?:[^()]*\b(?:3D|IMAX|Infinity Vision|Laser Ultra|UltraAVX|D-BOX|Dolby|Atmos|4DX|ScreenX|"
                           r"Recliner|VIP|Sensory|Subtitled|Dubbed|EST|Eng|Hindi|Punjabi|Tamil|Telugu|Malayalam|Kannada|Gujarati|Marathi|"
                           r"Bengali|Urdu|Korean|Japanese|Mandarin|Cantonese|Spanish|French|Version)\b[^()]*)\)\s*$", re.I)
EN_SUBS = re.compile(r"\b(EST|w/?\s*Eng|English subt|Eng\.? subt)", re.I)
EVENT_SUFFIX = re.compile(r"\s+[-–]\s+(?:Early Access|Advance|Sneak Peek|Fan Event|Opening Night|Special Event)\b.*$", re.I)


# ---------------------------------------------------------------- Landmark

def landmark_extract(page_html, now):
    """Pull the embedded schedule out of the showtimes page and save it (called by the workflow)."""
    i = page_html.find("pc.showtimesdata")
    j = page_html.find("[", page_html.find("'0'", i))
    if i < 0 or j < 0:
        raise ValueError("schedule not found on the page")
    depth = 0
    for k in range(j, len(page_html)):
        if page_html[k] == "[":
            depth += 1
        elif page_html[k] == "]":
            depth -= 1
            if depth == 0:
                break
    films = json.loads(page_html[j:k + 1])
    LANDMARK_STATE.write_text(json.dumps({"fetched": now.strftime("%Y-%m-%dT%H:%M"), "films": films},
                                         ensure_ascii=False, separators=(",", ":")))
    return len(films)


def landmark_needs_fetch(now):
    if not LANDMARK_STATE.exists():
        return True
    fetched = json.loads(LANDMARK_STATE.read_text()).get("fetched", "")
    return fetched < (now - timedelta(hours=20)).strftime("%Y-%m-%dT%H:%M")


def clock_24(t):
    m = re.match(r"(\d{1,2}):(\d{2})\s*([AP]M)", t.strip(), re.I)
    h = int(m[1]) % 12 + (12 if m[3].upper() == "PM" else 0)
    return f"{h:02d}:{m[2]}"


def landmark_title(raw, release_year, classic):
    """'Departed - 20th Anniversary, The' -> ('The Departed', '2006'); 'Halloween (1978)' -> ('Halloween', '1978').

    Landmark's ReleaseDate is the (re-)release date, so for re-releases we only trust a year we
    can work out; otherwise no year (which means no direct Letterboxd link rather than a wrong one).
    """
    t = FORMAT_SUFFIX.sub("", raw).strip()
    t = re.sub(r"\s+(?:RealD\s+3D\s+)?Fan Event\b.*$", "", t, flags=re.I)
    t = EVENT_SUFFIX.sub("", t)
    years_ago = 0
    m = re.search(r"\s*(?:[-–:]\s*)?(\d+)(?:st|nd|rd|th) Anniversary\b[^,]*", t, re.I)
    if m:
        years_ago, t = int(m[1]), t[:m.start()] + t[m.end():]
    t = re.sub(r"^(.*),\s*(The|A|An)$", r"\2 \1", t.strip())
    t, in_title = split_year(t)
    if in_title:
        return t, in_title
    if years_ago and release_year:
        return t, str(int(release_year) - years_ago)
    return t, "" if classic else release_year


def fetch_landmark(now):
    """Films from the saved Landmark schedule, or None if it's missing or too old."""
    if not LANDMARK_STATE.exists():
        return None
    data = json.loads(LANDMARK_STATE.read_text())
    if data.get("fetched", "") < (now - timedelta(hours=LANDMARK_MAX_AGE_HOURS)).strftime("%Y-%m-%dT%H:%M"):
        return None
    films = []
    for m in data["films"]:
        shows = []
        for day in m.get("Sessions") or []:
            for exp in day.get("ExperienceTypes") or []:
                for t in exp.get("Times") or []:
                    if t.get("SessionExpired"):
                        continue
                    names = [e["Name"] for e in t.get("Experience") or []]
                    en_subs = any(EN_SUBS.search(n) for n in names) or bool(EN_SUBS.search(m["Title"]))
                    shows.append({
                        "t": f"{day['NewDate']}T{clock_24(t['StartTime'])}",
                        "cinema": "Landmark Brandon",
                        "screen": t.get("Screen") or "",
                        "tags": [n for n in names if n not in ("Closed Caption", "Shout Out", "Standard")],
                        "note": "",
                        "ticket": (f"{LANDMARK}/booking?cinemaId={t['CinemaId']}&filmId={m['FilmId']}"
                                   f"&externalSessionId={t['ExternalSessionId']}&sessionId={t['Scheduleid']}"),
                        "status": "Sold out" if t.get("SoldOut") else "",
                        "dub": False,
                        "en": en_subs,
                    })
        if not shows and not m.get("IsComingSoon"):
            continue
        release = (m.get("ReleaseDate") or "")[:10]
        classic = any("rewind" in t.lower() or "classic" in t.lower() for s in shows for t in s["tags"])
        title, year = landmark_title(m["Title"], release[:4] if release > "1900" else "", classic)
        films.append(film(
            title=title,
            year=year,
            runtime=int(m["RunTime"]) if str(m.get("RunTime", "")).isdigit() else 0,
            director=m.get("Director") or "",
            blurb=html.unescape(m.get("Teaser") or ""),
            poster=m.get("Img") or "",
            links=[{"label": "Landmark", "url": f"{LANDMARK}/movie/{m.get('FriendlyName') or m['FilmId']}"}],
            shows=shows,
            premiere=release if release > "1900" else "",
            premiereConfirmed=bool(release),
        ))
    # Landmark lists formats (e.g. "Avengers: Doomsday (Infinity Vision)") as separate films.
    merged = {}
    for f in films:
        k = (f["title"].lower(), f["year"])
        if k in merged:
            merged[k]["shows"] += f["shows"]
        else:
            merged[k] = f
    return list(merged.values())


# ---------------------------------------------------------------- MovieScout

def _ms_get(path):
    import urllib.request
    req = urllib.request.Request(MS_API + path, headers=MS_UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def moviescout_refresh(now):
    """Fetch what's due today into state/moviescout.json (at most once a day). Returns the cache."""
    import time
    from zoneinfo import ZoneInfo
    cache = json.loads(MS_STATE.read_text()) if MS_STATE.exists() else {"fetchedOn": "", "days": {}}
    today = now.date()
    # Drop past days.
    cache["days"] = {k: v for k, v in cache["days"].items() if k.split("|")[1] >= today.isoformat()}
    if cache["fetchedOn"] == today.isoformat():
        return cache
    requests = errors = 0
    for tid, (_, _, far) in MS_THEATRES.items():
        for offset in range(MS_FAR_DAYS if far else MS_NEAR_DAYS):
            day = today + timedelta(days=offset)
            k = f"{tid}|{day.isoformat()}"
            if offset >= MS_NEAR_DAYS and k in cache["days"] and (offset % 7) != (today.toordinal() % 7):
                continue  # far-ahead date, not its turn this week
            # Times in this API are local wall-clock time, so ask from local midnight.
            try:
                time.sleep(MS_PAUSE)
                rows = _ms_get(f"/showtimes?theatre_id={tid}&date={day.isoformat()}T00%3A00%3A00")
                requests += 1
            except Exception as e:
                errors += 1
                print(f"  ! MovieScout {tid} {day}: {e}", file=sys.stderr)
                if errors >= 3:
                    print("  ! MovieScout: stopping after 3 errors; using what we have", file=sys.stderr)
                    MS_STATE.write_text(json.dumps(cache, ensure_ascii=False, separators=(",", ":")))
                    return cache
                continue
            cache["days"][k] = [{f: r.get(f) for f in ("movie_id", "movie_base_id", "name", "format", "start_time",
                                                       "release_year", "audio_lang", "subtitles", "duration_mins", "img")}
                                for r in rows]
    cache["fetchedOn"] = today.isoformat()
    MS_STATE.write_text(json.dumps(cache, ensure_ascii=False, separators=(",", ":")))
    print(f"  MovieScout: {requests} requests")
    return cache


def fetch_moviescout(now):
    """Films from the MovieScout cache (refreshing it first if today's fetch hasn't happened)."""
    from zoneinfo import ZoneInfo
    cache = moviescout_refresh(now)
    films = {}
    for k, rows in cache["days"].items():
        tid = int(k.split("|")[0])
        cinema, ticket, _ = MS_THEATRES.get(tid, (None, None, None))
        if not cinema:
            continue
        for r in rows:
            mid = r["movie_base_id"] or r["movie_id"]
            # MovieScout's start_time is the cinema's local wall-clock time despite the "Z" suffix
            # (checked against Landmark's own data and CinemaClock), so don't convert it.
            t = datetime.fromisoformat(r["start_time"][:19])
            subs = (r.get("subtitles") or "")
            name, year_in_title = split_year(r["name"])  # "Halloween (1978)": a re-release
            f = films.setdefault(mid, film(
                title=name, year=year_in_title or str(r["release_year"] or ""), runtime=r.get("duration_mins") or 0,
                poster=f"https://cdn.moviescout.ca/{r['img']}" if r.get("img") else "",
                links=[{"label": "MovieScout", "url": f"https://moviescout.ca/movies/{mid}"}],
            ))
            f["shows"].append({
                "t": t.strftime("%Y-%m-%dT%H:%M"), "cinema": cinema, "screen": "",
                "tags": [x for x in [r.get("format") if r.get("format") not in (None, "Standard") else "",
                                     f"{r['audio_lang']} audio" if r.get("audio_lang") not in (None, "English") else "",
                                     f"{subs} subtitles" if subs else ""] if x],
                "note": "" if tid == 37255 else "Tickets at the door",
                "ticket": ticket, "status": "", "dub": False, "en": subs.lower().startswith("english"),
            })
    return list(films.values())


# ---------------------------------------------------------------- CinemaClock

def _cc_date(label, earliest):
    """'Oct 2' + data-earliest-date '20261001' -> '2026-10-02' (handles the year rollover)."""
    mon, day = label.split()
    m = MONTHS[mon[:3].lower()]
    y = int(earliest[:4]) + (1 if m < int(earliest[4:6]) else 0)
    return f"{y}-{m:02d}-{int(day):02d}"


def fetch_cinemaclock(slug, cinema):
    url = f"{CC}/movie-theaters/{slug}"
    h = get(url)
    films = []
    for block in re.split(r'<div id="moviecin', h)[1:]:
        tm = re.search(r"<h3 class='movietitle[^']*'[^>]*><a[^>]*href='/movies/([^']+)'[^>]*>(.*?)</a>", block, re.S)
        if not tm:
            continue
        title = text(tm[2])
        genre = text((re.search(r"<p class='moviegenre'>(.*?)</p>", block, re.S) or [None, ""])[1])
        year = (re.search(r"\b(19\d\d|20\d\d)\b", genre) or re.search(r"-((?:19|20)\d\d)$", tm[1]) or [None, ""])[1]
        rt = re.search(r"(\d+)h(\d+)m", genre)
        poster = (re.search(r"data-src='(/images/posters/[^']+)'", block) or [None, ""])[1]
        shows = []
        for sub in re.findall(r'<div data-earliest-date="(\d{8})" class="filall[^"]*">(.*?)(?=<div data-earliest-date=|<!-- endsb -->)', block, re.S):
            earliest, body = sub
            fmt = [text(x) for x in re.findall(r'<p class="timesalso(?: ccad)?">(.*?)</p>', body, re.S)]
            fmt = [x for x in fmt if x and not x.lower().startswith(("standard", "optional"))]
            en = any("eng. subt" in x.lower() or "english subt" in x.lower() for x in fmt)
            for day, spans in re.findall(r'<span class="timesdate">([A-Z][a-z]{2} \d{1,2})</span></u><i>(.*?)</i>', body, re.S):
                date = _cc_date(day, earliest)
                for cls, hhmm, tix in re.findall(r'<span class="(tix|notix)[^"]*" data-time="(\d{4})"(?: id="(tix\d+)")?', spans):
                    shows.append({
                        "t": f"{date}T{hhmm[:2]}:{hhmm[2:]}",
                        "cinema": cinema,
                        "screen": "",
                        "tags": fmt,
                        "note": "" if cls == "tix" and tix else "Tickets at the door",
                        # Small theatres sell at the door: link the theatre page so the showing still counts as on sale.
                        "ticket": f"{CC}/buy-tickets/{tix}" if cls == "tix" and tix else url,
                        "status": "",
                        "dub": False,
                        "en": en,
                    })
        if shows:
            films.append(film(
                title=title, year=year, runtime=(int(rt[1]) * 60 + int(rt[2])) if rt else 0,
                poster=CC + poster if poster else "",
                links=[{"label": "CinemaClock", "url": f"{CC}/movies/{tm[1]}"}],
                shows=shows,
            ))
    return films


# ---------------------------------------------------------------- Evans Theatre

def fetch_evans():
    h = get(f"{EVANS}/upcoming-movies/")
    films = []
    for path in dict.fromkeys(re.findall(r'href="(/movie/[^"]+/)"', h)):
        p = get(EVANS + path)
        title = text((re.search(r'<h1 class="movie-title">(.*?)</h1>', p, re.S) or [None, ""])[1])
        shows = []
        for li in re.findall(r'<ul class="showtime-list">(.*?)</ul>', p, re.S):
            for d in re.findall(r"<li>(.*?)</li>", li, re.S):
                m = re.search(r"([A-Z][a-z]{2})[a-z]*\.?\s+(\d{1,2}),\s+(\d{4})\s+at\s+(\d{1,2}:\d{2}\s*[AP]M)", text(d))
                if m:
                    shows.append({
                        "t": f"{m[3]}-{MONTHS[m[1].lower()]:02d}-{int(m[2]):02d}T{clock_24(m[4])}",
                        "cinema": "Evans Theatre", "screen": "", "tags": [],
                        "note": "Cash at the door", "ticket": EVANS + path, "status": "",
                        "dub": False, "en": False,
                    })
        og = (re.search(r'property="og:image" content="([^"]+)"', p) or [None, ""])[1]
        blurb = text((re.search(r'<div class="movie-rating">.*?</div>.*?<p>(.*?)</p>', p, re.S) or [None, ""])[1])
        if title and shows:
            films.append(film(title=title, blurb=blurb, poster=og,
                              links=[{"label": "Evans Theatre", "url": EVANS + path}], shows=shows))
    return films


def fetch_all(now):
    """All Westman films: MovieScout first; CinemaClock + Evans for what MovieScout doesn't cover,
    and CinemaClock for Landmark if MovieScout fails."""
    out = []
    ms_ok = False
    try:
        ms = fetch_moviescout(now)
        ms_ok = any(s["cinema"] == "Landmark Brandon" for f in ms for s in f["shows"])
        print(f"  MovieScout: {len(ms)} films, {sum(len(f['shows']) for f in ms)} showings")
        out += ms
    except Exception as e:
        print(f"  ! MovieScout: {e}", file=sys.stderr)
    # MovieScout is complete for Landmark; for the small theatres combine both (duplicates are
    # removed later), since either one can be missing a showing.
    covered = {"Landmark Brandon"} if ms_ok else set()
    for slug, name in CC_THEATRES.items():
        if name in covered:
            continue
        try:
            fs = fetch_cinemaclock(slug, name)
            print(f"  CinemaClock {name}: {len(fs)} films")
            out += fs
        except Exception as e:
            print(f"  ! CinemaClock {name}: {e}", file=sys.stderr)
    try:
        ev = fetch_evans()
        print(f"  Evans Theatre: {len(ev)} films")
        out += ev
    except Exception as e:
        print(f"  ! Evans: {e}", file=sys.stderr)
    return out


if __name__ == "__main__":
    # Used by the workflow: python scraper/westman.py extract-landmark PAGE.html
    if sys.argv[1:2] == ["extract-landmark"]:
        from zoneinfo import ZoneInfo
        n = landmark_extract(Path(sys.argv[2]).read_text(encoding="utf-8", errors="replace"),
                             datetime.now(ZoneInfo("America/Winnipeg")).replace(tzinfo=None))
        print(f"Saved Landmark schedule: {n} film entries")
    elif sys.argv[1:2] == ["needs-landmark"]:
        from zoneinfo import ZoneInfo
        print("yes" if landmark_needs_fetch(datetime.now(ZoneInfo("America/Winnipeg")).replace(tzinfo=None)) else "no")
