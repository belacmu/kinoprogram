"""Westman (Brandon / Virden area, Manitoba) sources.

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
        tm = re.search(r"<h3 class='movietitle[^']*'[^>]*><a[^>]*href='/movies/([^']+)'>(.*?)</a>", block, re.S)
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
    """All Westman films. Landmark comes from the saved schedule if fresh, else from CinemaClock."""
    out = []
    lm = fetch_landmark(now)
    if lm is not None:
        print(f"  Landmark (saved {json.loads(LANDMARK_STATE.read_text())['fetched']}): {len(lm)} films")
        out += lm
    for slug, name in CC_THEATRES.items():
        if slug == "landmark-9-brandon" and lm is not None:
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
