"""Tests for scraper/details.py (film details: one TMDB request per film). Run: python3 scraper/test_details.py (no network)."""
import copy
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import details  # noqa: E402
import external  # noqa: E402
import filmids  # noqa: E402

NOW = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)
TODAY = "2026-11-18"
P = details.POSTER

# What /movie/{id}?append_to_response=credits,release_dates,watch/providers returns (shortened).
TMDB = {"id": 2, "title": "Klara and the Sun", "original_title": "Klara and the Sun", "original_language": "en", "poster_path": "/k.jpg",
        "overview": "A robot.", "runtime": 101,
        "credits": {"crew": [{"job": "Director", "name": "Kogonada"}, {"job": "Writer", "name": "X"}]},
        "release_dates": {"results": [{"iso_3166_1": "ES", "release_dates": [{"type": 4, "release_date": "2026-12-12T00:00:00.000Z"}]}]},
        "watch/providers": {"results": {"NO": {"link": "https://www.themoviedb.org/movie/2/watch?locale=NO",
                                               "flatrate": [{"provider_name": "Netflix", "logo_path": "/nf.jpg", "display_priority": 1}]}}}}


def registry(films):
    reg = filmids.Registry()
    reg.films = copy.deepcopy(films)
    reg.aliases = {i: i for i in films}
    reg.source = "supabase"
    return reg


def test_from_tmdb_and_store():
    got = details.from_tmdb(TMDB, ["Klara og solen"])
    assert got == {"en": "Klara and the Sun", "tmdbPoster": P + "/k.jpg", "blurb": "A robot.", "runtime": 101, "director": "Kogonada",
                   "lang": "en"}, got
    # TMDB's description, poster and language replace; the English title, running time and director only fill gaps.
    f = {"meta": {"en": "Klara", "blurb": "old", "director": "From the listing", "poster": "https://cinema/x.jpg"}}
    details.store(f, got, TODAY)
    assert f["meta"] == {"en": "Klara", "blurb": "A robot.", "director": "From the listing", "poster": "https://cinema/x.jpg",
                         "tmdbPoster": P + "/k.jpg", "lang": "en", "runtime": 101, "tmdbAt": TODAY}, f["meta"]
    g = {"meta": {}}
    details.store(g, got, TODAY)
    assert g["meta"]["poster"] == P + "/k.jpg"   # no poster at all: TMDB's


def test_migrate_moves_earlier_details_once():
    reg = registry({"fw-a": {"title": "A", "tmdb": 1, "meta": {}},
                    "fw-b": {"title": "B", "tmdb": 2, "meta": {"poster": P + "/b.jpg", "tmdbCard": "2026-11-17"}},
                    "fw-c": {"title": "C", "tmdb": 3, "meta": {}},
                    "fw-d": {"title": "D", "tmdb": 4, "meta": {"tmdbAt": "2026-11-01"}}})
    reg.aliases["cm-a"] = "fw-a"
    legacy = {"cm-a": {"posterPath": "/a.jpg", "overview": "About A.", "lang": "no", "tmdbChecked": "2026-11-10"}}
    details.migrate(reg, legacy, TODAY)
    assert reg.films["fw-a"]["meta"] == {"tmdbPoster": P + "/a.jpg", "blurb": "About A.", "lang": "no", "tmdbAt": "2026-11-10"}
    assert reg.films["fw-b"]["meta"] == {"poster": P + "/b.jpg", "tmdbPoster": P + "/b.jpg", "tmdbAt": "2026-11-17"}  # old fill_cards
    assert reg.films["fw-c"]["meta"] == {}                         # nothing earlier: fetched like a new film
    assert reg.films["fw-d"]["meta"] == {"tmdbAt": "2026-11-01"}   # already done


def test_due():
    old = (NOW.date() - timedelta(days=40)).isoformat()
    recent = (NOW.date() - timedelta(days=3)).isoformat()
    films = {"recovered": {"tmdb": 1, "meta": {}}, "new-listed": {"tmdb": 2, "meta": {}}, "new-gone": {"tmdb": 3, "meta": {}},
             "listed-stale": {"tmdb": 4, "meta": {"tmdbAt": old}}, "listed-fresh": {"tmdb": 5, "meta": {"tmdbAt": recent}},
             "wanted-due": {"tmdb": 6, "meta": {"tmdbAt": recent}}, "wanted-fresh": {"tmdb": 7, "meta": {"tmdbAt": recent}},
             "gone-old": {"tmdb": 8, "meta": {"tmdbAt": old}}, "no-tmdb": {"meta": {}}, "merged": {"tmdb": 9, "merged": "x", "meta": {}}}
    reg = registry(films)
    states = {"wanted-due": {"checked": NOW - timedelta(days=20), "found": False, "digital": []},
              "wanted-fresh": {"checked": NOW - timedelta(days=2), "found": False, "digital": []}}
    got = details.due(reg, {"new-listed", "listed-stale", "listed-fresh"}, {"wanted-due", "wanted-fresh"}, states, NOW,
                      first={"recovered"})
    # Never fetched (recovered, listed, then the rest), where to watch due, listed and stale. Not: fresh, gone, no TMDB, merged.
    assert got == ["recovered", "new-listed", "new-gone", "wanted-due", "listed-stale"], got
    assert details.due(reg, set(), set(), {}, NOW, budget=2) == ["new-gone", "new-listed"]


def test_update_one_request_per_film():
    reg = registry({"fw-klara": {"title": "Klara og solen", "tmdb": 2, "meta": {}}})
    asked = []
    external.tmdb_enabled = lambda: True
    external.tmdb_get = lambda path, **kw: asked.append((path, kw)) or TMDB
    filmids._config = lambda: (None, None)   # no database: nothing marked, nothing written
    rows = details.update(reg, {"fw-klara"}, {}, ["CA", "ES", "NO"], now=NOW)
    assert asked == [("/movie/2", {"language": "en-US", "append_to_response": "credits,release_dates,watch/providers"})], asked
    m = reg.films["fw-klara"]["meta"]
    assert m["blurb"] == "A robot." and m["tmdbPoster"] == P + "/k.jpg" and m["tmdbAt"] == TODAY and "fw-klara" in reg.touched
    by = {r["country"]: r for r in rows}
    assert by["NO"]["options"]["stream"][0]["name"] == "Netflix" and by["ES"]["options"] == {"digital": "2026-12-12"}
    assert by["CA"]["options"] == {}
    asked.clear()
    assert details.update(reg, {"fw-klara"}, {}, ["NO"], now=NOW) == [] and not asked   # fetched today: not again


def test_update_without_tmdb_still_migrates():
    reg = registry({"fw-a": {"title": "A", "tmdb": 1, "meta": {}}})
    external.tmdb_enabled = lambda: False
    details.update(reg, set(), {"fw-a": {"overview": "About A."}}, ["NO"], now=NOW)
    assert reg.films["fw-a"]["meta"]["blurb"] == "About A."


def film(listing, fid, poster="", tmdb=1):
    return {"id": fid, "listing": listing, "poster": poster, "ext": {"tmdb": tmdb} if tmdb else {}, "blurb": "?"}


def test_apply_like_before():
    reg = registry({"f": {"tmdb": 1, "meta": {"tmdbPoster": P + "/t.jpg", "blurb": "About.", "lang": "ja"}}})
    cases = [
        (film("cm-x", "f", "https://vrs.gd/still.jpg"), {"poster": P + "/t.jpg"}),          # Cinemateket's still: replaced
        (film("rv-x", "f", "https://img.evbuc.com/e.jpg"), {"poster": P + "/t.jpg"}),       # Revier's (Eventbrite) still: replaced
        (film("fw-x", "f", ""), {"poster": P + "/t.jpg"}),                                  # none: TMDB's
        (film("fw-x", "f", "https://filmweb/p.jpg"), {"poster": "https://filmweb/p.jpg"}),  # Filmweb's own: kept, no fallback
        (film("cc-x", "f", "https://cinemaclock/p.jpg"), {"poster": "https://cinemaclock/p.jpg", "poster2": P + "/t.jpg"}),
        (film("mk-x", "f", "https://mk2/p.jpg"), {"poster2": P + "/t.jpg"}),
        # The listing's own id decides, not the film's (here first seen in Winnipeg, listed by Filmweb in Oslo): no fallback.
        (film("fw-y", "cc-first-seen", "https://filmweb/p.jpg"), {"poster2": None}),
    ]
    reg.films["cc-first-seen"] = reg.films["f"]
    reg.aliases["cc-first-seen"] = "cc-first-seen"
    for f, want in cases:
        details.apply(reg, [f])
        for k, v in want.items():
            assert f.get(k) == v, (f, k, v)
        assert f["blurb"] == "About." and "listing" not in f
        assert f["ext"].get("lang") == ("ja" if f["id"] == "f" and str(want).find("mk2") > -1 else f["ext"].get("lang"))
    # The language only for Costa del Sol's listings (the only place it's used).
    f = film("mk-y", "f")
    details.apply(reg, [f])
    assert f["ext"]["lang"] == "ja"
    f = film("fw-y", "f")
    details.apply(reg, [f])
    assert "lang" not in f["ext"]
    # Matched through another of its listings: the links' listing has only an IMDb id, the film has a TMDB id. Gets TMDB's.
    f = film("cc-x", "f", tmdb=None)
    f["ext"] = {"imdb": "tt1"}
    details.apply(reg, [f])
    assert f["blurb"] == "About."
    # A conflicting match (no ext at all): no description, poster left alone.
    f = film("cm-z", "f", "https://vrs.gd/still.jpg", tmdb=None)
    details.apply(reg, [f])
    assert f["blurb"] == "" and f["poster"] == "https://vrs.gd/still.jpg"
    # Not matched on TMDB at all: the same.
    reg.films["g"] = {"meta": {"blurb": "x"}}
    reg.aliases["g"] = "g"
    f = film("cm-z", "g", "", tmdb=None)
    f["ext"] = {"imdb": "tt2"}
    details.apply(reg, [f])
    assert f["blurb"] == ""
    # Matching's language comes first.
    f = film("cl-x", "f")
    f["ext"]["lang"] = "es"
    details.apply(reg, [f])
    assert f["ext"]["lang"] == "es"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"{len(tests)} passed")
