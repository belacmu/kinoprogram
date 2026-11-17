"""Revier Film Club (Revier hotel, Kongens gate 5, Oslo): free screenings every Wednesday and Friday at 18:00.

The schedule lives only on Eventbrite, one event per screening, named "Title (year, 1t 40m)", with the director as the
summary ("Regi: …"). Read through Eventbrite's API (a private token in EVENTBRITE_TOKEN): one request for all upcoming
events with their ticket availability. Without a token, falls back to reading the public pages. Tickets are free but
must be reserved, so a showing links to its event page.
"""
import html
import json
import os
import re
import sys
import urllib.parse
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from sources import film, get  # noqa: E402

ORGANIZER_ID = "117564493841"
ORGANIZER = f"https://www.eventbrite.com/o/revier-{ORGANIZER_ID}"
API = "https://www.eventbriteapi.com/v3"
CINEMA = "Revier Film Club"
STATUS = {"sold_out": "Fully booked", "sales_ended": "Booking closed", "unavailable": "Not available"}
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


def next_data(h):
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', h, re.S)
    return json.loads(m[1])["props"]["pageProps"]


def parse_name(name):
    """"Drive (2011, 1t 40m)" -> ("Drive", "2011", 100). Anything else is kept whole as the title."""
    m = re.match(r"^(.*?)\s*\((\d{4})(?:,\s*(?:(\d+)\s*t)?\s*(?:(\d+)\s*m)?)?\s*\)\s*$", name.strip())
    if not m:
        return name.strip(), "", 0
    return m[1], m[2], int(m[3] or 0) * 60 + int(m[4] or 0)


def event_details(url):
    """Director, image and ticket release date from the event page; empty if the page can't be read."""
    try:
        h = get(url)
        ev = next((d for d in map(json.loads, re.findall(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', h, re.S))
                   if d.get("@type") == "Event"), {})
        page = next_data(h)
    except Exception as e:
        print(f"  ! {url}: {e}", file=sys.stderr)
        return {}
    # The page props are nested differently from page to page; find the sales status wherever it is.
    found = {}

    def walk(o):
        if isinstance(o, dict):
            if isinstance(o.get("salesStatus"), dict) and "sales" not in found:
                found["sales"] = o["salesStatus"]
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(page)
    director = re.match(r"^\s*Regi:\s*(.+?)\s*$", html.unescape(ev.get("description") or ""))
    start = ((found.get("sales") or {}).get("startSalesDate") or {}).get("local") or ""
    return {"director": director[1] if director else "", "image": ev.get("image") or "",
            "salesStart": start}


def show(f, when, url, ticket, status):
    f["shows"].append({
        "t": when, "cinema": CINEMA, "screen": "", "tags": [], "note": "Free, reserve a seat",
        "ticket": ticket, "status": status, "dub": False, "en": False,
    })


def film_for(films, name, director, poster, url):
    title, year, runtime = parse_name(name)
    f = films.get((title.lower(), year))
    if not f:
        f = films[(title.lower(), year)] = film(title=title, year=year, runtime=runtime, director=director, poster=poster,
                                                links=[{"label": "Revier", "url": url}])
    return f


def sales_from(start):
    return f"Free tickets from {int(start[8:10])} {MONTHS[int(start[5:7]) - 1]}"


def fetch_api(now, token):
    """Upcoming events from Eventbrite's API, with their ticket availability."""
    films, continuation = {}, ""
    stamp = now.strftime("%Y-%m-%dT%H:%M:%S")
    while True:
        q = {"status": "live", "order_by": "start_asc", "expand": "ticket_availability", "page_size": 50}
        if continuation:
            q["continuation"] = continuation
        data = json.loads(get(f"{API}/organizers/{ORGANIZER_ID}/events/?{urllib.parse.urlencode(q)}",
                              headers={"Authorization": f"Bearer {token}"}))
        for e in data.get("events") or []:
            if e.get("online_event") or e.get("status") != "live":
                continue
            director = re.match(r"^\s*Regi:\s*(.+?)\s*$", html.unescape(e.get("summary") or ""))
            logo = e.get("logo") or {}
            f = film_for(films, (e.get("name") or {}).get("text") or "", director[1] if director else "",
                         (logo.get("original") or {}).get("url") or logo.get("url") or "", e["url"])
            ta = e.get("ticket_availability") or {}
            start = ((ta.get("start_sales_date") or {}).get("local") or "")
            end = ((ta.get("end_sales_date") or {}).get("local") or "")
            ticket, status = "", ""
            if ta.get("has_available_tickets"):
                ticket = e["url"]
            elif ta.get("is_sold_out"):
                status = STATUS["sold_out"]
            elif start > stamp:
                status = sales_from(start)
            elif end and end < stamp:
                status = STATUS["sales_ended"]
            else:
                status = STATUS["unavailable"]
            show(f, e["start"]["local"][:16], e["url"], ticket, status)
        page = data.get("pagination") or {}
        continuation = page.get("continuation") or ""
        if not (page.get("has_more_items") and continuation):
            return list(films.values())


def fetch_pages(now):
    """Fallback without an API token: the organizer page's embedded events, and each event page for the director and
    the ticket release date."""
    page = next_data(get(ORGANIZER))
    if page.get("upcomingEventsFailed"):
        raise RuntimeError("Eventbrite could not load the upcoming events")
    events = page.get("upcomingEvents") or []
    if page.get("hasMoreUpcoming"):
        print(f"  ! Revier: only {len(events)} of {page.get('upcomingEventsTotal')} upcoming events on the page", file=sys.stderr)
    films = {}
    for e in events:
        if e.get("is_cancelled") or e.get("is_online_event"):
            continue
        details = event_details(e["url"])
        f = film_for(films, e["name"], details.get("director", ""),
                     details.get("image") or ((e.get("image") or {}).get("image_sizes") or {}).get("medium", ""), e["url"])
        sales = (e.get("event_sales_status") or {}).get("sales_status") or ""
        ticket = e["url"] if sales == "on_sale" else ""
        status = STATUS.get(sales, "")
        start = details.get("salesStart", "")
        if not ticket and not status and start:
            status = sales_from(start)
        show(f, f"{e['start_date']}T{e['start_time'][:5]}", e["url"], ticket, status)
    return list(films.values())


def fetch_all(now):
    token = os.environ.get("EVENTBRITE_TOKEN")
    if token:
        return fetch_api(now, token)
    print("  ! Revier: no EVENTBRITE_TOKEN, reading the public pages instead", file=sys.stderr)
    return fetch_pages(now)


if __name__ == "__main__":
    print(json.dumps(fetch_all(datetime.now()), ensure_ascii=False, indent=1))
