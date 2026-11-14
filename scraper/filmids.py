"""Stable ids for films and showings (docs/stable-ids.md).

A film's id is the first listing id it was ever seen with (e.g. "fw-EDI20261336"), frozen. Every listing id it has had since is an
alias of it, forever. A new listing joins a known film through a known listing id, else the film's TMDB id, else its IMDb id (never
by title). A showing's id is "<film id>|<start>|<cinema>" when first seen, frozen.

The registry of films, aliases and showings lives in Supabase (tables films, film_aliases, showings; filled by sync()). A copy is
kept in state/film-registry.json, so a run without Supabase still gives every film the same id. Ids are deterministic, so a film
first seen while Supabase was down gets the id it would have got anyway.

Usage from build.py:
    reg = filmids.load(now_utc)
    filmids.resolve(reg, {region: films}, history)   # sets f["id"], f["ids"], each show's "id"; combines entries of one film
    filmids.save_local(reg)
    filmids.sync(reg, ...)                            # never raises
"""
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCAL = ROOT / "state" / "film-registry.json"
HISTORY = ROOT / "state" / "listing-history.json"
IMDB = re.compile(r"^tt\d{5,10}$")
# A cinema's showings are only marked gone when it still has at least this share of the upcoming showings the database knows for
# it: a source that fails (or silently returns nothing) must not mark a whole cinema's programme gone.
GONE_MIN_SHARE = 0.5
MAX_SHOWING_ID = 500


class Registry:
    def __init__(self):
        self.films = {}      # id -> {"title", "year", "tmdb", "imdb", "merged", "first"}
        self.aliases = {}    # alias -> film id (may be a merged film: use canon())
        self.kinds = {}      # alias -> "listing" | "merged" | "history"
        self.shows = {}      # showing id -> (film id, cinema, starts, region, gone)
        self.db_shows = {}   # the same, as the database had them before this run (for gone_cinemas)
        self.source = "empty"   # "supabase", "local" or "empty"
        self.seed = False       # Supabase was empty: send everything we know
        self.order = 0          # films minted in this run, in the order they were made (a tie-break between equal "first")
        # What this run changed, for sync()
        self.touched = set()
        self.new_aliases = {}   # alias -> (film id, kind)
        self.merges = []        # [(loser, winner)]

    # ---------------------------------------------------------------- lookups
    def canon(self, fid):
        seen = set()
        while fid in self.films and self.films[fid].get("merged") and fid not in seen:
            seen.add(fid)
            fid = self.films[fid]["merged"]
        return fid

    def film_of(self, alias):
        fid = self.aliases.get(alias)
        return self.canon(fid) if fid else None

    def by_ext(self, key, value):
        """The oldest live film with this TMDB/IMDb id, or None."""
        if not value:
            return None
        hits = {self.canon(i) for i, f in self.films.items() if f.get(key) == value}
        return min(hits, key=self.age) if hits else None

    def age(self, fid):
        f = self.films.get(fid, {})
        return (f.get("first") or "9999", f.get("order", 0), fid)

    # ---------------------------------------------------------------- changes
    def add_alias(self, alias, fid, kind):
        if self.aliases.get(alias) == fid:
            return
        self.aliases[alias] = fid
        self.kinds.setdefault(alias, kind)
        self.new_aliases[alias] = (fid, self.kinds[alias])

    def mint(self, listing, now_iso):
        fid, n = listing, 1
        while fid in self.films or fid in self.aliases:
            n += 1
            fid = f"{listing}~{n}"
        self.order += 1
        self.films[fid] = {"title": "", "year": "", "tmdb": None, "imdb": None, "merged": None, "first": now_iso, "order": self.order}
        self.add_alias(fid, fid, "listing")
        self.touched.add(fid)
        return fid

    def merge(self, loser, winner):
        """Fold film `loser` into `winner` (both canonical). Nothing is deleted: the loser keeps its row, pointing at the winner."""
        if loser == winner:
            return
        self.films[loser]["merged"] = winner
        for f in self.films.values():
            if f.get("merged") == loser:
                f["merged"] = winner
        for a, fid in list(self.aliases.items()):
            if fid == loser:
                self.aliases[a] = winner
                self.new_aliases[a] = (winner, self.kinds.get(a, "listing"))
        self.kinds[loser] = self.kinds.get(loser, "listing")
        self.add_alias(loser, winner, "merged")
        w = self.films[winner]
        for k in ("tmdb", "imdb", "year"):
            w[k] = w.get(k) or self.films[loser].get(k)
        self.merges.append((loser, winner))
        self.touched.add(winner)
        print(f"  film ids: {loser} merged into {winner}")

    # ---------------------------------------------------------------- local copy
    def to_json(self):
        # Showings are kept only where the id isn't the one that would be made again from (film, start, cinema): the rest are
        # rebuilt for free.
        shows = {i: list(v[:4]) for i, v in self.shows.items() if i != f"{v[0]}|{v[2]}|{v[1]}"}
        films = {i: {k: v for k, v in f.items() if k != "order" and v} for i, f in self.films.items()}
        return {"films": dict(sorted(films.items())), "aliases": dict(sorted(self.aliases.items())),
                "kinds": {a: k for a, k in sorted(self.kinds.items()) if k != "listing"}, "shows": dict(sorted(shows.items()))}

    @classmethod
    def from_json(cls, d):
        reg = cls()
        for i, f in (d.get("films") or {}).items():
            reg.films[i] = {"title": f.get("title", ""), "year": f.get("year", ""), "tmdb": f.get("tmdb"), "imdb": f.get("imdb"),
                            "merged": f.get("merged"), "first": f.get("first", "")}
        reg.aliases = dict(d.get("aliases") or {})
        reg.kinds = {a: (d.get("kinds") or {}).get(a, "listing") for a in reg.aliases}
        reg.shows = {i: (v[0], v[1], v[2], v[3], False) for i, v in (d.get("shows") or {}).items()}
        return reg


# ---------------------------------------------------------------- Supabase
def _config():
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SECRET_KEY")
    return (url.rstrip("/"), key) if url and key else (None, None)


def _request(path, body=None, timeout=60):
    url, key = _config()
    headers = {"apikey": key, "Content-Type": "application/json"}
    if key.startswith("eyJ"):  # legacy service_role JWT
        headers["Authorization"] = f"Bearer {key}"
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    req = urllib.request.Request(f"{url}/rest/v1/{path}", data=data, headers=headers, method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


def _rows(table, query):
    """Every row, a page at a time (PostgREST returns at most 1000 per request)."""
    out, offset = [], 0
    while True:
        page = _request(f"{table}?{query}&limit=1000&offset={offset}")
        out += page
        if not page:
            return out
        offset += len(page)


def _load_supabase(since):
    reg = Registry()
    for r in _rows("films", "select=id,title,year,tmdb,imdb,merged_into,first_seen&order=id"):
        reg.films[r["id"]] = {"title": r["title"] or "", "year": r["year"] or "", "tmdb": r["tmdb"], "imdb": r["imdb"],
                              "merged": r["merged_into"], "first": r["first_seen"] or ""}
    for r in _rows("film_aliases", "select=alias,film_id,kind&order=alias"):
        reg.aliases[r["alias"]] = r["film_id"]
        reg.kinds[r["alias"]] = r["kind"]
    q = f"select=id,film_id,cinema,starts,region,gone_at&starts=gte.{urllib.parse.quote(since)}&order=id"
    for r in _rows("showings", q):
        reg.shows[r["id"]] = (r["film_id"], r["cinema"], r["starts"], r["region"], bool(r["gone_at"]))
    reg.db_shows = dict(reg.shows)
    reg.source = "supabase"
    return reg


def load_local():
    if LOCAL.exists():
        reg = Registry.from_json(json.loads(LOCAL.read_text()))
        reg.source = "local"
        return reg
    return Registry()


def load(since):
    """The registry: Supabase's when it can be read, else the local copy. `since` ("YYYY-MM-DD"): showings from then on are loaded."""
    local = load_local()
    if not _config()[0]:
        print(f"  film ids: no database configured; using the {local.source} registry ({len(local.films)} films)")
        return local
    try:
        reg = _load_supabase(since)
    except Exception as e:
        print(f"  ! film ids: database unavailable ({e}); using the {local.source} registry", file=sys.stderr)
        return local
    if not reg.films and local.films:
        # The tables were just made: start from the local copy and send all of it.
        local.source, local.seed = "supabase", True
        local.touched = set(local.films)
        local.new_aliases = {a: (fid, local.kinds.get(a, "listing")) for a, fid in local.aliases.items()}
        local.merges = [(i, f["merged"]) for i, f in local.films.items() if f.get("merged")]
        print(f"  film ids: database is empty; seeding it from the local registry ({len(local.films)} films)")
        return local
    print(f"  film ids: {len(reg.films)} films, {len(reg.aliases)} aliases, {len(reg.shows)} upcoming showings from the database")
    return reg


def save_local(reg, since):
    """Write the local copy. Showings before `since` ("YYYY-MM-DD") are left out: nothing will be resolved against them again."""
    reg.shows = {i: v for i, v in reg.shows.items() if v[2] >= since}
    LOCAL.write_text(json.dumps(reg.to_json(), ensure_ascii=False, indent=0))


# ---------------------------------------------------------------- resolving
def _ext(f):
    e = f.get("ext") or {}
    tmdb = e.get("tmdb") if isinstance(e.get("tmdb"), int) else None
    imdb = e.get("imdb") if isinstance(e.get("imdb"), str) and IMDB.match(e["imdb"]) else None
    return tmdb, imdb


def resolve_film(reg, f, now_iso):
    """The stable id for one scraped film (its listing ids are f["ids"]). Records new aliases and merges in `reg`."""
    listings = list(f["ids"])
    tmdb, imdb = _ext(f)
    cands = []
    for c in [reg.film_of(i) for i in listings] + [reg.by_ext("tmdb", tmdb), reg.by_ext("imdb", imdb)]:
        if c and c not in cands:
            cands.append(c)
    if not cands:
        fid = reg.mint(listings[0], now_iso)
    else:
        # Prefer a film with this TMDB/IMDb id; then the oldest.
        fits = [c for c in cands if (tmdb and reg.films[c].get("tmdb") == tmdb) or (imdb and reg.films[c].get("imdb") == imdb)]
        fid = min(fits or cands, key=reg.age)
        for c in cands:
            if c == fid:
                continue
            a, b = reg.films[c], reg.films[fid]
            clash = (a.get("tmdb") and b.get("tmdb") and a["tmdb"] != b["tmdb"]) or (a.get("imdb") and b.get("imdb") and a["imdb"] != b["imdb"])
            if clash:  # two films that are known to be different: never merge them
                print(f"  ! film ids: {f['title']!r} matches both {fid} and {c}, which have different TMDB/IMDb ids; not merged", file=sys.stderr)
                continue
            winner, loser = min((fid, c), key=reg.age), max((fid, c), key=reg.age)
            reg.merge(loser, winner)
            fid = winner
    for i in listings:
        known = reg.film_of(i)
        if known is None:
            reg.add_alias(i, fid, "listing")
        # A listing already belonging to a different film (a clash above) stays where it is.
    film = reg.films[fid]
    film["title"] = (f.get("ext") or {}).get("en") or f["title"] or film.get("title", "")
    film["year"] = f.get("year") or film.get("year", "")
    film["tmdb"] = tmdb or film.get("tmdb")
    film["imdb"] = imdb or film.get("imdb")
    reg.touched.add(fid)
    return fid


def add_history(reg, history):
    """Old listing ids (from git history, state/listing-history.json) whose TMDB/IMDb id matches a known film become its aliases,
    so watchlist entries made under them find the film again."""
    n = 0
    for listing, ext in sorted(history.items()):
        if listing in reg.aliases:
            continue
        fid = reg.by_ext("tmdb", ext.get("tmdb")) or reg.by_ext("imdb", ext.get("imdb"))
        if fid:
            reg.add_alias(listing, fid, "history")
            n += 1
    if n:
        print(f"  film ids: {n} old listing ids linked to their films")


def combine(films):
    """Entries of one region that resolved to the same film become one entry (e.g. two Filmweb listings of Heat, or a film and its
    IMAX re-release): the one with most showings first, the others' showings, links and series added to it."""
    groups = {}
    for f in films:
        groups.setdefault(f["id"], []).append(f)
    out = []
    for f in films:
        g = groups.pop(f["id"], None)
        if g is None:
            continue
        if len(g) > 1:
            g.sort(key=lambda x: -len(x["shows"]))
            main = g[0]
            shows = {(s["t"], s["cinema"]): s for x in g for s in x["shows"]}
            main["shows"] = sorted(shows.values(), key=lambda s: s["t"])
            main["links"] = list({l["url"]: l for x in g for l in x["links"]}.values())
            main["series"] = list(dict.fromkeys(s for x in g for s in x["series"]))
            main["listings"] = list(dict.fromkeys(i for x in g for i in x["listings"]))
            for k in ("alt", "director", "runtime", "blurb", "poster", "year", "premiere", "ext"):
                for x in g[1:]:
                    main[k] = main.get(k) or x.get(k)
            if any(s["ticket"] for s in main["shows"]):
                main["status"] = "on_sale"
            print(f"  film ids: combined {len(g)} entries for {main['title']!r} ({main['id']})")
            f = main
        out.append(f)
    return out


def showing_id(reg, by_key, fid, s, region):
    key = (fid, s["cinema"], s["t"])
    sid = by_key.get(key)
    if not sid:
        base = sid = f"{fid}|{s['t']}|{s['cinema']}"
        n = 1
        while sid in reg.shows:
            n += 1
            sid = f"{base}~{n}"
        by_key[key] = sid
    reg.shows[sid] = (fid, s["cinema"], s["t"], region, False)
    return sid


def resolve(reg, regions, now_iso, history=None):
    """Give every film in `regions` ({region: [films]}) its stable id. f["ids"] comes in as the film's listing ids and goes out as
    every id the film is known by, the stable id first. Each region's list is combined (see combine()). Returns {region: films}."""
    for films in regions.values():
        for f in films:
            f["listings"] = list(f["ids"])
            f["id"] = resolve_film(reg, f, now_iso)
    if history:
        add_history(reg, history)
    # Ids may have moved on while resolving (a later film merged an earlier one): settle them all.
    for films in regions.values():
        for f in films:
            f["id"] = reg.canon(f["id"])
    out = {r: combine(films) for r, films in regions.items()}
    by_key = {}
    for sid, (fid, cinema, starts, _, _) in sorted(reg.shows.items()):
        k = (reg.canon(fid), cinema, starts)
        # A showing both films of a merge had: the winner's own row is the one kept.
        if k not in by_key or reg.canon(fid) == fid:
            by_key[k] = sid
    names = {}
    for a, fid in reg.aliases.items():
        names.setdefault(reg.canon(fid), set()).add(a)
    for r, films in out.items():
        for f in films:
            fid = f["id"]
            for s in f["shows"]:
                s["id"] = showing_id(reg, by_key, fid, s, r)
            others = sorted(names.get(fid, set()) - {fid} - set(f["listings"]))
            f["ids"] = list(dict.fromkeys([fid] + f["listings"] + others))
    return out


# ---------------------------------------------------------------- writing to Supabase
def _ticket(u):
    return u if isinstance(u, str) and re.match(r"^https?://", u, re.I) and len(u) <= 1000 else None


def gone_cinemas(reg, region, films, now_s):
    """The cinemas whose missing upcoming showings may be marked gone this run (see GONE_MIN_SHARE)."""
    known, seen = {}, {}
    for fid, cinema, starts, r, gone in reg.db_shows.values():
        if r == region and starts >= now_s and not gone:
            known[cinema] = known.get(cinema, 0) + 1
    for f in films:
        for s in f["shows"]:
            seen[s["cinema"]] = seen.get(s["cinema"], 0) + 1
    ok, skipped = [], []
    for c, n in known.items():
        (ok if seen.get(c, 0) >= GONE_MIN_SHARE * n else skipped).append(c)
    for c in sorted(skipped):
        print(f"  ! film ids: {c} has {seen.get(c, 0)} showings, the database expects about {known[c]}; none of its showings marked gone",
              file=sys.stderr)
    return sorted(ok), sorted(skipped)


def sync(reg, built, live_ids, complete):
    """Write this run's films and showings to Supabase. built: [(region, now_s, films)] for the regions built in this run.
    live_ids: every film id in any region's current data. complete: all regions' data is present, so films in none can be marked
    gone. Never raises: the site doesn't depend on this."""
    if not _config()[0]:
        return
    if reg.source != "supabase":
        print("  film ids: database not read this run; not writing to it")
        return
    try:
        films = []
        for fid in sorted(reg.touched if reg.seed else {reg.canon(i) for i in reg.touched}):
            f = reg.films[fid]
            films.append({"id": fid, "title": (f.get("title") or fid)[:300], "year": str(f.get("year") or "")[:10],
                          "tmdb": f.get("tmdb"), "imdb": f.get("imdb")})
        cinemas = {r: gone_cinemas(reg, r, fs, now_s) for r, now_s, fs in built}
        # Films in no region's data are marked gone only when every region is there and no cinema looked broken this run.
        complete = complete and not any(skipped for _, skipped in cinemas.values())
        payload = {"films": films, "merges": [{"loser": a, "winner": b} for a, b in reg.merges],
                   "aliases": [{"alias": a, "film_id": fid, "kind": k} for a, (fid, k) in sorted(reg.new_aliases.items())],
                   "live": sorted(live_ids), "complete": bool(complete)}
        _request("rpc/sync_films", {"p": payload}, timeout=120)
        print(f"  film ids: wrote {len(films)} films, {len(payload['aliases'])} new aliases, {len(reg.merges)} merges")
        for r, now_s, fs in built:
            shows = []
            for f in fs:
                for s in f["shows"]:
                    if len(s["id"]) > MAX_SHOWING_ID:
                        continue
                    shows.append({"id": s["id"], "film_id": f["id"], "cinema": s["cinema"], "starts": s["t"],
                                  "title": (f["title"] or f["id"])[:300], "ticket": _ticket(s.get("ticket"))})
            _request("rpc/sync_showings", {"p": {"region": r, "now": now_s, "cinemas": cinemas[r][0], "showings": shows}}, timeout=120)
            print(f"  film ids: wrote {len(shows)} {r} showings")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        print(f"  ! film ids: writing to the database failed: {e.code} {detail}", file=sys.stderr)
    except Exception as e:
        print(f"  ! film ids: writing to the database failed: {e}", file=sys.stderr)


def load_history():
    return json.loads(HISTORY.read_text()) if HISTORY.exists() else {}
