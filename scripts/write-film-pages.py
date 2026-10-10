#!/usr/bin/env python3
"""One small page per film (site/f/<region>/<id>/index.html) so a shared link gets that film's title, text and poster
in its preview. The app's own film links are "#film/<id>", which chat apps and crawlers never see (they drop the
fragment), so every link would preview as the front page. These pages carry the film's Open Graph tags and send
people on to the app with the film open.

Run after site/data/*.json exist and before the site is uploaded (both deploy workflows do).
Usage: python3 scripts/write-film-pages.py
Environment: SITE_URL (default https://belacmu.github.io/kinoprogram/)
"""
import html
import json
import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
OUT = SITE / "f"
SITE_URL = os.environ.get("SITE_URL", "https://belacmu.github.io/kinoprogram/").rstrip("/") + "/"
DATA = {"oslo": "films.json", "westman": "westman.json", "winnipeg": "winnipeg.json", "costadelsol": "costadelsol.json"}


def clip(text, n):
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1].rsplit(" ", 1)[0].rstrip(",.;:") + "…"


def page(f, region, location):
    title = (f.get("ext") or {}).get("en") or f["title"]
    if f.get("year") and str(f["year"]) not in title:
        title += f" ({f['year']})"
    facts = " · ".join(x for x in [f.get("year"), f"{f['runtime']} min" if f.get("runtime") else "",
                                   f"Directed by {f['director']}" if f.get("director") else ""] if x)
    desc = " — ".join(x for x in [f"Showtimes and tickets in {location}", facts, clip(f.get("blurb"), 160)] if x)
    here = f"{SITE_URL}f/{region}/{f['id']}/"
    app = f"{SITE_URL}?r={region}#film/{f['id']}"
    poster = next((p for p in (f.get("poster"), f.get("poster2")) if p and p.startswith("http")), "")
    e = lambda s: html.escape(s, quote=True)
    image = (f'<meta property="og:image" content="{e(poster)}">\n<meta name="twitter:card" content="summary">'
             if poster else
             f'<meta property="og:image" content="{SITE_URL}og.png">\n<meta property="og:image:width" content="1200">\n'
             f'<meta property="og:image:height" content="630">\n<meta name="twitter:card" content="summary_large_image">')
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)} · Cinecrab</title>
<meta name="description" content="{e(desc)}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Cinecrab">
<meta property="og:title" content="{e(title)}">
<meta property="og:description" content="{e(desc)}">
<meta property="og:url" content="{e(here)}">
{image}
<link rel="canonical" href="{e(here)}">
<script>location.replace({json.dumps(app)});</script>
</head>
<body style="background:#0a0c0f;color:#e9ebef;font-family:system-ui,sans-serif">
<p style="padding:2rem"><a href="{e(app)}" style="color:inherit">{e(title)} on Cinecrab</a></p>
</body>
</html>
"""


def main():
    shutil.rmtree(OUT, ignore_errors=True)
    total = 0
    for region, name in DATA.items():
        path = SITE / "data" / name
        if not path.exists():
            continue
        data = json.loads(path.read_text())
        for f in data["films"]:
            if not f.get("id") or "/" in f["id"] or ".." in f["id"]:
                continue
            d = OUT / region / f["id"]
            d.mkdir(parents=True, exist_ok=True)
            (d / "index.html").write_text(page(f, region, data.get("location") or region))
            total += 1
    print(f"Wrote {total} film pages in {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
