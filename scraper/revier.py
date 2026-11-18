"""Revier Film Club (Revier hotel, Kongens gate 5, Oslo): free screenings every Wednesday and Friday at 18:00.

The schedule lives only on Eventbrite, one event per screening, named "Title (year, 1t 40m)", with the director as the
summary ("Regi: …"). Read through Eventbrite's API (a private token in EVENTBRITE_TOKEN): one request for all upcoming
events with their ticket availability. Eventbrite's terms rule out scraping its pages, so there is no fallback: without
a token, or when the API refuses, the run reports Revier as failed. Tickets are free but must be reserved, so a showing
links to its event page.
"""
import html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from sources import film, get  # noqa: E402

ORGANIZER_ID = "117564493841"
API = "https://www.eventbriteapi.com/v3"
CINEMA = "Revier Film Club"
STATUS = {"sold_out": "Fully booked", "sales_ended": "Booking closed", "unavailable": "Not available"}
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


def parse_name(name):
    """"Drive (2011, 1t 40m)" -> ("Drive", "2011", 100). Anything else is kept whole as the title."""
    m = re.match(r"^(.*?)\s*\((\d{4})(?:,\s*(?:(\d+)\s*t)?\s*(?:(\d+)\s*m)?)?\s*\)\s*$", name.strip())
    if not m:
        return name.strip(), "", 0
    return m[1], m[2], int(m[3] or 0) * 60 + int(m[4] or 0)


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
        q = {"status": "live", "order_by": "start_asc", "expand": "ticket_availability"}
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


def fetch_all(now):
    token = os.environ.get("EVENTBRITE_TOKEN")
    if not token:
        raise RuntimeError("no EVENTBRITE_TOKEN")
    try:
        return fetch_api(now, token)
    except urllib.error.HTTPError as e:
        # Eventbrite says what it objected to in the body ({"error": …, "error_description": …}).
        try:
            body = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            body = ""
        raise RuntimeError(f"Eventbrite API {e.code}: {body}") from e


if __name__ == "__main__":
    print(json.dumps(fetch_all(datetime.now()), ensure_ascii=False, indent=1))
