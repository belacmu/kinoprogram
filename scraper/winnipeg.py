"""Winnipeg sources.

- CinemaClock theatre pages: every cinema in the city, about the next week.
- MovieScout (used with MovieScout's permission, polled sparingly; see moviescout.py): beyond that week, the
  Cineplex cinemas' and Landmark Grant Park's advance sales months ahead. One fetch a day shared with Westman.
  Cinema City Northgate and Garden City (second-run) have no showtimes there; CinemaClock's week is all there is.
- Dave Barber Cinematheque: its own site (Filmbot) lists about a month ahead, with a ticket link per showing.
"""
import html
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import moviescout  # noqa: E402
from sources import film, get, split_year, text  # noqa: E402

CC_THEATRES = {
    "scotiabank-theatre-winnipeg": "Scotiabank Theatre",
    "cineplex-odeon-mcgillivray-vip": "Cineplex McGillivray",
    "cineplex-junxion-kildonan-place": "Cineplex Kildonan Place",
    "silvercity-st-vital": "SilverCity St. Vital",
    "landmark-grant-park": "Landmark Grant Park",
    "cinema-city-northgate": "Cinema City Northgate",
    "garden-city-cinemas": "Garden City Cinemas",
}
# MovieScout theatre id -> (our name, ticket/info link, note). The Cinematheque (14456) is left out: its own site
# has the same showings with ticket links.
MS_THEATRES = {
    6971: ("Scotiabank Theatre", "https://www.cineplex.com/theatre/scotiabank-theatre-winnipeg/", ""),
    9734: ("Cineplex McGillivray", "https://www.cineplex.com/theatre/cineplex-odeon-mcgillivray-and-vip/", ""),
    6207: ("Cineplex Kildonan Place", "https://www.cineplex.com/theatre/cineplex-junxion-kildonan-place/", ""),
    6205: ("SilverCity St. Vital", "https://www.cineplex.com/theatre/silvercity-st-vital-cinemas-xscape-entertainment-centre", ""),
    42876: ("Landmark Grant Park", "https://moviescout.ca/theatres/landmark-cinemas-8-grant-park-42876", ""),
    10411: ("Cinema City Northgate", "https://www.cineplex.com/theatre/cinema-city-northgate", ""),
    10000099: ("Garden City Cinemas", "https://www.gardencitycinemas.com/", ""),
}
CINEMATHEQUE = "https://davebarbercinematheque.com"
CINEMATHEQUE_NAME = "Cinematheque"


def fetch_cinematheque():
    """Films from the calendar and Coming soon pages, one page per film with its dates, times and ticket links."""
    paths = []
    for page in ("/", "/calendar/", "/coming-soon/"):
        paths += re.findall(r'href="https://davebarbercinematheque\.com(/movies/[^"/]+/)"', get(CINEMATHEQUE + page))
    films = []
    for path in dict.fromkeys(paths):
        p = get(CINEMATHEQUE + path)
        ld = re.search(r'<script type="application/ld\+json">(.*?)</script>', p, re.S)
        info = next((g for g in json.loads(ld[1]).get("@graph", []) if g.get("@type") == "Movie"), {}) if ld else {}
        spec = lambda label: text((re.search(rf'show-spec-label">{label}:</span>(.*?)</span>', p, re.S) or [None, ""])[1])
        title, year_in_title = split_year(html.unescape(info.get("name") or text(
            (re.search(r'<h2 class="show-title[^"]*">(.*?)</h2>', p, re.S) or [None, ""])[1])))
        shows = []
        # Each showing: <li data-date="<UTC midnight of its date>"> <a href=".../purchase/ID/" ...>7:15 pm</a>
        for day, ticket, clock in re.findall(r'<li data-date="(\d+)"[^>]*>\s*<a href="([^"]+)"[^>]*class="showtime[^"]*"[^>]*>\s*'
                                             r'(\d{1,2}:\d{2}\s*[ap]m)', p, re.I):
            d = datetime.fromtimestamp(int(day), timezone.utc).strftime("%Y-%m-%d")
            m = re.match(r"(\d{1,2}):(\d{2})\s*([ap])m", clock.strip(), re.I)
            h = int(m[1]) % 12 + (12 if m[3].lower() == "p" else 0)
            shows.append({"t": f"{d}T{h:02d}:{m[2]}", "cinema": CINEMATHEQUE_NAME, "screen": "", "tags": [], "note": "",
                          "ticket": ticket, "status": "", "dub": False,
                          "en": "english subtitles" in spec("Language").lower()})
        title = re.sub(r"\s+4K Restoration$", "", title, flags=re.I)  # "The Devils 4K Restoration"
        if not (title and shows):
            continue
        runtime = re.search(r"(\d+)", spec("Run Time"))
        films.append(film(
            title=title, year=year_in_title or spec("Release Year")[:4], runtime=int(runtime[1]) if runtime else 0,
            director=", ".join(d["name"] for d in info.get("director") or [] if d.get("name")) or spec("Director"),
            genres=info.get("genre") or [], blurb=html.unescape(info.get("description") or ""),
            poster=info.get("image") or (re.search(r'property="og:image" content="([^"]+)"', p) or [None, ""])[1],
            links=[{"label": "Cinematheque", "url": CINEMATHEQUE + path}], shows=shows,
        ))
    return films


def fetch_all(now):
    """All Winnipeg films: CinemaClock for the next week and MovieScout beyond, plus the Cinematheque's own site."""
    out = moviescout.region_films(now, CC_THEATRES, MS_THEATRES)
    try:
        cq = fetch_cinematheque()
        print(f"  Cinematheque: {len(cq)} films, {sum(len(f['shows']) for f in cq)} showings")
        out += cq
    except Exception as e:
        print(f"  ! Cinematheque: {e}", file=sys.stderr)
    return out
