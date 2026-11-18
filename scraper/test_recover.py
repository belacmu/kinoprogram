"""Tests for external.recover_films (watchlist films from before the film registry). Run: python3 scraper/test_recover.py"""
import sys
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


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"{len(tests)} passed")
