"""Fetch showings from Filmweb (GraphQL) and Cinemateket (HTML). Standard library only."""
import html
import json
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

UA = {"User-Agent": "Mozilla/5.0 (kinoprogram; +https://github.com/belacmu/kinoprogram)"}


def get(url, data=None, headers=None, timeout=30):
    req = urllib.request.Request(url, data=data, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8")


def text(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


# A showing that comes with a meal (dinner + film packages): priced far above a plain cinema ticket, so the site tags it.
MEAL_TAG = "Meal + film"
MEAL = re.compile(r"\b(middag|dinner|lunsj|lunch|brunsj|brunch|buffet|[2-6]-retters)\b", re.I)


def film(**kw):
    base = {"title": "", "alt": "", "year": "", "runtime": 0, "genres": [], "director": "",
            "countries": [], "blurb": "", "poster": "", "links": [], "series": [], "shows": [],
            "premiere": "", "premiereConfirmed": False, "knownIds": {}, "scope": "",
            "elsewhere": [], "checkedNationwide": False}
    return {**base, **kw}


# ---------------------------------------------------------------- Filmweb

FILMWEB = "https://movieinfoqs.filmweb.no/graphql"
MOVIE_FIELDS = """title titleOriginal mainVersionId productionYear lengthInMinutes genres nationalities
  sanityImagePosterUrl synopsisIngress premiere isPremiereConfirmed"""


def filmweb_query(query, variables):
    body = json.dumps({"query": query, "variables": variables}).encode()
    data = json.loads(get(FILMWEB, body, {"Content-Type": "application/json"}, timeout=60))
    if data.get("errors"):
        raise RuntimeError(data["errors"][0].get("message"))
    return data["data"]


def split_year(title):
    """Filmweb sometimes puts the year in the title: "It's a Wonderful Life (1946)"."""
    m = re.match(r"^(.*?)\s*\((\d{4})\)\s*$", title)
    return (m[1].strip(), m[2]) if m else (title, "")


def filmweb_film(m, shows):
    title, title_year = split_year(m["title"])
    poster = m.get("sanityImagePosterUrl") or ""
    if poster:
        poster = poster.split("?")[0] + "?w=360&h=540&fit=crop&auto=format"
    alt = m.get("titleOriginal") or ""
    premiere = (m.get("premiere") or "")[:10]
    alt = split_year(alt)[0]
    return film(
        title=title,
        alt=alt if alt.strip().lower() != title.strip().lower() else "",
        year=m.get("productionYear") or title_year,
        runtime=m.get("lengthInMinutes") or 0,
        genres=m.get("genres") or [],
        countries=m.get("nationalities") or [],
        blurb=m.get("synopsisIngress") or "",
        poster=poster,
        links=[{"label": "Filmweb", "url": f"https://www.filmweb.no/film/{m['mainVersionId']}"}],
        shows=shows,
        premiere=premiere if premiere > "1900" else "",
        premiereConfirmed=bool(m.get("isPremiereConfirmed")),
    )


def fetch_filmweb(location):
    q = """query ($locations: [String]) { movieQuery {
      current: getCurrentMovies(locations: $locations, removePastShows: true) { %s
        shows { showStart theaterName screenName ticketSaleUrl showType showInfo versionTags { tag } } }
      upcoming: getUpcomingMovies(locations: $locations, includeIndependent: true) { %s }
    } }""" % (MOVIE_FIELDS, MOVIE_FIELDS)
    data = filmweb_query(q, {"locations": [location]})["movieQuery"]
    films = []
    current_ids = set()
    for m in data["current"]:
        current_ids.add(m["mainVersionId"])
        norwegian = "Norge" in (m.get("nationalities") or [])
        shows = []
        for s in m.get("shows") or []:
            tags = [t["tag"] for t in s.get("versionTags") or [] if t["tag"] != "2D"]
            tags += [t.strip() for t in (s.get("showType") or "").split(",") if t.strip()]
            if MEAL.search((s.get("showInfo") or "") + " " + (s.get("showType") or "")):  # e.g. a Vega dinner screening
                tags.append(MEAL_TAG)
            shows.append({
                "t": s["showStart"][:16],
                "cinema": s["theaterName"],
                "screen": s.get("screenName") or "",
                "tags": tags,
                "note": s.get("showInfo") or "",
                "ticket": s.get("ticketSaleUrl") or "",
                "dub": "Norsk tale" in tags and not norwegian,
                "en": "Engelsk tekst" in tags,
            })
        films.append(filmweb_film(m, shows))
    # Filmweb's upcoming list is national (the city is ignored), so for films premiering around now,
    # check where in Norway they actually have showings. That tells "not coming to Oslo" apart from
    # "not scheduled anywhere yet".
    today = datetime.now().strftime("%Y-%m-%d")
    lo = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    hi = (datetime.now() + timedelta(days=14)).strftime("%Y-%m-%d")
    for m in data["upcoming"]:
        if m["mainVersionId"] in current_ids:
            continue
        f = filmweb_film(m, [])
        if f["premiere"] and lo <= f["premiere"] <= hi:
            try:
                shows = filmweb_query("query ($m: String) { showQuery { getShows(movieId: $m) { location } } }",
                                      {"m": m["mainVersionId"]})["showQuery"]["getShows"] or []
                f["elsewhere"] = sorted({s["location"] for s in shows if s.get("location") and s["location"] != location})
                f["checkedNationwide"] = True
            except Exception as e:
                print(f"  ! Filmweb shows for {m['title']}: {e}", file=sys.stderr)
        films.append(f)
    return films


# ---------------------------------------------------------------- Cinemateket

CINE = "https://www.cinemateket.no"
# Cinemateket's button text on a showing without a ticket link, in English like the rest of the site.
CTA_STATUS = {"Utsolgt": "Sold out", "Ikke i salg": "Not on sale yet", "Meld på": "Sign up by email"}


def cine_listing():
    """Walk /forestillinger pagination; return {film_url: {title, image}}."""
    films, url, pages = {}, f"{CINE}/forestillinger", 0
    while url and pages < 60:
        pages += 1
        h = get(url)
        for li in re.findall(r'<li class="shows-(?:list|grid)__item.*?</li>', h, re.S):
            href = re.search(r'href="([^"]+)"', li).group(1)
            title = text((re.search(r'__item-title[^"]*">(.*?)</', li, re.S) or [None, ""])[1])
            srcset = (re.search(r'data-srcset="([^"]+)"', li) or [None, ""])[1]
            img = ""
            if srcset:
                # pick the ~400w candidate, falling back to the largest
                cands = [c.strip().split(" ")[0] for c in html.unescape(srcset).split(",")]
                img = next((c for c in cands if "width=400" in c), cands[-1])
            films.setdefault(href, {"title": title, "image": img})
        m = re.search(r'data-paginate-link href="([^"]+)"', h)
        url = m.group(1) if m else None
    return films


def cine_film(url, listing):
    h = get(url)
    title = text((re.search(r'<h1 class="page-film__heading[^"]*">(.*?)</h1>', h, re.S) or [None, listing["title"]])[1])
    facts = {text(k): text(v) for k, v in re.findall(
        r'data-table__key">(.*?)</td>\s*<td class="data-table__value">(.*?)</td>', h, re.S)}
    series = [text(s) for s in re.findall(r'page-film__concepts-link"[^>]*>(.*?)</a>', h, re.S)]
    intro = text((re.search(r'page-film__intro-text">(.*?)</div>', h, re.S) or [None, ""])[1])
    og = html.unescape((re.search(r'property="og:image" content="([^"]+)"', h) or [None, ""])[1])
    runtime = re.search(r"\((\d+) minutter\)", facts.get("Spilletid", ""))
    produced = facts.get("Produsert", "")
    year = re.search(r"(\d{4})\s*$", produced)
    countries = [c.strip() for c in re.split(r"[/,]", re.sub(r"\d{4}\s*$", "", produced)) if c.strip()]
    # Dubbed = spoken only in Norwegian, but not a Norwegian production.
    dub = facts.get("Tale", "").strip().lower() == "norsk" and countries and "Norge" not in countries
    en = "engelsk" in facts.get("Tekst", "").lower()

    # #all-shows lists every showing; past ones carry a -expired CTA.
    i = h.find('id="all-shows"')
    section = h[i:] if i >= 0 else h
    shows = []
    for item in re.findall(r'<li class="shows-table__item.*?</li>', section, re.S):
        d = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", item)
        tm = re.search(r'shows-table__time">\s*(\d{1,2}):(\d{2})', item)
        if not (d and tm) or "shows-table__cta-expired" in item:
            continue
        cta = (re.search(r'shows-table__cta">(.*?)</span>\s*</div>', item, re.S) or [None, ""])[1]
        ticket = html.unescape((re.search(r'href="([^"]*billetter[^"]*)"', cta) or [None, ""])[1])
        tags = []
        if facts.get("Visningsformat") and facts["Visningsformat"] not in ("DCP", "Digital"):
            tags.append(facts["Visningsformat"])
        if en:
            tags.append("Engelsk tekst")
        shows.append({
            "t": f"{d[3]}-{d[2]}-{d[1]}T{int(tm[1]):02d}:{tm[2]}",
            "cinema": "Cinemateket",
            "screen": text((re.search(r'shows-table__screen">(.*?)</span>', item, re.S) or [None, ""])[1]),
            "tags": tags,
            "note": text((re.search(r'shows-table__extra-info">(.*?)</div>', item, re.S) or [None, ""])[1]),
            "ticket": ticket,
            "status": "" if ticket else CTA_STATUS.get(text(cta), text(cta)),
            "dub": bool(dub),
            "en": en,
        })
    return film(
        title=title or listing["title"],
        alt=facts.get("Originaltittel", ""),
        year=year[1] if year else "",
        runtime=int(runtime[1]) if runtime else 0,
        director=facts.get("Regi", ""),
        countries=countries,
        blurb=intro,
        poster=listing["image"] or og,
        links=[{"label": "Cinemateket", "url": url}],
        series=series,
        shows=shows,
    )


def fetch_cinemateket():
    listing = cine_listing()

    def safe(u):
        try:
            return cine_film(u, listing[u])
        except Exception as e:  # one broken film page shouldn't sink the run
            print(f"  ! {u}: {e}", file=sys.stderr)
            return None

    with ThreadPoolExecutor(6) as ex:
        results = list(ex.map(safe, listing))
    return [r for r in results if r and r["shows"]]
