"""Tests for scraper/watch.py. Run: python3 scraper/test_watch.py (no network, no database)."""
import io
import sys
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import external  # noqa: E402
import filmids  # noqa: E402
import watch  # noqa: E402

NOW = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)

# What TMDB's /movie/{id}/watch/providers returns (shortened).
TMDB = {"id": 550, "results": {
    "NO": {"link": "https://www.themoviedb.org/movie/550/watch?locale=NO",
           "flatrate": [{"provider_name": "Max", "logo_path": "/max.jpg", "display_priority": 3},
                        {"provider_name": "Netflix", "logo_path": "/nf.jpg", "display_priority": 1},
                        {"provider_name": "Netflix", "logo_path": "/nf.jpg", "display_priority": 1}],
           "rent": [{"provider_name": "Apple TV", "logo_path": "/apple.jpg", "display_priority": 2}],
           "buy": [{"provider_name": "Apple TV", "logo_path": "/apple.jpg", "display_priority": 2}]},
    "US": {"flatrate": [{"provider_name": "Hulu", "logo_path": "/hulu.jpg", "display_priority": 1}]},
}}


def registry():
    reg = filmids.Registry()
    reg.films = {"fw-dune": {"title": "Dune", "tmdb": 1}, "fw-gone": {"title": "Gone", "tmdb": 2},
                 "fw-notmdb": {"title": "Local short", "tmdb": None}, "cm-old": {"title": "Gone", "tmdb": 2, "merged": "fw-gone"},
                 "fw-unwatched": {"title": "Nobody's", "tmdb": 3}}
    reg.aliases = {"fw-dune": "fw-dune", "fw-gone": "fw-gone", "fw-notmdb": "fw-notmdb", "cm-old": "fw-gone",
                   "fw-EDI9": "fw-gone", "fw-unwatched": "fw-unwatched"}
    reg.source = "supabase"
    return reg


def test_options_for():
    opts, link = watch.options_for(TMDB["results"]["NO"])
    assert opts == {"stream": [{"name": "Netflix", "logo": "/nf.jpg"}, {"name": "Max", "logo": "/max.jpg"}],
                    "rent": [{"name": "Apple TV", "logo": "/apple.jpg"}], "buy": [{"name": "Apple TV", "logo": "/apple.jpg"}]}, opts
    assert link.startswith("https://www.themoviedb.org/")
    assert watch.options_for({}) == ({}, "")
    assert watch.options_for({"link": "javascript:alert(1)"})[1] == ""


def test_only_watchlist_films_with_no_showings():
    """On a watchlist (under any id it has had) and no showings anywhere; a film with no TMDB match can't be looked up."""
    reg = registry()
    lists = [["fw-dune", "fw-EDI9"], ["cm-old", "fw-notmdb", "never-heard-of"], None]
    got = watch.candidates(reg, lists, with_showings={"fw-dune"})
    assert got == {"fw-gone"}, got   # Dune is showing; fw-unwatched is on no list


def test_due():
    checked = {"a": NOW - timedelta(days=2), "b": NOW - timedelta(days=20), "c": NOW - timedelta(days=15)}
    assert watch.due({"a", "b", "c", "new"}, checked, NOW) == ["new", "b", "c"]  # never checked first, then the oldest; "a" is fresh
    assert watch.due({"a"}, checked, NOW) == []
    assert watch.due({f"n{i}" for i in range(150)}, {}, NOW, budget=100).__len__() == 100


def test_update_writes_every_country_and_skips_fresh():
    reg = registry()
    sent, asked = [], []
    rows = {"profiles": [{"watchlist": ["fw-gone", "fw-dune"]}], "watch_options": []}
    filmids._config = lambda: ("http://x", "k")
    filmids._rows = lambda table, q: rows[table]
    filmids._request = lambda path, body=None, timeout=60: sent.append((path, body))
    external.tmdb_enabled = lambda: True
    external.tmdb_get = lambda path, **kw: asked.append(path) or TMDB
    watch.update(reg, {"fw-dune"}, ["CA", "ES", "NO"])
    assert asked == ["/movie/2/watch/providers"], asked
    (path, body), = sent
    assert path == "rpc/sync_watch_options"
    by = {r["country"]: r for r in body["p"]}
    assert set(by) == {"CA", "ES", "NO"} and all(r["film_id"] == "fw-gone" for r in body["p"])
    assert by["NO"]["options"]["stream"][0]["name"] == "Netflix" and by["CA"]["options"] == {} and by["CA"]["link"] == ""
    # Checked a day ago: nothing to do.
    rows["watch_options"] = [{"film_id": "fw-gone", "checked_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()}]
    sent.clear(); asked.clear()
    watch.update(reg, {"fw-dune"}, ["CA", "ES", "NO"])
    assert not asked and not sent


def test_update_without_the_table_does_nothing():
    reg = registry()
    filmids._config = lambda: ("http://x", "k")
    def rows(table, q):
        if table == "watch_options":
            raise urllib.error.HTTPError("u", 404, "no table", {}, io.BytesIO(b""))
        return [{"watchlist": ["fw-gone"]}]
    filmids._rows = rows
    asked = []
    external.tmdb_enabled = lambda: True
    external.tmdb_get = lambda path, **kw: asked.append(path) or TMDB
    watch.update(reg, set(), ["NO"])
    assert not asked


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"{len(tests)} passed")
