"""Deichman Bjørvika (Oslo's main library): its film screenings, from its Hoopla ticket shop.

The shop lists every event the library holds (embroidery, concerts, courses …); the film showings are the ones whose
category label is "Filmvisning" or "Film og samtale". School and kindergarten outdoor screenings ("Utekino for
barnehager/skolebarn") are for booked groups and are left out. Free, but a ticket is recommended; tickets are released
a week ahead (children's screenings the same morning), so an event with no ticket types yet is announced, not on sale.

Hoopla's public API is behind a queue-it redirect that only needs cookies kept, so requests use a cookie jar.
"""
import json
import re
import sys
import urllib.request
from datetime import datetime, timedelta
from http.cookiejar import CookieJar
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))
from sources import UA, film  # noqa: E402

SHOP = "https://deichman.hoopla.no"
API = f"{SHOP}/api/public/v3.0/organizations/915950447"
CINEMA = "Deichman Bjørvika"
FILM_LABELS = {"Filmvisning", "Film og samtale"}
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
OSLO = ZoneInfo("Europe/Oslo")

_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))


def api(path):
    req = urllib.request.Request(API + path, headers=UA)
    with _opener.open(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def local(utc):
    return datetime.fromisoformat(utc.replace("Z", "+00:00")).astimezone(OSLO).replace(tzinfo=None)


def parse_name(name):
    """"Barnas kino: Toy story 5. Lør kl. 14:00" -> "Toy story 5"; "Film fra Sør: X" -> "X". A "Film og samtale" or
    a night of several films keeps its whole name, and is returned as not a single film."""
    name = name.strip()
    name = re.sub(r"\.\s*(?:man|tir|ons|tor|fre|lør|søn)\w*\.?\s*kl\.?.*$", "", name, flags=re.I)
    m = re.match(r"^(Barnas kino|Film fra Sør|Filmvisning):\s*(.+)$", name)
    return (m[2].strip(), True) if m else (name, False)


def details(desc):
    """Year, running time and subtitles from the facts line: "Animasjon | 2026 | 1t 42min | 6 år | Norsk tale | …"."""
    m = re.search(r"\|\s*(\d{4})\s*\|\s*(?:(\d+)\s*t)?\s*(?:(\d+)\s*min)?", desc)
    year, runtime = (m[1], int(m[2] or 0) * 60 + int(m[3] or 0)) if m else ("", 0)
    return year, runtime, "Engelske undertekster" in desc


def release(start, desc):
    """When tickets open, from the event text ("én uke før", "samme dag kl. 08:00"); None when it doesn't say."""
    if re.search(r"én uke før|en uke før", desc):
        return start - timedelta(days=7)
    if re.search(r"samme dag", desc):
        return start
    return None


def fetch_all(now):
    events = [e for e in api("/events")["events"]
              if not e.get("is_cancelled") and (e["data"].get("other_category_description") or "") in FILM_LABELS]
    films = {}
    for e in events:
        d = api(f"/events/{e['event_id']}")["event"]
        desc = d.get("description") or ""
        start = local(e["start"])
        title, single = parse_name(e["name"])
        if not single and not re.search(r"film|kino", e["name"] + " " + (e["data"].get("location") or {}).get("name", ""), re.I):
            continue  # labelled a film showing, but a talk or reading
        year, runtime, en = details(desc) if single else ("", 0, False)
        key = (title.lower(), year) if single else e["event_id"]
        ticket_url = f"{SHOP}/event/{e['event_id']}"
        f = films.get(key)
        if not f:
            f = films[key] = film(title=title, year=year, runtime=runtime,
                                  blurb=re.sub(r"\s+", " ", (desc.split("OM FILMEN")[-1] if "OM FILMEN" in desc else desc)).strip()[:400],
                                  links=[{"label": "Deichman", "url": ticket_url}], kindHint="" if single else "talk")
        types = api(f"/events/{e['event_id']}/available_ticket_types")
        on_sale = any(g["ticket_types"] for g in types)
        sold_out = e.get("availability") == "SOLD_OUT"
        status = "Sold out" if sold_out else ""
        if not on_sale and not sold_out:
            at = release(start, desc)
            status = f"Free tickets from {at.day} {MONTHS[at.month - 1]}" if at else "Tickets not released yet"
        screen = re.split(r"[,.]", (e["data"].get("location") or {}).get("name", ""))[0].strip()
        f["shows"].append({
            "t": start.strftime("%Y-%m-%dT%H:%M"), "cinema": CINEMA, "screen": screen if screen != CINEMA else "",
            "tags": [], "note": "Free, ticket recommended" + (" · few left" if e.get("availability") == "FEW_LEFT" else ""),
            "ticket": ticket_url if on_sale and not sold_out else "", "status": status, "dub": False, "en": en,
        })
    return list(films.values())


if __name__ == "__main__":
    print(json.dumps(fetch_all(datetime.now()), ensure_ascii=False, indent=1))
