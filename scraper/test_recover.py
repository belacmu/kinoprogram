"""Tests for external.recover_films (watchlist films from before the film registry) and external.fill_letterboxd (Letterboxd for
films no longer listed). Run: python3 scraper/test_recover.py"""
import io
import sys
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import external  # noqa: E402
import filmids  # noqa: E402

NOW = "2026-11-18T12:00:00+00:00"
HISTORY = {"fw-GRI20171335": {"tmdb": 1417, "imdb": "tt0457430"}, "cm-pans-labyrint": {"tmdb": 1417, "imdb": "tt0457430"},
           "cm-donnie-darko": {"imdb": "tt0246578"}, "rv-drive-2011": {"tmdb": 64690, "imdb": "tt0780504"},
           "fw-OLD-HEAT": {"tmdb": 949}}
MOVIES = {1417: {"title": "Pan's Labyrinth", "original_title": "El laberinto del fauno", "release_date": "2006-10-11", "imdb_id": "tt0457430"},
          141: {"title": "Donnie Darko", "release_date": "2001-01-19", "imdb_id": "tt0246578"},
          64690: {"title": "Drive", "release_date": "2011-09-15"}}


def tmdb(asked):
    def get(path, **kw):
        asked.append(path)
        if path.startswith("/find/"):
            return {"movie_results": [{"id": 141}]} if path == "/find/tt0246578" else {"movie_results": []}
        return MOVIES[int(path.rsplit("/", 1)[1])]
    return get


def registry():
    reg = filmids.Registry()
    reg.films = {"fw-heat": {"title": "Heat", "year": "1995", "tmdb": 949, "first": "2026-11-14T00:00:00+00:00"}}
    reg.aliases = {"fw-heat": "fw-heat"}
    return reg


def test_recovers_films_from_the_history():
    reg, asked = registry(), []
    external.tmdb_enabled = lambda: True
    external.tmdb_get = tmdb(asked)
    ids = ["fw-GRI20171335", "cm-pans-labyrint", "cm-donnie-darko", "rv-drive-2011", "fw-OLD-HEAT", "fw-heat", "fw-NOHISTORY"]
    created = external.recover_films(reg, ids, HISTORY, NOW)
    assert created == ["cm-donnie-darko", "cm-pans-labyrint", "rv-drive-2011"], created
    # One film for Pan's Labyrinth: the other cinema's id is its alias (and costs no request).
    assert reg.film_of("fw-GRI20171335") == "cm-pans-labyrint" and asked.count("/movie/1417") == 1
    assert reg.films["cm-pans-labyrint"] == {"title": "Pan's Labyrinth", "year": "2006", "tmdb": 1417, "imdb": "tt0457430",
                                             "merged": None, "first": NOW, "meta": {}}
    # IMDb only: TMDB's id found through /find.
    assert reg.films["cm-donnie-darko"]["tmdb"] == 141 and reg.films["cm-donnie-darko"]["title"] == "Donnie Darko"
    # Already known under another id: linked, not created.
    assert reg.film_of("fw-OLD-HEAT") == "fw-heat" and "fw-OLD-HEAT" not in reg.films
    # No history: stays unknown.
    assert reg.film_of("fw-NOHISTORY") is None
    # Sent to the database like any new film, with its aliases.
    assert set(created) <= reg.touched and reg.new_aliases["fw-GRI20171335"] == ("cm-pans-labyrint", "history")


def test_budget_and_no_tmdb():
    reg, asked = registry(), []
    external.tmdb_get = tmdb(asked)
    external.tmdb_enabled = lambda: False
    assert external.recover_films(reg, ["rv-drive-2011"], HISTORY, NOW) == [] and not asked
    external.tmdb_enabled = lambda: True
    external.RECOVER_BUDGET = 1
    try:
        assert len(external.recover_films(reg, ["rv-drive-2011", "cm-pans-labyrint"], HISTORY, NOW)) == 1
    finally:
        external.RECOVER_BUDGET = 40


def test_letterboxd_for_films_no_longer_listed():
    reg = filmids.Registry()
    reg.films = {"listed": {"title": "Dune", "tmdb": 1, "meta": {}},
                 "gone": {"title": "Drive My Car", "tmdb": 758866, "imdb": "tt14039582", "meta": {"poster": "https://p"}},
                 "rated": {"title": "Heat", "tmdb": 949, "meta": {"lb": "heat-1995", "lbRating": 4.2}},
                 "missing": {"title": "Gone from Letterboxd", "tmdb": 5, "meta": {}},
                 "noids": {"title": "Local short", "meta": {}}}
    asked = []
    def lb(rec):
        asked.append(rec.get("imdb") or rec.get("tmdb"))
        if rec.get("tmdb") == 5:
            raise urllib.error.HTTPError("u", 404, "no", {}, io.BytesIO(b""))
        return "drive-my-car", 4.03
    external.letterboxd = lb
    external.fill_letterboxd(reg, listed={"listed"})
    assert asked == ["tt14039582", 5], asked   # by its exact id; not listed films, nor ones with a slug and rating, nor no ids
    m = reg.films["gone"]["meta"]
    assert m["lb"] == "drive-my-car" and m["lbRating"] == 4.03 and m["poster"] == "https://p" and m["lbChecked"], m
    assert reg.films["missing"]["meta"].get("lbChecked") and "lb" not in reg.films["missing"]["meta"]   # 404: checked, nothing
    asked.clear()
    external.fill_letterboxd(reg, listed={"listed"})
    assert asked == [], asked   # once per film


def test_letterboxd_refusing_stops_the_run():
    reg = filmids.Registry()
    reg.films = {f"f{i}": {"title": str(i), "tmdb": i, "meta": {}} for i in range(1, 4)}
    asked = []
    def lb(rec):
        asked.append(rec["tmdb"])
        raise urllib.error.HTTPError("u", 429, "slow down", {}, io.BytesIO(b""))
    external.letterboxd = lb
    external.fill_letterboxd(reg, listed=set())
    assert asked == [1] and not any(f["meta"] for f in reg.films.values())   # nothing marked checked: tried again next run


def test_card_keeps_the_rating():
    f = {"title": "Heat", "ext": {"lb": "heat-1995", "lbRating": 4.2}, "shows": []}
    assert filmids.card(f, "oslo") == {"lb": "heat-1995", "lbRating": 4.2}


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"{len(tests)} passed")
