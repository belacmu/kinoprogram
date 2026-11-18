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
TODAY = "2026-11-18"

# What TMDB's /movie/{id}?append_to_response=watch/providers,release_dates returns (shortened).
TMDB = {"id": 2, "title": "Gone",
        "watch/providers": {"results": {
            "NO": {"link": "https://www.themoviedb.org/movie/2/watch?locale=NO",
                   "flatrate": [{"provider_name": "Max", "logo_path": "/max.jpg", "display_priority": 3},
                                {"provider_name": "Netflix", "logo_path": "/nf.jpg", "display_priority": 1},
                                {"provider_name": "Netflix", "logo_path": "/nf.jpg", "display_priority": 1}],
                   "rent": [{"provider_name": "Apple TV", "logo_path": "/apple.jpg", "display_priority": 2}],
                   "buy": [{"provider_name": "Apple TV", "logo_path": "/apple.jpg", "display_priority": 2}]},
            "US": {"flatrate": [{"provider_name": "Hulu", "logo_path": "/hulu.jpg", "display_priority": 1}]}}},
        "release_dates": {"results": [
            {"iso_3166_1": "NO", "release_dates": [{"type": 3, "release_date": "2026-09-01T00:00:00.000Z"},
                                                   {"type": 4, "release_date": "2026-10-20T00:00:00.000Z"}]},
            {"iso_3166_1": "ES", "release_dates": [{"type": 4, "release_date": "2026-10-01T00:00:00.000Z"},
                                                   {"type": 4, "release_date": "2026-12-12T00:00:00.000Z", "note": "VOD"},
                                                   {"type": 4, "release_date": "2027-02-01T00:00:00.000Z"}]},
            {"iso_3166_1": "US", "release_dates": [{"type": 4, "release_date": "2026-11-25T00:00:00.000Z"}]}]}}


def registry():
    reg = filmids.Registry()
    reg.films = {"fw-dune": {"title": "Dune", "tmdb": 1}, "fw-gone": {"title": "Gone", "tmdb": 2},
                 "fw-notmdb": {"title": "Local short", "tmdb": None}, "cm-old": {"title": "Gone", "tmdb": 2, "merged": "fw-gone"},
                 "fw-unmarked": {"title": "Nobody's", "tmdb": 3}}
    reg.aliases = {"fw-dune": "fw-dune", "fw-gone": "fw-gone", "fw-notmdb": "fw-notmdb", "cm-old": "fw-gone",
                   "fw-EDI9": "fw-gone", "fw-unmarked": "fw-unmarked"}
    reg.source = "supabase"
    return reg


def test_options_for():
    opts, link = watch.options_for(TMDB["watch/providers"]["results"]["NO"])
    assert opts == {"stream": [{"name": "Netflix", "logo": "/nf.jpg"}, {"name": "Max", "logo": "/max.jpg"}],
                    "rent": [{"name": "Apple TV", "logo": "/apple.jpg"}], "buy": [{"name": "Apple TV", "logo": "/apple.jpg"}]}, opts
    assert link.startswith("https://www.themoviedb.org/")
    assert watch.options_for({}) == ({}, "")
    assert watch.options_for({"link": "javascript:alert(1)"})[1] == ""


def test_digital_date_per_country():
    rd = TMDB["release_dates"]["results"]
    assert watch.digital_date(rd, "NO", TODAY) == "2026-10-20"   # only one, already past: the latest
    assert watch.digital_date(rd, "ES", TODAY) == "2026-12-12"   # the next one ahead, not the later one or the past one
    assert watch.digital_date(rd, "CA", TODAY) == ""             # none for Canada: never another country's (US) date
    assert watch.digital_date([], "NO", TODAY) == ""


def test_all_marked_films_with_tmdb():
    """Every marked film (under any id it has had) matched on TMDB, showing somewhere or not; unmarked ones never."""
    reg = registry()
    got = watch.candidates(reg, ["fw-dune", "fw-EDI9", "cm-old", "fw-notmdb", "never-heard-of"])
    assert got == {"fw-dune", "fw-gone"}, got


def test_next_check():
    st = lambda days_ago, found=False, digital=(): {"checked": NOW - timedelta(days=days_ago), "found": found, "digital": list(digital)}
    # Nothing found: every 14 days.
    assert watch.next_check(st(0), NOW) == NOW + timedelta(days=14)
    # Found somewhere: every 60 days.
    assert watch.next_check(st(0, found=True), NOW) == NOW + timedelta(days=60)
    # A digital release ahead, before the usual recheck: the day after it.
    assert watch.next_check(st(0, digital=["2026-11-22"]), NOW) == datetime(2026, 11, 23, tzinfo=timezone.utc)
    # ...after it: the usual 14 days.
    assert watch.next_check(st(0, digital=["2027-03-01"]), NOW) == NOW + timedelta(days=14)
    # Released digitally over two years ago and still nowhere: never again. Under two years: keep asking.
    assert watch.next_check(st(0, digital=["2024-01-01"]), NOW) is None
    assert watch.next_check(st(0, digital=["2025-06-01"]), NOW) == NOW + timedelta(days=14)


def test_due():
    states = {"fresh": {"checked": NOW - timedelta(days=2), "found": False, "digital": []},
              "stale": {"checked": NOW - timedelta(days=20), "found": False, "digital": []},
              "older": {"checked": NOW - timedelta(days=30), "found": False, "digital": []},
              "found": {"checked": NOW - timedelta(days=30), "found": True, "digital": []},
              "given-up": {"checked": NOW - timedelta(days=300), "found": False, "digital": ["2020-01-01"]}}
    assert watch.due(set(states) | {"new"}, states, NOW) == ["new", "older", "stale"]  # never first, then the most overdue
    assert len(watch.due({f"n{i}" for i in range(150)}, {}, NOW, budget=100)) == 100


def test_states_from():
    rows = [{"film_id": "a", "checked_at": "2026-11-01T00:00:00+00:00", "options": {"digital": "2026-12-12"}},
            {"film_id": "a", "checked_at": "2026-10-30T00:00:00Z", "options": {"rent": [{"name": "X", "logo": ""}]}},
            {"film_id": "b", "checked_at": "2026-11-01T00:00:00+00:00", "options": {}}]
    st = watch.states_from(rows)
    assert st["a"]["found"] and st["a"]["digital"] == ["2026-12-12"] and st["a"]["checked"].day == 30
    assert not st["b"]["found"] and st["b"]["digital"] == []


def test_update_writes_every_country_and_skips_fresh():
    reg = registry()
    sent, asked = [], []
    rows = {"film_marks": [{"film_id": "fw-gone"}], "watch_options": []}
    filmids._config = lambda: ("http://x", "k")
    filmids._rows = lambda table, q: rows[table]
    filmids._request = lambda path, body=None, timeout=60: sent.append((path, body))
    external.tmdb_enabled = lambda: True
    external.tmdb_get = lambda path, **kw: asked.append((path, kw)) or TMDB
    watch.update(reg, ["CA", "ES", "NO"])
    assert asked == [("/movie/2", {"append_to_response": "watch/providers,release_dates"})], asked  # one request: services and dates
    (path, body), = sent
    assert path == "rpc/sync_watch_options"
    by = {r["country"]: r for r in body["p"]}
    assert set(by) == {"CA", "ES", "NO"} and all(r["film_id"] == "fw-gone" for r in body["p"])
    assert by["NO"]["options"]["stream"][0]["name"] == "Netflix" and by["NO"]["options"]["digital"] == "2026-10-20"
    assert by["CA"]["options"] == {} and by["CA"]["link"] == ""
    assert by["ES"]["options"] == {"digital": "2026-12-12"}, by["ES"]   # nothing in Spain yet, a digital release ahead
    # Checked a day ago with services found: nothing to do.
    rows["watch_options"] = [{"film_id": "fw-gone", "checked_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
                              "options": {"rent": [{"name": "X", "logo": ""}]}}]
    sent.clear(); asked.clear()
    watch.update(reg, ["CA", "ES", "NO"])
    assert not asked and not sent


def test_update_without_the_tables_does_nothing():
    reg = registry()
    filmids._config = lambda: ("http://x", "k")
    def rows(table, q):
        raise urllib.error.HTTPError("u", 404, "no table", {}, io.BytesIO(b""))
    filmids._rows = rows
    asked = []
    external.tmdb_enabled = lambda: True
    external.tmdb_get = lambda path, **kw: asked.append(path) or TMDB
    watch.update(reg, ["NO"])
    assert not asked


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"{len(tests)} passed")
