"""mk2 Cinesur Miramar (Fuengirola), straight from the cinema's own website.

CarteleraCines.es lags behind: new films (Whalefall, La invitación) can be on mk2's VOSE page days before the
aggregator has them. The VOSE page is plain server-rendered HTML with one block per day and every session on it in the
original version with Spanish subtitles, so it is read directly. One request per run.
"""
import html
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from sources import film, get, text  # noqa: E402

BASE = "https://www.mk2cines.es/"
URL = BASE + "miramar-fuengirola/es/miramar-fuengirola/vose"
CINEMA = "mk2 Miramar (Fuengirola)"

DAY = re.compile(r'<div class="rotulo_dia\s[^>]*>(.*?)</div>', re.S)
SESSION = re.compile(r'<a class="btn[^"]*"[^>]*href="([^"]+)"[^>]*>\s*<span>(.*?)</span>\s*(\d{1,2}:\d{2})\s*</a>', re.S)
INFINITY = "rotulo_sala_infinity"


def day_date(label, today):
    """"Hoy", "Mañana" or "Viernes 16/10" (no year) -> date."""
    label = text(label).lower()
    if label == "hoy":
        return today
    if label in ("mañana", "manana"):
        return today + timedelta(days=1)
    m = re.search(r"(\d{1,2})/(\d{1,2})", label)
    if not m:
        return None
    d = date(today.year, int(m[2]), int(m[1]))
    return d if d >= today - timedelta(days=60) else d.replace(year=today.year + 1)  # December page showing January


def parse(page, today):
    """Films with their sessions from the VOSE page's HTML. `today` is a date."""
    days = re.split(r'(?=<div class="rotulo_dia\s)', page)[1:]
    if not days:
        raise RuntimeError("mk2: no day blocks found; the page layout has changed")
    films = {}
    for block in days:
        m = DAY.match(block)
        day = day_date(m[1], today) if m else None
        if not day:
            continue
        for b in block.split('<div class="horarios">')[1:]:
            slug = re.search(r'href="https://www\.mk2cines\.es/es/miramar-fuengirola/([^"/?#]+)"', b)
            title = re.search(r"<b>(.*?)</b>", b, re.S)
            if not slug or not title:
                continue
            f = films.get(slug[1])
            if not f:
                director = re.search(r'<p class="gibsonL">de (.*?)</p>', b, re.S)
                runtime = re.search(r"(\d+)\s*minutos", b)
                poster = re.search(r'<img src="(data/fotos/[^"]+)"', b)
                f = films[slug[1]] = film(
                    title=text(title[1]), runtime=int(runtime[1]) if runtime else 0,
                    director=text(director[1]) if director else "",
                    poster=BASE + html.unescape(poster[1]) if poster else "",
                    links=[{"label": "mk2", "url": f"{BASE}es/miramar-fuengirola/{slug[1]}"}],
                )
            hours = b.split('<div class="horas', 1)[-1]
            infinity = hours.find(INFINITY)  # sessions after this marker are in the Infinity Vision room
            for s in SESSION.finditer(hours):
                f["shows"].append({
                    "t": f"{day.isoformat()}T{int(s[3].split(':')[0]):02d}:{s[3].split(':')[1]}",
                    "cinema": CINEMA, "screen": "Infinity Vision" if 0 <= infinity < s.start() else "",
                    "tags": ["Original language"], "note": "Spanish subtitles", "ticket": html.unescape(s[1]),
                    "status": "", "dub": False, "en": True,  # the whole page is VOSE
                })
    return list(films.values())


def fetch_all(now):
    films = parse(get(URL, headers={"Accept-Language": "es"}), now.date() if isinstance(now, datetime) else now)
    print(f"  {CINEMA} (mk2cines.es): {sum(len(f['shows']) for f in films)} VOSE sessions in {len(films)} films")
    return films
