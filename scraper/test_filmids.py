"""Tests for scraper/filmids.py. Run: python3 scraper/test_filmids.py (no network, no database)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import filmids  # noqa: E402

NOW = "2026-11-14T10:00:00+00:00"
LATER = "2026-11-20T10:00:00+00:00"


def film(title, ids, tmdb=None, imdb=None, shows=(), year="2026"):
    ext = {k: v for k, v in (("tmdb", tmdb), ("imdb", imdb)) if v}
    return {"title": title, "alt": "", "year": year, "ids": list(ids), "ext": ext, "links": [{"url": u, "label": ""} for u in ids],
            "series": [], "status": "on_sale" if shows else "announced", "director": "", "runtime": 0, "blurb": "", "poster": "",
            "premiere": "", "shows": [{"t": t, "cinema": c, "ticket": "https://t.example/" + t} for t, c in shows]}


def roundtrip(reg):
    return filmids.Registry.from_json(json.loads(json.dumps(reg.to_json())))


def test_relisted_film_keeps_its_id():
    """The watchlist bug: a film listed by one source, gone, then listed by another source, keeps its id."""
    reg = filmids.Registry()
    out = filmids.resolve(reg, {"oslo": [film("Klara", ["cm-klara"], tmdb=858035, shows=[("2026-11-15T19:00", "Cinemateket")])]}, NOW)
    fid = out["oslo"][0]["id"]
    assert fid == "cm-klara"
    reg = roundtrip(reg)
    reg.source = "supabase"
    out = filmids.resolve(reg, {"oslo": []}, LATER)   # no cinema shows it
    reg = roundtrip(reg)
    out = filmids.resolve(reg, {"oslo": [film("Klara og solen", ["fw-EDI20260950"], tmdb=858035)]}, LATER)
    f = out["oslo"][0]
    assert f["id"] == "cm-klara", f["id"]
    assert f["ids"][0] == "cm-klara" and "fw-EDI20260950" in f["ids"]
    assert reg.film_of("fw-EDI20260950") == "cm-klara"


def test_tmdb_arriving_later_merges_into_older_film():
    reg = filmids.Registry()
    filmids.resolve(reg, {"oslo": [film("Old", ["cm-old"], tmdb=1)]}, NOW)
    reg = roundtrip(reg)
    out = filmids.resolve(reg, {"winnipeg": [film("New", ["cc-new"])]}, LATER)  # no TMDB match yet: a new film
    assert out["winnipeg"][0]["id"] == "cc-new"
    reg = roundtrip(reg)
    out = filmids.resolve(reg, {"winnipeg": [film("New", ["cc-new"], tmdb=1)]}, "2026-11-21T10:00:00+00:00")
    f = out["winnipeg"][0]
    assert f["id"] == "cm-old" and "cc-new" in f["ids"]
    assert reg.merges == [("cc-new", "cm-old")]
    assert reg.films["cc-new"]["merged"] == "cm-old" and reg.canon("cc-new") == "cm-old"
    assert reg.new_aliases["cc-new"] == ("cm-old", "merged") and reg.merges.count(("cc-new", "cm-old")) == 1


def test_different_tmdb_never_merged():
    reg = filmids.Registry()
    filmids.resolve(reg, {"oslo": [film("A", ["fw-a"], tmdb=1), film("B", ["fw-b"], tmdb=2)]}, NOW)
    reg = roundtrip(reg)
    # A wrong source merge glued both listings into one entry with B's TMDB id: no merge, B's id wins, A keeps its listing.
    out = filmids.resolve(reg, {"oslo": [film("A/B", ["fw-a", "fw-b"], tmdb=2)]}, LATER)
    assert out["oslo"][0]["id"] == "fw-b"
    assert reg.merges == [] and reg.film_of("fw-a") == "fw-a"
    assert "fw-a" not in out["oslo"][0]["ids"]  # the other film's listing isn't counted as this one's


def test_same_film_twice_in_a_region_is_combined():
    reg = filmids.Registry()
    out = filmids.resolve(reg, {"oslo": [
        film("Heat", ["fw-1"], tmdb=949, shows=[("2026-11-15T19:00", "Vega")]),
        film("Heat", ["fw-2"], tmdb=949, shows=[("2026-11-16T19:00", "Saga"), ("2026-11-17T19:00", "Saga")]),
    ]}, NOW)
    assert len(out["oslo"]) == 1
    f = out["oslo"][0]
    assert f["id"] == "fw-1" and len(f["shows"]) == 3 and set(f["listings"]) == {"fw-1", "fw-2"}
    assert [s["id"] for s in f["shows"]] == ["fw-1|2026-11-15T19:00|Vega", "fw-1|2026-11-16T19:00|Saga", "fw-1|2026-11-17T19:00|Saga"]


def test_global_film_across_regions():
    reg = filmids.Registry()
    out = filmids.resolve(reg, {"oslo": [film("Clayface", ["fw-c"], tmdb=5)], "winnipeg": [film("Clayface", ["cc-c"], tmdb=5)]}, NOW)
    assert out["oslo"][0]["id"] == out["winnipeg"][0]["id"] == "fw-c"
    assert out["winnipeg"][0]["ids"][:2] == ["fw-c", "cc-c"]


def test_showing_ids_are_frozen_through_a_merge():
    reg = filmids.Registry()
    filmids.resolve(reg, {"oslo": [film("Old", ["cm-old"], tmdb=1)]}, NOW)
    reg = roundtrip(reg)
    out = filmids.resolve(reg, {"winnipeg": [film("New", ["cc-new"], shows=[("2026-11-25T19:00", "Globe")])]}, LATER)
    sid = out["winnipeg"][0]["shows"][0]["id"]
    assert sid == "cc-new|2026-11-25T19:00|Globe"
    # The database now has that showing (as Supabase would return it), then the film is merged into cm-old.
    reg = roundtrip(reg)
    reg.shows[sid] = ("cc-new", "Globe", "2026-11-25T19:00", "winnipeg", False)
    out = filmids.resolve(reg, {"winnipeg": [film("New", ["cc-new"], tmdb=1, shows=[("2026-11-25T19:00", "Globe")])]}, "2026-11-21T10:00:00+00:00")
    assert out["winnipeg"][0]["id"] == "cm-old"
    assert out["winnipeg"][0]["shows"][0]["id"] == sid


def test_chained_merges_keep_showing_ids_frozen():
    """Two merges in one run (A into C, then B into C), where A and B had the same showing: the surviving row is the one the database
    keeps (A's, moved first), and the payload never sends a row that would clash with the unique (film, cinema, start) key."""
    reg = filmids.Registry()
    for fid, first in (("fw-c", "2026-01"), ("fw-b", "2026-02"), ("fw-a", "2026-03")):
        reg.films[fid] = {"title": fid, "year": "", "tmdb": 7 if fid == "fw-c" else None, "imdb": None, "merged": None, "first": first}
        reg.aliases[fid] = fid
    T = "2026-11-20T19:00"
    reg.shows = {f"fw-a|{T}|Vega": ("fw-a", "Vega", T, "oslo", False), f"fw-b|{T}|Vega": ("fw-b", "Vega", T, "oslo", False)}
    # one entry glues A and B (by listing) and carries C's TMDB id
    out = filmids.resolve(reg, {"oslo": [film("X", ["fw-a", "fw-b"], tmdb=7, shows=[(T, "Vega")])]}, NOW)
    f = out["oslo"][0]
    assert f["id"] == "fw-c"
    assert f["shows"][0]["id"] == f"fw-a|{T}|Vega", f["shows"][0]["id"]
    assert len(reg.merges) == len(set(reg.merges)) == 2
    assert reg.shows[f"fw-b|{T}|Vega"][0] == "fw-b" and reg.shows[f"fw-b|{T}|Vega"][4]   # left on the loser, gone


def test_showing_id_collision_gets_a_suffix():
    reg = filmids.Registry()
    reg.shows["x|2026-11-25T19:00|Globe"] = ("other", "Globe", "2026-11-25T19:00", "winnipeg", False)
    reg.films["x"] = {"title": "", "year": "", "tmdb": None, "imdb": None, "merged": None, "first": "2026"}
    reg.aliases["x"] = "x"
    out = filmids.resolve(reg, {"winnipeg": [film("X", ["x"], shows=[("2026-11-25T19:00", "Globe")])]}, NOW)
    assert out["winnipeg"][0]["shows"][0]["id"] == "x|2026-11-25T19:00|Globe~2"


def test_history_links_old_listings():
    reg = filmids.Registry()
    out = filmids.resolve(reg, {"oslo": [film("Digger", ["fw-WAB20240252"], tmdb=1248832)]}, NOW,
                          {"cc-digger-2026": {"tmdb": 1248832}, "cc-unknown": {"tmdb": 9}})
    f = out["oslo"][0]
    assert "cc-digger-2026" in f["ids"] and "cc-unknown" not in reg.aliases
    assert reg.kinds["cc-digger-2026"] == "history"


def test_gone_cinemas_guard():
    reg = filmids.Registry()
    reg.db_shows = {f"s{i}": ("f", "Saga" if i < 10 else "Vega", f"2026-11-2{i % 9}T19:00", "oslo", False) for i in range(20)}
    films = [{"shows": [{"cinema": "Saga"} for _ in range(6)] + [{"cinema": "Vega"} for _ in range(2)]}]
    ok, skipped = filmids.gone_cinemas(reg, "oslo", films, "2026-11-14T10:00")
    assert ok == ["Saga"] and skipped == ["Vega"]


def test_deterministic_without_registry():
    """A run that can't reach the database and has no local copy makes the same ids."""
    a, b = filmids.Registry(), filmids.Registry()
    fs = lambda: {"oslo": [film("A", ["fw-a", "cm-a"], shows=[("2026-11-15T19:00", "Vega")])]}
    ra, rb = filmids.resolve(a, fs(), NOW), filmids.resolve(b, fs(), LATER)
    assert ra["oslo"][0]["id"] == rb["oslo"][0]["id"] == "fw-a"
    assert ra["oslo"][0]["shows"][0]["id"] == rb["oslo"][0]["shows"][0]["id"]


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"{len(tests)} passed")
