"""Westman (Brandon / Virden area, Manitoba) sources.

- CinemaClock theatre pages: every area cinema it lists, about the next week.
- MovieScout (used with MovieScout's permission, polled sparingly; see moviescout.py): beyond that week, Landmark
  Brandon's advance sales months ahead, and Strand (Melita) and Roxy (Neepawa), which CinemaClock doesn't list.
  One fetch a day shared with Winnipeg.
- Evans Theatre (Brandon University): its own site lists the whole season.

Landmark Cinemas Brandon's own site isn't fetched: it refuses automated requests. Its showings come from CinemaClock
and MovieScout.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import moviescout  # noqa: E402
from sources import film, get, text  # noqa: E402

# CinemaClock theatre slug -> how we name it.
CC_THEATRES = {
    "landmark-9-brandon": "Landmark Brandon",
    "evans-theatre": "Evans Theatre",                # merged with the Evans site; adds the year
    "gaiety-theatre": "Gaiety (Glenboro)",
    "community-theatre-carnduff": "Community Theatre (Carnduff)",
    "derrick-theatre": "Derrick (Virden)",
    "avalon-theatre-souris": "Avalon (Souris)",
    "moosomin-community-theatre": "Moosomin Community Theatre",
}
EVANS = "https://evanstheatre.ca"
DOOR = "Tickets at the door"
# MovieScout theatre id -> (our name, ticket/info link, note). Only Strand and Roxy are asked about theatre by
# theatre; the others' showings come with the searches near Brandon.
MS_THEATRES = {
    37255: ("Landmark Brandon", "https://www.landmarkcinemas.com/showtimes/brandon", ""),
    22439: ("Gaiety (Glenboro)", "https://moviescout.ca/theatres/glenboro-gaiety-theatre-22439", DOOR),
    22464: ("Strand (Melita)", "https://moviescout.ca/theatres/strand-theatre-melita-22464", DOOR),
    22482: ("Avalon (Souris)", "https://moviescout.ca/theatres/avalon-theatre-22482", DOOR),
    22467: ("Roxy (Neepawa)", "https://moviescout.ca/theatres/roxy-theatre-neepawa-22467", DOOR),
    22500: ("Derrick (Virden)", "https://moviescout.ca/theatres/derrick-theatre-22500", DOOR),
}
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def clock_24(t):
    m = re.match(r"(\d{1,2}):(\d{2})\s*([AP]M)", t.strip(), re.I)
    h = int(m[1]) % 12 + (12 if m[3].upper() == "PM" else 0)
    return f"{h:02d}:{m[2]}"


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
        if title and shows:
            films.append(film(title=title, poster=og,
                              links=[{"label": "Evans Theatre", "url": EVANS + path}], shows=shows))
    return films


def fetch_all(now):
    """All Westman films: CinemaClock for the next week and MovieScout beyond, plus the Evans Theatre's season."""
    out = moviescout.region_films(now, CC_THEATRES, MS_THEATRES)
    try:
        ev = fetch_evans()
        print(f"  Evans Theatre: {len(ev)} films")
        out += ev
    except Exception as e:
        print(f"  ! Evans: {e}", file=sys.stderr)
    return out

