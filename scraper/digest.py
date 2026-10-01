#!/usr/bin/env python3
"""Email each subscriber the films that became bookable, and films newly announced with a
Norwegian date, since the last digest.

Usage:
  python3 scraper/digest.py --scheduled   # real run: only after 09:00 Oslo, once per day
  python3 scraper/digest.py --dry-run     # print what each subscriber would get; send nothing
  python3 scraper/digest.py --to ME@X.COM # send one test email (default settings); state untouched

Reads site/data/films.json and state/seen.json written by build.py.
Environment: SUPABASE_URL, SUPABASE_SECRET_KEY, GMAIL_USER, GMAIL_APP_PASSWORD, SITE_URL
"""
import html
import json
import os
import smtplib
import sys
import urllib.request
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state" / "seen.json"
DATA = ROOT / "site" / "data" / "films.json"
TZ = ZoneInfo("Europe/Oslo")
SEND_HOUR = 9
FMT = "%Y-%m-%dT%H:%M"
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def when(t):
    d = datetime.strptime(t, FMT)
    return f"{WEEKDAYS[d.weekday()]} {d.day} {MONTHS[d.month - 1]} {d:%H:%M}"


def title_of(f, prefs=None):
    """English title when we found a confident one (as on the site); Norwegian title in brackets."""
    en = (f.get("ext") or {}).get("en")
    if en:
        return f"{en} ({f['title']})"
    return f["title"]


def show_ok(s, prefs, now_s):
    """Same rules as the site's filters (site/app.js: showMatches)."""
    if not s["ticket"] or s["t"] < now_s:
        return False
    cinemas = prefs.get("cinemas") or []
    if cinemas and s["cinema"] not in cinemas:
        return False
    if prefs.get("hideDubbed") and s.get("dub"):
        return False
    if prefs.get("englishSubs") and not s.get("en"):
        return False
    return True


def announced_ok(f, prefs, now_s):
    """Announced films often have no showings yet; only filter on what we know."""
    if not f["shows"]:
        return True
    return any(show_ok({**s, "ticket": s["ticket"] or "-"}, prefs, now_s) for s in f["shows"])


def pick_announced(films, profile, now_s):
    prefs = profile.get("prefs") or {}
    if not prefs.get("announcements", True):
        return []
    watch = set(profile.get("watchlist") or [])
    out = [(f, bool(watch & set(f["ids"]))) for f in films if announced_ok(f, prefs, now_s)]
    return sorted(out, key=lambda x: (not x[1], x[0]["premiere"] or (x[0]["shows"][0]["t"] if x[0]["shows"] else "9999")))


def pick(new_films, profile, now_s):
    """Return [(film, shows, on_watchlist)] for one subscriber, watchlist films first."""
    prefs = profile.get("prefs") or {}
    watch = set(profile.get("watchlist") or [])
    always = prefs.get("watchlistAlways", True)
    out = []
    for f in new_films:
        watched = bool(watch & set(f["ids"]))
        shows = [s for s in f["shows"] if show_ok(s, prefs, now_s)]
        if not shows and watched and always:
            shows = [s for s in f["shows"] if s["ticket"] and s["t"] >= now_s]
        if shows:
            out.append((f, shows, watched))
    out.sort(key=lambda x: (not x[2], x[1][0]["t"]))
    return out


def announced_when(f):
    if f["shows"]:
        return f"Showings from {when(f['shows'][0]['t'])}, tickets not on sale yet"
    if f["premiere"]:
        d = datetime.strptime(f["premiere"], "%Y-%m-%d")
        if f.get("premiereConfirmed"):
            return f"Premiere {WEEKDAYS[d.weekday()]} {d.day} {MONTHS[d.month - 1]} {d.year}"
        return f"Expected {MONTHS[d.month - 1]} {d.year}"
    return "Date not announced"


def subject_for(items, announced, prefs):
    parts = []
    if items:
        parts.append(f"{len(items)} new on sale")
    if announced:
        parts.append(f"{len(announced)} newly announced")
    names = [title_of(f, prefs).split(" (")[0] for f, *_ in items] + [title_of(f, prefs).split(" (")[0] for f, _ in announced]
    return "Oslo cinemas: " + " · ".join(parts) + " — " + ", ".join(names[:3]) + (" …" if len(names) > 3 else "")


def render(items, announced, site, unsub_url, prefs=None):
    prefs = prefs or {}
    subject = subject_for(items, announced, prefs)
    txt, rows = (["NEW ON SALE", ""] if items else []), []
    for f, shows, watched in items:
        cinemas = list(dict.fromkeys(s["cinema"] for s in shows))
        link = f"{site}#film/{f['id']}"
        meta = " · ".join(x for x in [f["year"], f"{f['runtime']} min" if f["runtime"] else "", f["director"]] if x)
        en = any(s.get("en") for s in shows)
        line = f"{'★ ' if watched else ''}{title_of(f, prefs)}" + (f" — {meta}" if meta else "")
        txt += [line, f"  {', '.join(cinemas)} · {len(shows)} showing{'s' if len(shows) != 1 else ''} from {when(shows[0]['t'])}"
                + (" · English subtitles" if en else ""), f"  {link}", ""]
        next3 = "".join(
            f'<a href="{html.escape(s["ticket"])}" style="display:inline-block;margin:0 6px 6px 0;padding:4px 8px;'
            f'border:1px solid #d6dae1;border-radius:3px;color:#16181d;text-decoration:none;font-size:13px">'
            f'{html.escape(when(s["t"]))} · {html.escape(s["cinema"])}</a>' for s in shows[:3])
        poster = (f'<img src="{html.escape(f["poster"])}" width="72" height="108" alt="" '
                  f'style="display:block;width:72px;height:108px;object-fit:cover;border-radius:3px;background:#e2e5ea">'
                  if f["poster"] else "")
        rows.append(f"""
<tr><td style="padding:14px 14px 14px 0;vertical-align:top;width:72px">{poster}</td>
<td style="padding:14px 0;vertical-align:top;border-bottom:1px solid #e2e5ea">
  <div style="font-size:12px;color:#a3213a;font-weight:600">{"★ ON YOUR WATCHLIST" if watched else ""}</div>
  <a href="{html.escape(link)}" style="font-size:18px;font-weight:700;color:#16181d;text-decoration:none">{html.escape(title_of(f, prefs))}</a>
  <div style="font-size:13px;color:#5d6470;margin:2px 0 8px">{html.escape(meta)}</div>
  <div style="font-size:13px;margin-bottom:8px">{html.escape(", ".join(cinemas))} · {len(shows)} showing{"s" if len(shows) != 1 else ""}{" · <b>English subtitles</b>" if en else ""}</div>
  {next3}
  <div><a href="{html.escape(link)}" style="font-size:13px;color:#a3213a">All showings →</a></div>
</td></tr>""")
    arows = []
    if announced:
        txt += ["NEWLY ANNOUNCED", ""]
    for f, watched in announced:
        link = f"{site}#film/{f['id']}"
        meta = " · ".join(x for x in [f["year"], f"{f['runtime']} min" if f["runtime"] else ""] if x)
        txt += [f"{'★ ' if watched else ''}{title_of(f, prefs)}" + (f" — {meta}" if meta else ""), f"  {announced_when(f)}", f"  {link}", ""]
        arows.append(f"""
<tr><td style="padding:8px 0;border-bottom:1px solid #e2e5ea">
  <a href="{html.escape(link)}" style="font-size:15px;font-weight:600;color:#16181d;text-decoration:none">{"★ " if watched else ""}{html.escape(title_of(f, prefs))}</a>
  <span style="font-size:13px;color:#5d6470">{html.escape(" · " + meta if meta else "")}</span>
  <div style="font-size:13px;color:#5d6470">{html.escape(announced_when(f))}</div>
</td></tr>""")
    footer = f"Settings and watchlist: {site}\nUnsubscribe: {unsub_url}"
    body_html = f"""<!doctype html><html><body style="margin:0;padding:16px;background:#ffffff;font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#16181d">
<div style="max-width:600px;margin:0 auto">
{f'''<h1 style="font-size:22px;margin:0 0 4px">New on sale in Oslo</h1>
<p style="margin:0 0 8px;color:#5d6470;font-size:14px">Films whose tickets went on sale since the last email, filtered by your settings.</p>
<table role="presentation" cellspacing="0" cellpadding="0" style="width:100%;border-collapse:collapse">{"".join(rows)}</table>''' if rows else ""}
{f'''<h2 style="font-size:18px;margin:28px 0 4px">Newly announced</h2>
<p style="margin:0 0 4px;color:#5d6470;font-size:14px">Films that just got a Norwegian release date or showings. Star them on the site to have them highlighted when tickets go on sale.</p>
<table role="presentation" cellspacing="0" cellpadding="0" style="width:100%;border-collapse:collapse">{"".join(arows)}</table>''' if arows else ""}
<p style="font-size:12px;color:#5d6470;margin-top:24px">
<a href="{html.escape(site)}" style="color:#5d6470">Change settings or watchlist</a> ·
<a href="{html.escape(unsub_url)}" style="color:#5d6470">Unsubscribe</a></p>
</div></body></html>"""
    return subject, "\n".join(txt) + "\n" + footer, body_html


def subscribers():
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SECRET_KEY")
    if not (url and key):
        return None
    headers = {"apikey": key}
    if key.startswith("eyJ"):  # legacy service_role JWT
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(
        url.rstrip("/") + "/rest/v1/profiles?subscribed=eq.true&select=email,prefs,watchlist,unsubscribe_token",
        headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def send(messages):
    user, pw = os.environ["GMAIL_USER"], os.environ["GMAIL_APP_PASSWORD"]
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as smtp:
        smtp.login(user, pw)
        for to, subject, text, body_html in messages:
            msg = EmailMessage()
            msg["From"] = formataddr(("Kino by film", user))
            msg["To"] = to
            msg["Subject"] = subject
            msg.set_content(text)
            msg.add_alternative(body_html, subtype="html")
            smtp.send_message(msg)
            print(f"  sent to {to[:2]}…@{to.split('@')[-1]}")


def main():
    args = sys.argv[1:]
    dry = "--dry-run" in args
    test_to = args[args.index("--to") + 1] if "--to" in args else None
    site = os.environ.get("SITE_URL", "https://belacmu.github.io/kinoprogram/").rstrip("/") + "/"
    now = datetime.now(TZ).replace(tzinfo=None)
    now_s = now.strftime(FMT)
    state = json.loads(STATE.read_text())
    data = json.loads(DATA.read_text())

    if "--scheduled" in args:
        if now.hour < SEND_HOUR:
            print(f"Too early ({now:%H:%M} Oslo); digest goes out after {SEND_HOUR}:00.")
            return
        if state["lastDigest"][:10] >= now.strftime("%Y-%m-%d"):
            print("Digest already sent today.")
            return

    new = [f for f in data["films"] if f.get("onSaleSince") and f["onSaleSince"] > state["lastDigest"]]
    ann = [f for f in data["films"] if f["status"] == "announced"
           and f.get("announcedSince") and f["announcedSince"] > state["lastDigest"]]
    if test_to and not (new or ann):  # make the test email show something
        new = sorted((f for f in data["films"] if f["status"] == "on_sale"), key=lambda f: f["shows"][0]["t"])[:3]
        ann = [f for f in data["films"] if f["status"] == "announced" and f["premiere"]][:3]
    print(f"Since {state['lastDigest']}: {len(new)} newly on sale, {len(ann)} newly announced")

    if test_to:
        profiles = [{"email": test_to, "prefs": {}, "watchlist": [], "unsubscribe_token": "test"}]
    else:
        profiles = subscribers()
        if profiles is None:
            print("Supabase isn't configured (SUPABASE_URL / SUPABASE_SECRET_KEY); no emails sent.")
            profiles = []
    messages = []
    for p in profiles:
        items, announced = pick(new, p, now_s), pick_announced(ann, p, now_s)
        if not (items or announced):
            continue
        unsub = f"{site}?unsubscribe={p['unsubscribe_token']}"
        messages.append((p["email"], *render(items, announced, site, unsub, p.get("prefs") or {})))
    print(f"{len(profiles)} subscriber(s), {len(messages)} with something new")

    if dry:
        for to, subject, text, _ in messages:
            print(f"\n=== {to}\n{subject}\n\n{text}")
        return
    if messages:
        send(messages)
    if not test_to:
        state["lastDigest"] = now_s
        STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
