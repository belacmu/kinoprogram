#!/usr/bin/env python3
"""Email each subscriber the films that became bookable, and films newly announced with a
Norwegian date, since the last digest. Watchlist films get their own section at the top, with
a status line (on sale / announced / leaving soon). A watchlist notice alone never triggers an email.

Usage:
  python3 scraper/digest.py --scheduled [--only "oslo westman"]  # real run: per region, only after 09:00 local time, once per day
  python3 scraper/digest.py --dry-run     # print what each subscriber would get; send nothing
  python3 scraper/digest.py --to ME@X.COM # send one test email (default settings); state untouched
  python3 scraper/digest.py --preview OUT.html  # write a sample email (demo watchlist) to a file; send nothing

Reads each region's data and state files written by build.py (see build.REGIONS). A subscriber
gets one email per region they chose (prefs.regions, default ["oslo"]).
Environment: SUPABASE_URL, SUPABASE_SECRET_KEY, GMAIL_USER, GMAIL_APP_PASSWORD, SITE_URL
"""
import html
import json
import os
import smtplib
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).parent))
from build import REGIONS  # noqa: E402
from costadelsol import LANG_NAMES  # noqa: E402
SEND_HOUR = 9
WEEKLY_DAY = 4  # weekly emails go out on Fridays (Monday is 0)
DEFAULT_HIDE_KINDS = ["short", "stage", "talk"]  # same default as the site: films only
FMT = "%Y-%m-%dT%H:%M"
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

e = html.escape


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
    cinemas = prefs.get("_cinemas") or []  # the subscriber's cinemas in this region (set in main)
    if cinemas and s["cinema"] not in cinemas:
        return False
    if prefs.get("hideDubbed") and s.get("dub"):
        return False
    if prefs.get("englishSubs") and not s.get("en"):
        return False
    if prefs.get("_audioEnNo") and s.get("lang") not in ("en", "no", "nb"):  # Costa del Sol: no Spanish needed
        return False
    return True


def announced_ok(f, prefs, now_s):
    """Announced films often have no showings yet; only filter on what we know."""
    if not f["shows"]:
        return True
    return any(show_ok({**s, "ticket": s["ticket"] or "-"}, prefs, now_s) for s in f["shows"])


def is_hidden(f, prefs, watch):
    """Hidden on the site (prefs.hidden: film id -> onSaleSince when hidden), unless it is on the watchlist or has since
    come back after going off sale (a newer onSaleSince), the same rule as the site."""
    hidden = prefs.get("hidden") or {}
    if watch & set(f["ids"]):
        return False
    since = next((hidden[i] for i in f["ids"] if i in hidden), None)
    if since is None:
        return False
    return not (since and f.get("onSaleSince") and f["onSaleSince"] > since)


def pick_announced(films, profile, now_s):
    prefs = profile.get("prefs") or {}
    if not prefs.get("announcements", True):
        return []
    watch = set(profile.get("watchlist") or [])
    hide_kinds = set(prefs["hideKinds"] if "hideKinds" in prefs else DEFAULT_HIDE_KINDS)
    out = [(f, bool(watch & set(f["ids"]))) for f in films if announced_ok(f, prefs, now_s) and not is_hidden(f, prefs, watch)
           and (f.get("kind", "film") not in hide_kinds or watch & set(f["ids"]))]
    return sorted(out, key=lambda x: (not x[1], x[0]["premiere"] or (x[0]["shows"][0]["t"] if x[0]["shows"] else "9999")))


def pick(new_films, profile, now_s):
    """Return [(film, shows, on_watchlist)] for one subscriber, watchlist films first."""
    prefs = profile.get("prefs") or {}
    watch = set(profile.get("watchlist") or [])
    always = prefs.get("watchlistAlways", True)
    out = []
    hide_kinds = set(prefs["hideKinds"] if "hideKinds" in prefs else DEFAULT_HIDE_KINDS)
    for f in new_films:
        watched = bool(watch & set(f["ids"]))
        if is_hidden(f, prefs, watch):
            continue
        if f.get("kind", "film") in hide_kinds and not watched:
            continue
        shows = [s for s in f["shows"] if show_ok(s, prefs, now_s)]
        if not shows and watched and always:
            shows = [s for s in f["shows"] if s["ticket"] and s["t"] >= now_s]
        if shows:
            out.append((f, shows, watched))
    out.sort(key=lambda x: (not x[2], x[1][0]["t"]))
    return out


def pick_leaving(films, profile, now_s, skip_ids):
    """Watchlist films showing "Last chance" (build.mark_last_chance): [(film, shows_left, last_show)]."""
    prefs = profile.get("prefs") or {}
    watch = set(profile.get("watchlist") or [])
    out = []
    for f in films:
        if f["id"] in skip_ids or not watch & set(f["ids"]) or not f.get("lastChance") or f["lastChance"] > now_s:
            continue
        shows = sorted((s for s in f["shows"] if s["ticket"] and s["t"] >= now_s), key=lambda s: s["t"])
        mine = [s for s in shows if show_ok(s, prefs, now_s)]
        shows = mine or (shows if prefs.get("watchlistAlways", True) else [])
        if shows:
            out.append((f, shows, shows[-1]))
    out.sort(key=lambda x: x[2]["t"])
    return out


def announced_when(f):
    if f["shows"]:
        statuses = {s["status"] for s in f["shows"]}  # all sold out, say, rather than not on sale yet
        status = statuses.pop() if len(statuses) == 1 else ""
        status = status[0].lower() + status[1:] if status else "tickets not on sale yet"
        return f"Showings from {when(f['shows'][0]['t'])}, {status}"
    if f["premiere"] and f.get("scope") == "Canada":
        d = datetime.strptime(f["premiere"], "%Y-%m-%d")
        return f"Opens in Canada {WEEKDAYS[d.weekday()]} {d.day} {MONTHS[d.month - 1]} (not scheduled here yet)"
    if f["premiere"]:
        d = datetime.strptime(f["premiere"], "%Y-%m-%d")
        if f.get("premiereConfirmed"):
            return f"Premiere {WEEKDAYS[d.weekday()]} {d.day} {MONTHS[d.month - 1]} {d.year}"
        return f"Expected {MONTHS[d.month - 1]} {d.year}"
    return "Date not announced"


def cinemas_of(shows):
    """'Vega Kino, Saga +3' - first two cinemas, then a count, so lines stay short."""
    names = list(dict.fromkeys(s["cinema"] for s in shows))
    return ", ".join(names[:2]) + (f" +{len(names) - 2}" if len(names) > 2 else "")


def plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def lang_note(shows, rkey):
    """Language note for a film's showings: English subtitles in Oslo, original language in Costa del Sol."""
    if not any(s.get("en") for s in shows):
        return ""
    if rkey == "costadelsol":
        names = sorted({LANG_NAMES.get(s.get("lang"), "") for s in shows if s.get("en")} - {""})
        return f" · {' / '.join(names)} audio, Spanish subtitles" if names else " · Original language, Spanish subtitles"
    return " · English subtitles"


def watch_entries(items, announced, leaving, rkey="oslo"):
    """The watchlist section: [(film, kind, detail)] with kind 'sale' | 'announced' | 'leaving'."""
    out = []
    for f, shows, watched in items:
        if watched:
            cinemas = cinemas_of(shows)
            en = lang_note(shows, rkey)
            out.append((f, "sale", f"{cinemas} · {plural(len(shows), 'showing')} from {when(shows[0]['t'])}{en}"))
    for f, watched in announced:
        if watched:
            out.append((f, "announced", announced_when(f)))
    for f, shows, last in leaving:
        out.append((f, "leaving", f"{plural(len(shows), 'showing')} left, {last['cinema']}, last {when(last['t'])}"))
    return out


def subject_for(items, announced, leaving, prefs, region="Oslo"):
    short = lambda f: title_of(f, prefs).split(" (")[0]
    marked = [(f, "sale") for f, _, w in items if w] + [(f, "announced") for f, w in announced if w]
    others = [f for f, _, w in items if not w] + [f for f, w in announced if not w]
    if marked:
        if len(marked) == 1:
            f, kind = marked[0]
            head = f"♥ {short(f)} " + ("is on sale" if kind == "sale" else "was announced")
        else:
            head = f"♥ {short(marked[0][0])}, {short(marked[1][0])}" + (f" and {len(marked) - 2} more" if len(marked) > 2 else "") + " on your watchlist"
        tail = f" · {len(others)} more new" if others else ""
        return f"{head}{tail}"
    parts = []
    if items:
        parts.append(f"{len(items)} new on sale")
    if announced:
        parts.append(f"{len(announced)} newly announced")
    names = [short(f) for f in others]
    return " · ".join(parts) + " — " + ", ".join(names[:3]) + (" …" if len(names) > 3 else "")


# ---- HTML pieces. Email has no CSS grid: cards are inline-blocks (3 across, 2 on phones). ----

STYLE = """<style>
@media (max-width:599px){.c{width:170px!important}.pt{height:237px!important}.pm{height:169px!important}.pad{padding-left:10px!important;padding-right:10px!important}}
@media (prefers-color-scheme:dark){
.page{background:#101216!important}.wrap{background:#191c22!important}
.ink,.ink a{color:#e9ebef!important}.mut{color:#9097a3!important}.line{border-color:#2b2f37!important}
.acc{color:#f0647d!important}.grn{color:#6fd198!important}.ph{background:#23272e!important}}
</style>"""

# The site's heading font. Apple Mail / iOS Mail load it from Google Fonts; Gmail can't, and falls back to a narrow system font.
DISPLAY = "'Big Shoulders Display','Roboto Condensed','Arial Narrow',Arial,sans-serif"
FONT_LINK = '<link href="https://fonts.googleapis.com/css2?family=Big+Shoulders+Display:wght@700;800&display=swap" rel="stylesheet">'
KIND_LABEL = {"sale": ("Tickets on sale", "grn", "#1f7a45"), "announced": ("Newly announced", "mut", "#5d6470"),
              "leaving": ("Last chance", "acc", "#a3213a")}


def rating_of(f):
    r = (f.get("ext") or {}).get("lbRating")
    return f"★ {r:.1f}" if r else ""


def poster_html(f, width, dim=False):
    """A 2:3 poster. Stills and odd shapes are cropped to fit; no poster gets a grey block with the title."""
    height = int(width * 1.5)
    op = "opacity:.45;" if dim else ""
    if f["poster"]:
        return (f'<img src="{e(f["poster"])}" width="{width}" height="{height}" alt="" '
                f'style="display:block;width:100%;height:{height}px;object-fit:cover;aspect-ratio:2/3;border-radius:6px;background:#e2e5ea;{op}">')
    return (f'<div class="ph" style="height:{height}px;overflow:hidden;border-radius:6px;background:#e2e5ea;{op}">'
            f'<div style="padding:12px;font-size:{13 if width > 100 else 10}px;font-weight:700;color:#5d6470;text-align:center">{e(f["title"])}</div></div>')


def section_head(title, count, sub=""):
    return (f'<h2 class="ink" style="margin:0 0 4px;font:800 22px/1.1 {DISPLAY};text-transform:uppercase;letter-spacing:.03em;color:#16181d">'
            f'{e(title)} <span class="mut" style="font-size:15px;color:#5d6470">{count}</span></h2>'
            + (f'<p class="mut" style="margin:0 0 14px;font-size:13px;color:#5d6470">{e(sub)}</p>' if sub else '<div style="height:10px"></div>'))


def grid_html(cards):
    cells = "".join(f'<div class="c" style="display:inline-block;vertical-align:top;width:186px;font-size:14px;line-height:1.3;text-align:left"><div style="padding:0 6px 16px">{c}</div></div>'
                    for c in cards)
    return f'<div style="text-align:center;font-size:0;line-height:0">{cells}</div>'


ASSET_BASE = ""  # set by render(): where site/email/*.png is published


def asset_url(name):
    return f"{ASSET_BASE}email/{name}"


# Cards have a fixed pixel size so a poster is always exactly 2:3: 186px wide (3 across the 600px email) on desktop,
# 170px wide (2 across even a 360px phone) below 600px, where the .pt/.pm classes in STYLE switch the heights.
POSTER_H = 261   # 174px poster
POSTER_H_SMALL = 237  # 158px poster


def poster_card(f, link, heart_link, dim=False, watched=False):
    """Poster as a cell background so the heart (top right) and rating (bottom right) can sit on it, as on the site."""
    heart = (f'<a href="{e(heart_link)}" title="{"On your watchlist" if watched else "Add to watchlist"}" style="text-decoration:none"><img src="{e(asset_url("heart-on.png" if watched else "heart.png"))}" '
             f'width="32" height="32" alt="{"♥" if watched else "♡"}" style="display:block;width:32px;height:32px;border:0"></a>')
    r = (f.get("ext") or {}).get("lbRating")
    rating = (f'<span style="display:inline-block;background:rgba(10,12,16,.72);color:#ffffff;font-size:12px;font-weight:700;line-height:1;'
              f'padding:4px 7px;border-radius:12px">{r:.1f}<span style="color:#f2b84b;font-size:11px"> ★</span></span>') if r else ""
    shade = "linear-gradient(rgba(120,124,132,.6),rgba(120,124,132,.6)),url(" if dim else "url("
    img = e(f["poster"]) if f["poster"] else ""
    bg = f"background-color:#e2e5ea;background-image:{shade}{img});background-size:cover;background-position:center;" if img else "background-color:#e2e5ea;"
    middle = (f'<a href="{e(link)}" class="pm" style="display:block;height:{POSTER_H - 68}px;font-size:0;line-height:0;text-decoration:none">&nbsp;</a>' if img else
              f'<a class="mut pm" href="{e(link)}" style="display:block;height:{POSTER_H - 68}px;padding:0 10px;font-size:13px;font-weight:700;line-height:1.2;color:#5d6470;text-decoration:none;overflow:hidden">{e(f["title"])}</a>')
    return (f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border-collapse:separate;width:100%"><tr>'
            f'<td class="pt"{f" background={chr(34)}{img}{chr(34)}" if img else ""} height="{POSTER_H}" valign="top" style="height:{POSTER_H}px;border-radius:6px;overflow:hidden;{bg}">'
            f'<table class="pt" role="presentation" width="100%" height="{POSTER_H}" cellspacing="0" cellpadding="0" style="width:100%;height:{POSTER_H}px">'
            f'<tr><td align="right" valign="top" style="padding:6px 6px 0 0;height:32px">{heart}</td></tr>'
            f'<tr><td valign="top">{middle}</td></tr>'
            f'<tr><td align="right" valign="bottom" style="padding:0 6px 6px 0;height:24px">{rating}</td></tr></table></td></tr></table>')


def card_html(f, link, lines, dim=False, heart_link=None, watched=False):
    body = "".join(f'<div class="mut" style="font-size:12px;line-height:1.4;color:#5d6470">{e(x)}</div>' for x in lines if x)
    return (poster_card(f, link, link if watched else (heart_link or link), dim, watched) +
            f'<a class="ink" href="{e(link)}" style="display:block;margin:8px 0 3px;font:700 17px/1.05 {DISPLAY};text-transform:uppercase;letter-spacing:.01em;color:#16181d;text-decoration:none">{e(title_of(f))}</a>'
            + body)


def watch_row_html(f, kind, detail, link):
    label, cls, color = KIND_LABEL[kind]
    meta = " · ".join(x for x in [f["year"], f"{f['runtime']} min" if f["runtime"] else "", rating_of(f)] if x)
    return f"""<tr>
<td style="width:64px;padding:0 14px 14px 0;vertical-align:top"><a href="{e(link)}" style="text-decoration:none">{poster_html(f, 64, kind == 'announced')}</a></td>
<td style="padding:0 0 14px;vertical-align:top">
  <div class="{cls}" style="font-size:11px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:{color}">♥ {label}</div>
  <a class="ink" href="{e(link)}" style="font-size:16px;font-weight:700;line-height:1.25;color:#16181d;text-decoration:none">{e(title_of(f))}</a>
  <div class="mut" style="font-size:12px;color:#5d6470;margin:1px 0 4px">{e(meta)}</div>
  <div class="ink" style="font-size:13px;color:#16181d">{e(detail)}</div>
</td></tr>"""


def render(items, announced, leaving, site, unsub_url, prefs=None, region="Oslo", rkey="oslo"):
    global ASSET_BASE
    prefs = prefs or {}
    ASSET_BASE = site
    subject = subject_for(items, announced, leaving, prefs, region)
    site = site if rkey == "oslo" else f"{site}?r={rkey}"
    link_of = lambda f: f"{site}#film/{f['id']}"
    watch_of = lambda f: f"{site}#watch/{f['id']}"  # the site adds it to the watchlist, then opens the film
    watching = watch_entries(items, announced, leaving, rkey)
    sale = [(f, shows, w) for f, shows, w in items]       # watchlist films appear here too, with a filled heart
    ann = [(f, w) for f, w in announced]

    txt = []
    if watching:
        txt += ["♥ ON YOUR WATCHLIST", ""]
        for f, kind, detail in watching:
            txt += [f"{title_of(f)} — {KIND_LABEL[kind][0]}", f"  {detail}", f"  {link_of(f)}", ""]
    if sale:
        txt += ["NEW ON SALE", ""]
        for f, shows, w in sale:
            cinemas = cinemas_of(shows)
            txt += [f"{'♥ ' if w else ''}{title_of(f)}" + (f" — {rating_of(f)}" if rating_of(f) else ""),
                    f"  {cinemas} · {plural(len(shows), 'showing')} from {when(shows[0]['t'])}", f"  {link_of(f)}", ""]
    if ann:
        txt += ["NEWLY ANNOUNCED", ""]
        for f, w in ann:
            txt += [f"{'♥ ' if w else ''}{title_of(f)}", f"  {announced_when(f)}", f"  {link_of(f)}", ""]

    blocks = []
    if watching:
        rows = "".join(watch_row_html(f, k, d, link_of(f)) for f, k, d in watching)
        blocks.append(section_head("On your watchlist", len(watching)) +
                      f'<table role="presentation" cellspacing="0" cellpadding="0" style="width:100%;border-collapse:collapse">{rows}</table>')
    if sale:
        cards = []
        for f, shows, w in sale:
            cinemas = cinemas_of(shows)
            en = lang_note(shows, rkey)
            cards.append(card_html(f, link_of(f), [
                cinemas, f"{when(shows[0]['t'])} · {plural(len(shows), 'showing')}{en}"], heart_link=watch_of(f), watched=w))
        blocks.append(section_head("New on sale", len(sale), "Tickets went on sale since the last email.") + grid_html(cards))
    if ann:
        cards = [card_html(f, link_of(f), [announced_when(f)], dim=True, heart_link=watch_of(f), watched=w) for f, w in ann]
        blocks.append(section_head("Newly announced", len(ann), "Just got a date. Tap ♡ to add it to your watchlist.") + grid_html(cards))

    footer = f"Settings and watchlist: {site}\nUnsubscribe: {unsub_url}"
    sep = '<div class="line" style="border-top:1px solid #e2e5ea;margin:8px 0 22px"></div>'
    body_html = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark"><meta name="supported-color-schemes" content="light dark">{FONT_LINK}{STYLE}</head>
<body class="page" style="margin:0;padding:0;background:#eef0f2;font-family:-apple-system,'Segoe UI',Helvetica,Arial,sans-serif;color:#16181d">
<div class="page" style="background:#eef0f2;padding:16px 0">
<div class="wrap" style="max-width:600px;margin:0 auto;background:#ffffff;border-radius:10px;overflow:hidden">
  <div style="background:#0a0c0f;padding:18px 20px;color:#ffffff">
    <img src="{e(asset_url("cinecrab-wordmark.png"))}" width="112" height="28" alt="Cinecrab" style="display:inline-block;border:0;width:112px;height:28px;vertical-align:middle">
    <span style="float:right;font-size:13px;line-height:28px;color:#9097a3">{e(region)}</span>
  </div>
  <div class="pad" style="padding:22px 20px 8px">
    {sep.join(blocks)}
  </div>
  <div class="pad" style="padding:6px 20px 22px;font-size:12px;color:#5d6470">
    <a class="mut" href="{e(site)}" style="color:#5d6470">Open Cinecrab</a> ·
    <a class="mut" href="{e(site)}" style="color:#5d6470">Settings and watchlist</a> ·
    <a class="mut" href="{e(unsub_url)}" style="color:#5d6470">Unsubscribe</a>
  </div>
</div></div></body></html>"""
    return subject, "\n".join(txt) + "\n" + footer, body_html


def fetch_profiles(filter_):
    """Rows from the profiles table (needs SUPABASE_URL and SUPABASE_SECRET_KEY); None if not configured."""
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SECRET_KEY")
    if not (url and key):
        return None
    headers = {"apikey": key}
    if key.startswith("eyJ"):  # legacy service_role JWT
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(
        url.rstrip("/") + f"/rest/v1/profiles?{filter_}&select=email,subscribed,prefs,watchlist,unsubscribe_token",
        headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def write_stats():
    """Counts only (no addresses): printed, and shown on the run's summary page in GitHub (Actions)."""
    rows = fetch_profiles("id=not.is.null")
    if rows is None:
        return
    subs = [r for r in rows if r.get("subscribed")]
    freq = lambda r: "weekly" if (r.get("prefs") or {}).get("frequency") == "weekly" else "daily"
    regions = lambda r: (r.get("prefs") or {}).get("regions") or ["oslo"]
    lines = [("Accounts", len(rows)), ("Subscribed to the email", len(subs)),
             ("  daily", sum(freq(r) == "daily" for r in subs)), ("  weekly", sum(freq(r) == "weekly" for r in subs))]
    lines += [(f"  getting {cfg['name']}", sum(k in regions(r) for r in subs)) for k, cfg in REGIONS.items()]
    lines.append(("With a watchlist", sum(bool(r.get("watchlist")) for r in rows)))
    print("Subscribers: " + ", ".join(f"{k.strip()} {v}" for k, v in lines))
    out = os.environ.get("GITHUB_STEP_SUMMARY")
    if out:
        with open(out, "a") as f:
            f.write("### Cinecrab subscribers\n\n| | |\n|---|---|\n" + "".join(f"| {k.replace('  ', '&nbsp;&nbsp;')} | {v} |\n" for k, v in lines))


def subscribers():
    return fetch_profiles("subscribed=eq.true")


def profile_for(email):
    """The saved profile for this address (subscribed or not), so a demo email is built exactly like a real one."""
    rows = fetch_profiles("email=eq." + urllib.parse.quote(email, safe="")) or []
    return rows[0] if rows else None


def send(messages):
    user, pw = os.environ["GMAIL_USER"], os.environ["GMAIL_APP_PASSWORD"]
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as smtp:
        smtp.login(user, pw)
        for to, subject, text, body_html in messages:
            msg = EmailMessage()
            msg["From"] = formataddr(("Cinecrab", user))
            msg["To"] = to
            msg["Subject"] = subject
            msg.set_content(text)
            msg.add_alternative(body_html, subtype="html")
            smtp.send_message(msg)
            print(f"  sent to {to[:2]}…@{to.split('@')[-1]}")


def main():
    args = sys.argv[1:]
    dry = "--dry-run" in args
    scheduled = "--scheduled" in args
    test_to = args[args.index("--to") + 1] if "--to" in args else None
    preview = args[args.index("--preview") + 1] if "--preview" in args else None
    site = os.environ.get("SITE_URL", "https://belacmu.github.io/kinoprogram/").rstrip("/") + "/"
    only = args[args.index("--only") + 1].split() if "--only" in args else list(REGIONS)  # regions refreshed in this run
    if scheduled or "--stats" in args:
        try:
            write_stats()
        except Exception as e:  # counting must never stop the emails
            print(f"  ! stats failed: {e}", file=sys.stderr)
    profiles = None
    for rkey, cfg in REGIONS.items():
        if rkey not in only:
            continue
        state_path, data_path = ROOT / "state" / cfg["state"], ROOT / "site" / "data" / cfg["data"]
        if not (state_path.exists() and data_path.exists()):
            continue
        now = datetime.now(ZoneInfo(cfg["tz"])).replace(tzinfo=None)
        now_s, today = now.strftime(FMT), now.strftime("%Y-%m-%d")
        state, data = json.loads(state_path.read_text()), json.loads(data_path.read_text())
        print(f"== {cfg['name']}")
        # Daily subscribers get what is new since the last daily email; weekly subscribers get what is new
        # since the last weekly one, on Fridays. A test or preview is one email built like a daily one.
        modes = [("test", "lastDigest")] if (test_to or preview) else [("daily", "lastDigest"), ("weekly", "lastWeekly")]
        for mode, key in modes:
            if scheduled:
                if now.hour < SEND_HOUR:
                    print(f"{mode}: too early ({now:%H:%M} local); goes out after {SEND_HOUR}:00.")
                    continue
                if mode == "weekly" and now.weekday() != WEEKLY_DAY:
                    print("weekly: goes out on Fridays.")
                    continue
                if (state.get(key) or "")[:10] >= today:
                    print(f"{mode}: already sent today.")
                    continue
            since = state.get(key) or max((now - timedelta(days=7)).strftime(FMT), state["baseline"])  # first weekly: not the films we only started tracking on the baseline day
            new = [f for f in data["films"] if f.get("onSaleSince") and f["onSaleSince"] > since]
            ann = [f for f in data["films"] if f["status"] == "announced"
                   and f.get("announcedSince") and f["announcedSince"] > since]
            if (test_to or preview) and not (new or ann):  # make the test email show something
                new = sorted((f for f in data["films"] if f["status"] == "on_sale"), key=lambda f: f["shows"][0]["t"])[:3]
                ann = [f for f in data["films"] if f["status"] == "announced" and f["premiere"]][:3]
            if preview:  # a fuller sample than the test email, so the grid shows
                new = sorted((f for f in data["films"] if f["status"] == "on_sale" and f.get("kind") == "film" and f["poster"]),
                             key=lambda f: f["shows"][0]["t"])[:7]
                ann = [f for f in data["films"] if f["status"] == "announced" and f["premiere"]][:5]
            print(f"{mode}: since {since}: {len(new)} newly on sale, {len(ann)} newly announced")

            if preview:
                everyone = {"prefs": {}, "watchlist": [f["id"] for f in data["films"]]}
                ending = pick_leaving(data["films"], everyone, now_s, {new[0]["id"], ann[0]["id"]})
                demo = [new[0]["id"], ann[0]["id"]] + [f["id"] for f, *_ in ending[:2]]
                recipients = [{"email": "you@example.com", "prefs": {"regions": [rkey]}, "watchlist": demo, "unsubscribe_token": "preview"}]
            elif test_to:
                # Demo email = a real digest on command: built from this address's saved account if it has one
                # (its filters, watchlist and real unsubscribe link), forced to include this region so both get tested.
                real = profile_for(test_to)
                if real:
                    print(f"Using the saved settings for {test_to}")
                    recipients = [{**real, "prefs": {**(real.get("prefs") or {}), "regions": [rkey]}}]
                else:
                    print(f"No account for {test_to}; using default settings")
                    recipients = [{"email": test_to, "prefs": {"regions": [rkey]}, "watchlist": [], "unsubscribe_token": "test"}]
            else:
                if profiles is None:
                    profiles = subscribers()
                    if profiles is None:
                        print("Supabase isn't configured (SUPABASE_URL / SUPABASE_SECRET_KEY); no emails sent.")
                        profiles = []
                recipients = [p for p in profiles if rkey in ((p.get("prefs") or {}).get("regions") or ["oslo"])
                              and (((p.get("prefs") or {}).get("frequency") == "weekly") == (mode == "weekly"))]
            messages = []
            for p in recipients:
                prefs = dict(p.get("prefs") or {})
                prefs["_cinemas"] = [c for c in prefs.get("cinemas") or [] if c in data["cinemas"]]
                if rkey != "oslo":  # English subtitles only make sense for Oslo's data
                    prefs["englishSubs"] = False
                prefs["_audioEnNo"] = rkey == "costadelsol" and bool(prefs.get("audioEnNo"))
                if rkey not in ("oslo", "costadelsol"):  # Westman's data has no dubbed/original information
                    prefs["hideDubbed"] = False
                p = {**p, "prefs": prefs}
                items, announced = pick(new, p, now_s), pick_announced(ann, p, now_s)
                if not (items or announced):
                    continue  # a watchlist notice (e.g. leaving soon) alone never sends an email
                leaving = pick_leaving(data["films"], p, now_s, {f["id"] for f, *_ in items} | {f["id"] for f, _ in announced})
                unsub = f"{site}?unsubscribe={p['unsubscribe_token']}"
                messages.append((p["email"], *render(items, announced, leaving, site, unsub, prefs, cfg["name"], rkey)))
            print(f"{mode}: {len(recipients)} subscriber(s), {len(messages)} with something new")

            if preview:
                if messages:
                    Path(preview).write_text(messages[0][3])
                    print(f"Wrote {preview}\n{messages[0][1]}")
                return
            if dry:
                for to, subject, txt, _ in messages:
                    print(f"\n=== {to}\n{subject}\n\n{txt}")
                continue
            if messages:
                send(messages)
            if not test_to:
                state[key] = now_s
                state_path.write_text(json.dumps(state, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
