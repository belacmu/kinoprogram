"""Sommerro (hotel, Sommerrogata 1, Oslo): film evenings that come with a meal, from sommerrohouse.com/kultur/filmopplevelser.

Each screening is a package (dinner or brunch + film, prepaid), so every showing is tagged MEAL_TAG and says so in its
note: it costs far more than a cinema ticket. The programme is server-rendered HTML, one block per screening; booking
is a widget on the site, so a showing links to its series page. "(Utsolgt)" is sold out, "(Få plasser igjen)" almost.
"""
import html
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from sources import MEAL_TAG, film, get, text  # noqa: E402

PAGE = "https://www.sommerrohouse.com/kultur/filmopplevelser/"
CINEMA = "Sommerro"
MONTHS = {m: i + 1 for i, names in enumerate(
    ["jan january januar", "feb february februar", "mar march mars", "apr april", "may mai", "jun june juni", "jul july juli",
     "aug august", "sep sept september", "oct october oktober", "nov november", "dec december desember"]) for m in names.split()}


def parse_date(day, month, hhmm, now):
    """The page gives no year: the first such date from a month ago on."""
    d = datetime(now.year, MONTHS[month.lower()], int(day), int(hhmm[:2]), int(hhmm[3:]))
    return d.replace(year=now.year + 1) if (now - d).days > 30 else d


def package_blurb(url, cache):
    """The series page's "Pakken inkluderer …" sentence, e.g. what the dinner is."""
    if url not in cache:
        try:
            m = re.search(r"Pakken inkluderer[^.]*\.", text(get(url)))
            cache[url] = m[0] if m else ""
        except Exception as e:
            print(f"  ! {url}: {e}", file=sys.stderr)
            cache[url] = ""
    return cache[url]


def parse(h, now):
    start = h.find("events-listing-block--big")  # the programme; the carousel above it repeats the series
    blocks = re.split(r'<div class="events-listing-block__items__item\b', h[start:])[1:]
    out = []
    for b in blocks:
        link = re.search(r'<a href="([^"]+/events/[^"]+)"', b)
        series = re.search(r'__item__heading">(.*?)</span>', b, re.S)
        title = re.search(r'<p style="font-weight: 700;">(.*?)</p>', b, re.S)
        when = re.search(r"(\d{1,2})\.\s*([A-Za-zæøå]+)\s+kl\s*(\d{1,2}:\d{2})", b)
        if not (link and series and title and when) or when[2].lower() not in MONTHS:
            continue
        paras = re.findall(r"<p>(.*?)</p>", b, re.S)
        poster = re.search(r'data-src="([^"]+/wp-content/uploads/[^"]+)"', b)
        out.append({
            "series": text(series[1]), "url": link[1], "title": text(title[1]),
            "blurb": text(paras[0]) if paras else "", "poster": poster[1] if poster else "",
            "t": parse_date(when[1], when[2], when[3], now),
            "sold_out": "(Utsolgt)" in b, "few": "(Få plasser igjen)" in b,
            "place": text(re.search(r'__item__location">(.*?)</span>', b, re.S)[1]).lstrip("→ ") if "__item__location" in b else "",
        })
    return out


def fetch_all(now):
    entries = parse(get(PAGE), now)
    if not entries:
        raise RuntimeError("no screenings found on the Sommerro page; has its layout changed?")
    packages, films = {}, {}
    for e in entries:
        m = re.match(r"^(.*?)\s*\((\d{4})\)\s*$", e["title"])
        title, year = (m[1], m[2]) if m else (e["title"], "")
        # One link per film: the series page is shared by every film in it, so the film gets its own anchor.
        slug = re.sub(r"[^a-z0-9]+", "-", e["title"].lower()).strip("-")
        base = e["url"].split("#")[0]
        f = films.get(slug)
        if not f:
            f = films[slug] = film(title=title, year=year, blurb=e["blurb"], poster=e["poster"],
                                   links=[{"label": "Sommerro", "url": f"{base}#{slug}"}], series=[e["series"].title()])
        if e["series"].title() not in f["series"]:
            f["series"].append(e["series"].title())
        package = package_blurb(base, packages)
        note = "Meal + film package, prepaid" + (" · few seats left" if e["few"] and not e["sold_out"] else "")
        f["shows"].append({
            "t": e["t"].strftime("%Y-%m-%dT%H:%M"), "cinema": CINEMA, "screen": e["place"],
            "tags": [MEAL_TAG], "note": note, "ticket": "" if e["sold_out"] else base,
            "status": "Sold out" if e["sold_out"] else "", "dub": False, "en": False,
        })
        if package and package not in f["blurb"]:
            f["blurb"] = f"{package} {f['blurb']}".strip()
    return list(films.values())


if __name__ == "__main__":
    print(json.dumps(fetch_all(datetime.now()), ensure_ascii=False, indent=1))
