"""Tests for scraper/watch.py (where to watch: parsing and when to ask again). Run: python3 scraper/test_watch.py"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
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


def test_states_from():
    rows = [{"film_id": "a", "checked_at": "2026-11-01T00:00:00+00:00", "options": {"digital": "2026-12-12"}},
            {"film_id": "a", "checked_at": "2026-10-30T00:00:00Z", "options": {"rent": [{"name": "X", "logo": ""}]}},
            {"film_id": "b", "checked_at": "2026-11-01T00:00:00+00:00", "options": {}}]
    st = watch.states_from(rows)
    assert st["a"]["found"] and st["a"]["digital"] == ["2026-12-12"] and st["a"]["checked"].day == 30
    assert not st["b"]["found"] and st["b"]["digital"] == []


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"{len(tests)} passed")
