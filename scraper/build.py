#!/usr/bin/env python3
"""Fetch all sources, merge by film, track when films go on sale, write site/data/films.json.

Usage: python3 scraper/build.py
State lives in state/seen.json:
  baseline    when tracking started (films on sale then are never "new")
  lastDigest  when the daily email last went out (digest.py reads and updates it)
  films       {film id: {"since": when it last became on sale, "last": last time seen on sale}}
  dated       {film id: {"first": first seen with a date, "last": last seen with a date}}
              A film is "newly announced" the first time it has a concrete date (a Filmweb
              premiere date, or showings) while not yet on sale.
"""
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))
import sources  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state" / "seen.json"
DATA = ROOT / "site" / "data" / "films.json"
LOCATION = "Oslo"
TZ = ZoneInfo("Europe/Oslo")
OFF_SALE_DAYS = 7  # a film must be off sale this long before it counts as new again
FORGET_DATED_DAYS = 90  # forget announced films not seen for this long
FMT = "%Y-%m-%dT%H:%M"


def film_id(url):
    m = re.search(r"filmweb\.no/film/([^/?#]+)", url)
    if m:
        return "fw-" + m[1]
    m = re.search(r"cinemateket\.no/filmer/([^/?#]+)", url)
    if m:
        return "cm-" + m[1]
    return re.sub(r"[^a-z0-9]+", "-", url.lower())


def key(title):
    t = title.lower()
    t = re.sub(r"\s*\((restaurert|ny restaurering)[^)]*\)", "", t)
    t = re.sub(r"\s+[–—-]\s+(\d+\s*mm|restaurert.*|4k.*|ny kopi.*)$", "", t)
    t = re.sub(r"^(the|a|an)\s+", "", t)
    return re.sub(r"[^0-9a-zæøåäöüéè]+", " ", t).strip()


def years_match(a, b):
    return not a or not b or abs(int(a) - int(b)) <= 1


def merge(primary, extra):
    films = list(primary)
    index = {}
    for f in films:
        for t in (f["title"], f["alt"]):
            if t:
                index.setdefault(key(t), []).append(f)
    for c in extra:
        cands = index.get(key(c["title"]), []) + (index.get(key(c["alt"]), []) if c["alt"] else [])
        match = next((f for f in cands if years_match(f["year"], c["year"])), None)
        if match:
            match["shows"] += c["shows"]
            match["links"] += c["links"]
            match["series"] += c["series"]
            for k in ("alt", "director", "runtime", "blurb", "poster", "year"):
                match[k] = match[k] or c[k]
        else:
            films.append(c)
    return films


def finalise(films, now):
    now_s = now.strftime(FMT)
    out = []
    for f in films:
        f["shows"] = sorted((s for s in f["shows"] if s["t"] >= now_s), key=lambda s: s["t"])
        f["ids"] = [film_id(l["url"]) for l in f["links"]]
        f["id"] = f["ids"][0]
        f["series"] = list(dict.fromkeys(f["series"]))
        if any(s["ticket"] for s in f["shows"]):
            f["status"] = "on_sale"
        elif f["shows"] or (f["premiere"] and f["premiere"] >= (now - timedelta(days=14)).strftime("%Y-%m-%d")) \
                or (not f["premiere"] and f["links"][0]["label"] == "Filmweb"):
            f["status"] = "announced"
        else:
            continue  # premiered a while ago but not playing in this city
        out.append(f)
    return out


def track(films, now):
    """Set each film's onSaleSince and update state. Returns the state dict."""
    first_run = not STATE.exists()
    state = {"baseline": None, "lastDigest": None, "films": {}} if first_run else json.loads(STATE.read_text())
    now_s = now.strftime(FMT)
    state["baseline"] = state["baseline"] or now_s
    state["lastDigest"] = state["lastDigest"] or now_s
    seen = state["films"]
    dated_first_run = "dated" not in state
    dated = state.setdefault("dated", {})
    for f in films:
        # Newly announced: first time this film has a concrete date (any status counts as
        # "known", so a film dropping from on sale back to announced doesn't trigger).
        f["announcedSince"] = ""
        if f["premiere"] or f["shows"]:
            drecs = [dated[i] for i in f["ids"] if i in dated]
            first = min((r["first"] for r in drecs), default=None)
            if not first:
                # Baseline runs and films that go straight on sale never count as announced.
                first = state["baseline"] if dated_first_run or f["status"] == "on_sale" else now_s
            for i in f["ids"]:
                dated[i] = {"first": first, "last": now_s}
            if f["status"] == "announced":
                f["announcedSince"] = first
        recs = [seen[i] for i in f["ids"] if i in seen]
        if f["status"] != "on_sale":
            f["onSaleSince"] = ""
            continue
        last = max((r["last"] for r in recs), default=None)
        gone_too_long = last and datetime.strptime(last, FMT) < datetime.strptime(now_s, FMT) - timedelta(days=OFF_SALE_DAYS)
        since = now_s if (not recs or gone_too_long) else min(r["since"] for r in recs)
        for i in f["ids"]:
            seen[i] = {"since": since, "last": now_s}
        f["onSaleSince"] = since
    # Forget films that have been off sale long enough to count as new anyway.
    cutoff = (now - timedelta(days=OFF_SALE_DAYS + 3)).strftime(FMT)
    state["films"] = {k: v for k, v in sorted(seen.items()) if v["last"] >= cutoff}
    dcut = (now - timedelta(days=FORGET_DATED_DAYS)).strftime(FMT)
    state["dated"] = {k: v for k, v in sorted(dated.items()) if v["last"] >= dcut}
    state["lastRun"] = now_s
    return state


def main():
    now = datetime.now(TZ).replace(tzinfo=None)
    print("Fetching Filmweb …")
    fw = sources.fetch_filmweb(LOCATION)
    print(f"  {len(fw)} films, {sum(len(f['shows']) for f in fw)} showings")
    print("Fetching Cinemateket …")
    try:
        cm = sources.fetch_cinemateket()
        print(f"  {len(cm)} films, {sum(len(f['shows']) for f in cm)} showings")
    except Exception as e:
        # Don't let one source failing wipe its films from the "seen" record: records are
        # only pruned after OFF_SALE_DAYS, so a short outage won't make films look new.
        print(f"  ! Cinemateket failed: {e}", file=sys.stderr)
        cm = []
    films = finalise(merge(fw, cm), now)
    state = track(films, now)

    new = [f for f in films if f["onSaleSince"] and f["onSaleSince"] > state["lastDigest"]]
    ann = [f for f in films if f["announcedSince"] and f["announcedSince"] > state["lastDigest"]]
    print(f"{sum(f['status'] == 'on_sale' for f in films)} on sale, "
          f"{sum(f['status'] == 'announced' for f in films)} announced; since last digest: "
          f"{len(new)} newly on sale, {len(ann)} newly announced")
    for f in new:
        print("  • on sale: " + f["title"])
    for f in ann:
        print("  • announced: " + f["title"])

    cinemas = sorted({s["cinema"] for f in films for s in f["shows"]},
                     key=lambda c: (c != "Cinemateket", c.lower()))
    DATA.parent.mkdir(parents=True, exist_ok=True)
    DATA.write_text(json.dumps({
        "generated": now.strftime(FMT), "baseline": state["baseline"], "location": LOCATION,
        "cinemas": cinemas, "films": films,
    }, ensure_ascii=False, separators=(",", ":")))
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1))
    print(f"Wrote {DATA.relative_to(ROOT)} ({DATA.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
