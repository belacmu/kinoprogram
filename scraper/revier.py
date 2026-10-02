"""Revier Film Club (Revier hotel, Kongens gate 5, Oslo): free screenings every Wednesday and Friday at 18:00.

The schedule lives only on Eventbrite, one event per screening, named "Title (year, 1t 40m)". The organizer page
embeds its upcoming events as JSON (`__NEXT_DATA__`); each event page adds the director ("Regi: …" as the summary),
a short Norwegian description and when tickets are released. Tickets are free but must be reserved, so a showing
links to its event page. A handful of requests per run.
"""
import html
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from sources import film, get, text  # noqa: E402

ORGANIZER = "https://www.eventbrite.com/o/revier-117564493841"
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
    """Director, description and ticket release date from the event page; empty if the page can't be read."""
    try:
        h = get(url)
        ev = next((d for d in map(json.loads, re.findall(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', h, re.S))
                   if d.get("@type") == "Event"), {})
        page = next_data(h)
    except Exception as e:
        print(f"  ! {url}: {e}", file=sys.stderr)
        return {}
    # The page props are nested differently from page to page; find the description and sales status wherever they are.
    found = {}

    def walk(o):
        if isinstance(o, dict):
            if "structuredContent" in o and "content" not in found:
                found["content"] = o["structuredContent"]
            if isinstance(o.get("salesStatus"), dict) and "sales" not in found:
                found["sales"] = o["salesStatus"]
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(page)
    blurb = " ".join(text(m.get("text")) for m in (found.get("content") or {}).get("modules") or [] if m.get("type") == "text")
    director = re.match(r"^\s*Regi:\s*(.+?)\s*$", html.unescape(ev.get("description") or ""))
    start = ((found.get("sales") or {}).get("startSalesDate") or {}).get("local") or ""
    return {"director": director[1] if director else "", "blurb": blurb.strip(), "image": ev.get("image") or "",
            "salesStart": start}


def fetch_all(now):
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
        title, year, runtime = parse_name(e["name"])
        details = event_details(e["url"])
        f = films.get((title.lower(), year))
        if not f:
            f = films[(title.lower(), year)] = film(
                title=title, year=year, runtime=runtime, director=details.get("director", ""),
                blurb=details.get("blurb", ""),
                poster=details.get("image") or ((e.get("image") or {}).get("image_sizes") or {}).get("medium", ""),
                links=[{"label": "Revier", "url": e["url"]}],
            )
        sales = (e.get("event_sales_status") or {}).get("sales_status") or ""
        ticket = e["url"] if sales == "on_sale" else ""
        status = STATUS.get(sales, "")
        start = details.get("salesStart", "")
        if not ticket and not status and start:
            status = f"Free tickets from {int(start[8:10])} {MONTHS[int(start[5:7]) - 1]}"
        f["shows"].append({
            "t": f"{e['start_date']}T{e['start_time'][:5]}", "cinema": CINEMA, "screen": "",
            "tags": [], "note": "Free, reserve a seat", "ticket": ticket, "status": status, "dub": False, "en": False,
        })
    return list(films.values())


if __name__ == "__main__":
    from datetime import datetime
    print(json.dumps(fetch_all(datetime.now()), ensure_ascii=False, indent=1))
