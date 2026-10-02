"""CinemaClock theatre pages (server-rendered, about a week ahead), used for the Manitoba regions.

Small theatres sell at the door: their showings link the theatre page, so they still count as on sale.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from sources import film, get, text  # noqa: E402

CC = "https://www.cinemaclock.com"
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def _cc_date(label, earliest):
    """'Oct 2' + data-earliest-date '20261001' -> '2026-10-02' (handles the year rollover)."""
    mon, day = label.split()
    m = MONTHS[mon[:3].lower()]
    y = int(earliest[:4]) + (1 if m < int(earliest[4:6]) else 0)
    return f"{y}-{m:02d}-{int(day):02d}"


def fetch_theatre(slug, cinema):
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
