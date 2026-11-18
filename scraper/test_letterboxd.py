"""Tests for scraper/letterboxd.py (step 3: Letterboxd page and rating per film). Run: python3 scraper/test_letterboxd.py"""
import copy
import io
import sys
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import external  # noqa: E402
import filmids  # noqa: E402
import letterboxd  # noqa: E402

NOW = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)
TODAY = "2026-11-18"
DAY = lambda n: (NOW.date() - timedelta(days=n)).isoformat()


def registry(films):
    reg = filmids.Registry()
    reg.films = copy.deepcopy(films)
    reg.aliases = {i: i for i in films}
    return reg


def test_due():
    reg = registry({"new-listed": {"imdb": "tt1", "meta": {}}, "new-gone": {"tmdb": 2, "meta": {}},
                    "listed-stale": {"imdb": "tt3", "meta": {"lbAt": DAY(8)}}, "listed-fresh": {"imdb": "tt4", "meta": {"lbAt": DAY(2)}},
                    "wanted-stale": {"imdb": "tt5", "meta": {"lbAt": DAY(40)}}, "wanted-fresh": {"imdb": "tt6", "meta": {"lbAt": DAY(10)}},
                    "gone-old": {"imdb": "tt7", "meta": {"lbAt": DAY(400)}}, "no-ids": {"meta": {}},
                    "merged": {"imdb": "tt9", "merged": "x", "meta": {}}})
    got = letterboxd.due(reg, {"new-listed", "listed-stale", "listed-fresh"}, {"wanted-stale", "wanted-fresh"}, NOW.date())
    # Never asked (listed first), then the longest ago among the due: wanted monthly, listed weekly. Never: the rest.
    assert got == ["new-listed", "new-gone", "wanted-stale", "listed-stale"], got
    assert letterboxd.due(reg, set(), set(), NOW.date(), budget=1) == ["new-gone"]


def test_update_by_exact_id_and_once():
    reg = registry({"gone": {"title": "Drive My Car", "tmdb": 758866, "imdb": "tt14039582", "meta": {"poster": "https://p"}},
                    "missing": {"title": "Not there", "tmdb": 5, "meta": {}}})
    asked = []
    def lb(rec):
        asked.append(rec.get("imdb") or rec.get("tmdb"))
        if rec.get("tmdb") == 5:
            raise urllib.error.HTTPError("u", 404, "no", {}, io.BytesIO(b""))
        return "drive-my-car", 4.21
    external.letterboxd = lb
    letterboxd.update(reg, set(), set(), {}, now=NOW)
    assert sorted(map(str, asked)) == ["5", "tt14039582"], asked
    assert reg.films["gone"]["meta"] == {"poster": "https://p", "lb": "drive-my-car", "lbRating": 4.21, "lbAt": TODAY}
    assert reg.films["missing"]["meta"] == {"lbAt": TODAY}   # 404: asked, nothing there
    assert {"gone", "missing"} <= reg.touched
    asked.clear()
    letterboxd.update(reg, set(), set(), {}, now=NOW)
    assert asked == []   # not listed, not wanted: never again


def test_refusing_stops_the_run():
    reg = registry({f"f{i}": {"tmdb": i, "meta": {}} for i in range(1, 4)})
    asked = []
    def lb(rec):
        asked.append(rec["tmdb"])
        raise urllib.error.HTTPError("u", 429, "slow down", {}, io.BytesIO(b""))
    external.letterboxd = lb
    letterboxd.update(reg, set(), set(), {}, now=NOW)
    assert asked == [1] and not any(f["meta"] for f in reg.films.values())   # nothing marked asked: tried again next run


def test_migrate():
    reg = registry({"fw-a": {"imdb": "tt1", "meta": {}}, "fw-b": {"imdb": "tt2", "meta": {"lb": "b", "lbChecked": "2026-11-17"}},
                    "fw-c": {"imdb": "tt3", "meta": {}}, "fw-d": {"imdb": "tt4", "meta": {"lbAt": "2026-11-01"}}})
    reg.aliases["cm-a"] = "fw-a"
    legacy = {"cm-a": {"lbSlug": "a-film", "lbRating": 3.9, "rated": "2026-11-15"}, "fw-c": {"posterPath": "/c.jpg"}}
    letterboxd.migrate(reg, legacy, TODAY)
    assert reg.films["fw-a"]["meta"] == {"lb": "a-film", "lbRating": 3.9, "lbAt": "2026-11-15"}
    assert reg.films["fw-b"]["meta"] == {"lb": "b", "lbAt": "2026-11-17"}   # the old one-off lookup
    assert reg.films["fw-c"]["meta"] == {}                                  # never rated before: asked like a new film
    assert reg.films["fw-d"]["meta"] == {"lbAt": "2026-11-01"}


def test_apply():
    reg = registry({"f": {"meta": {"lb": "heat-1995", "lbRating": 4.2}}, "g": {"meta": {}}})
    f = {"id": "f", "ext": {"imdb": "tt1"}}
    g = {"id": "g", "ext": {"imdb": "tt2"}}
    h = {"id": "f", "ext": {}}   # a conflicting match: no links at all
    letterboxd.apply(reg, [f, g, h])
    assert f["ext"] == {"imdb": "tt1", "lb": "heat-1995", "lbRating": 4.2}
    assert g["ext"] == {"imdb": "tt2", "lb": ""}
    assert h["ext"] == {}


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"{len(tests)} passed")
