"""Tests for scraper/sources.py (cinema listings). Run: python3 scraper/test_sources.py (no network)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import sources  # noqa: E402

# A Cinemateket film page, shortened. Like the real one, it links each series twice (here Die Hard's).
CINE_PAGE = """
<h1 class="page-film__heading">Die Hard</h1>
<a class="page-film__concepts-link" href="/serier/mannsroller">Mannsroller</a>
<a class="page-film__concepts-link" href="/serier/jul">Jul på Cinemateket</a>
<table><tr><td class="data-table__key">Visningsformat</td>
<td class="data-table__value">35mm</td></tr>
<tr><td class="data-table__key">Produsert</td>
<td class="data-table__value">USA 1988</td></tr></table>
<div id="all-shows"><ul>
<li class="shows-table__item"><span>to. 10.12.2026</span><span class="shows-table__time">18:00</span>
<span class="shows-table__screen">Tancred</span>
<div class="shows-table__cta"><a href="https://www.cinemateket.no/billetter/1">Kjøp</a></div></li>
</ul></div>
<a class="page-film__concepts-link" href="/serier/mannsroller">Mannsroller</a>
<a class="page-film__concepts-link" href="/serier/jul">Jul på Cinemateket</a>
"""


def test_cinemateket_series_once():
    url = "https://www.cinemateket.no/filmer/die-hard"
    get = sources.get
    sources.get = lambda u, *a, **k: CINE_PAGE
    try:
        f = sources.cine_film(url, {"title": "Die Hard", "image": ""})
    finally:
        sources.get = get
    assert f["series"] == ["Mannsroller", "Jul på Cinemateket"], f["series"]
    [s] = f["shows"]
    assert (s["t"], s["screen"]) == ("2026-12-10T18:00", "Tancred"), s
    assert s["tags"] == ["35mm", "Mannsroller", "Jul på Cinemateket"], s["tags"]


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"{len(tests)} passed")
