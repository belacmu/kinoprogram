#!/usr/bin/env python3
"""Write state/listing-history.json: every listing id that ever had a TMDB or IMDb match in state/external.json's git history,
{listing id: {"tmdb": 123, "imdb": "tt…"}}. build.py (scraper/filmids.py add_history) links each one whose TMDB/IMDb id belongs
to a known film as an alias of that film, so watchlist entries saved under a listing that has since disappeared find their film.

Run once from the repo root (needs the full git history: git fetch --unshallow if it is a shallow clone):
    python3 scripts/film-history.py
"""
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "state" / "listing-history.json"


def main():
    revs = subprocess.check_output(["git", "rev-list", "--reverse", "HEAD", "--", "state/external.json"], cwd=ROOT, text=True).split()
    out = {}
    for rev in revs:
        try:
            cache = json.loads(subprocess.check_output(["git", "show", f"{rev}:state/external.json"], cwd=ROOT))
        except (subprocess.CalledProcessError, json.JSONDecodeError):
            continue
        for listing, rec in cache.items():
            if rec.get("conflict"):
                continue
            tmdb = rec.get("tmdb") if isinstance(rec.get("tmdb"), int) else None
            imdb = rec.get("imdb") if isinstance(rec.get("imdb"), str) and re.match(r"^tt\d{5,10}$", rec["imdb"]) else None
            if tmdb or imdb:
                out[listing] = {k: v for k, v in (("tmdb", tmdb), ("imdb", imdb)) if v}  # the latest match wins
    OUT.write_text("{\n" + ",\n".join(f"{json.dumps(k)}:{json.dumps(v)}" for k, v in sorted(out.items())) + "\n}\n")  # one per line
    print(f"{len(out)} listing ids from {len(revs)} versions -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
