# Stable film and showing ids: design

Status: **draft, waiting for approval**. Nothing here is built yet.

## What I found (confirmed by reading the code and the live data)

- **The watchlist bug.** `requestWatch` stores only `f.id`, the *first* listing id at the time. `isWatched` checks
  `f.ids.some(...)`, where `f.ids` is the film's *current* listing ids. When the old listing disappears and the film
  comes back under another source, `f.ids` no longer has the stored id, so the heart goes empty. Same for
  `prefs.hidden`, friends' initials, `watchlist_added` and the email.
- **How often.** `state/external.json`'s git history (2 weeks, 39 versions) has 60 films whose old listing id is gone
  while a different listing of the same TMDB film is live. Most are cross-region (a Winnipeg listing gone, the
  same film live in Oslo); a few are the same region switching source (Filmweb ↔ Cinemateket, MovieScout ↔ CinemaClock).
- **Listing ids aren't even unique today.** In Winnipeg, `https-moviescout-ca-movies-10003477` is in both "Dune: Part
  Two" and "Dune: Part Two The IMAX Experience". Oslo has two "Heat" entries (two Filmweb ids, one TMDB film).
- **External ids are good but incomplete.** TMDB or IMDb match, live data today:

  | Region | Films | Matched | Matched among on sale |
  | --- | --- | --- | --- |
  | Oslo | 408 | 321 (79%) | 218 / 270 |
  | Westman | 50 | 47 (94%) | 29 / 32 |
  | Winnipeg | 63 | 57 (90%) | 39 / 45 |
  | Costa del Sol | 38 | 21 (55%) | 21 / 38 |

  A match can also arrive days after the film first appears (60 Wikidata lookups per run), and the cache in
  `state/external.json` is itself keyed by listing id. So TMDB/IMDb can't *be* the id; they're used to *link* a new
  listing to a film we already know. MovieScout's ids are exact; Wikidata/TMDB matches are conservative.
- **Plans.** As you described: text copies, `showing_id = "<film id>|<start>|<cinema>"`, no foreign keys.
  `save_plan` is the only writer; party merging and the invite token are server-side.
- **Other id users I found besides the ones you listed:** `prefs.hidden` (keyed by listing id), the email's heart link
  `#watch/<id>`, share links `#film/<id>` and the preview pages `site/f/<region>/<id>/`, `state/external.json`, and
  `external.py`'s poster rule that looks at the `f.id` prefix.
- **Side finding (not part of this work):** `deploy-site.yml` copies `films.json`, `westman.json` and
  `costadelsol.json` from the live site but not `winnipeg.json`, so a code-only deploy publishes no Winnipeg data
  until the next programme update. Worth a one-line fix; I haven't touched it.

## The ids

**Film id: the first listing id a film was ever seen with, frozen.** For example `fw-EDI20261336`.
- Every film in today's data keeps exactly the id it has now (`f.id`), so most existing watchlist entries, plans,
  share links, events and state keys are *already* the stable id. That makes the migration small.
- Deterministic: if Supabase is unreachable, the scraper mints the same id it would have got anyway.
- Downside: the prefix names the source where the film was first seen, not where it plays now (a Winnipeg film may
  end up with an `fw-` id). Cosmetic only.
- Alternative: opaque ids (`f_7k2m9q`). Cleaner, but every existing reference would need rewriting. Not recommended.

**Identity is global, not per region.** One film row for "Clayface" whether it's in Oslo or Winnipeg. Your watchlist is
already one list across regions; this makes it actually work across them, and friends in other regions match too.

**How the scraper resolves a film each run** (in `build.py`, Python, so I can test it here):
1. Any of its listing ids already known (`film_aliases`) → that film.
2. Otherwise its TMDB id, else IMDb id, matches a known film → that film (this is the fix for the bug).
3. Otherwise a new film, id = its first listing id.
4. New listing ids are recorded as aliases of the film, forever.
5. If one scraped film resolves to two known films (its listings were separate films before, or its TMDB id now
   points at an older film), the newer film is **merged** into the older: `merged_into` is set, never deleted.

**Showing id: `"<film id>|<start>|<cinema>"` when first seen, frozen.** The same string plans use today, so existing
plans mostly line up. Stored, not recomputed: if the film is later merged, the showing keeps its id and only its
`film_id` changes, so plans keep pointing at it. The scraper finds existing showings by (film, cinema, start).

## Tables (new)

```
films          id text pk, title text, year text, tmdb int, imdb text,
               merged_into text → films, first_seen, last_seen timestamptz, gone_at timestamptz
film_aliases   alias text pk, film_id text → films, kind text ('listing' | 'merged' | 'history'), created_at
showings       id text pk, film_id text → films, region text, cinema text, starts text ("2026-11-07T19:00", local),
               title text (the film's title in that region's listing), ticket text (http/https only),
               first_seen, last_seen, gone_at timestamptz, replaced_by text → showings
               unique (film_id, cinema, starts)
```

- Readable by everyone (it's the public programme, same as the JSON); written only with the secret key.
- **Gone, never deleted.** After each region is built, its future showings that weren't seen get `gone_at`; seen
  again → cleared. Showings that simply *started* are not gone, just past. A film in no region's data gets `gone_at`.
- **Not if a source failed.** If a source errors in a run (e.g. Cinemateket down), nothing in that region is marked
  gone that run, otherwise every Cinemateket plan would flash "no longer listed".
- Growth: about 5k showing rows a week at most; that's well under the free tier's 500 MB for years.
- The scraper reads the alias table and upcoming showings at the start (paged; PostgREST returns 1000 rows per
  request), and keeps a copy in `state/film-registry.json` so a run without Supabase still gets the same ids.
- **The Supabase step can't fail the build**, same as the Letterboxd lookups: if it errors, the site still updates.

## A showing whose time changes

Two options:
- **Match and update**: guess that the 19:00 that vanished and the 19:30 that appeared are the same showing, change
  its time, and the plan follows silently. Simple for users when the guess is right; when it's wrong (the cinema
  dropped one showing and added another) people are moved to a time they never chose, with no record of it.
- **New showing, old one gone** (recommended): showings never change time. When a showing goes and the same film at
  the same cinema gets a new one on the same day in the same run, the old row gets `replaced_by` the new one. A plan
  on it shows **"Time changed to 19:30. Move my plan"**; one tap moves it (a new `move_plan()` keeps your invite link
  and party, so friends who move land in the same party). No guessing is ever applied without a person seeing it.

## Plans

- `plans.showing_id` gets a real foreign key to `showings.id`.
- `save_plan(showing, seats, joined_via)`: film, cinema, time, region and **ticket come from `showings`**, so the client
  can no longer write a URL. It refuses a showing that doesn't exist or is gone (changing seats on an existing plan
  still works). Party merging, the advisory lock, invite tokens and every security property stay as they are.
- `friends_plans()` and `plan_by_invite()` return the same columns as now, joined from `showings`/`films`, plus
  `gone` and `replaced_by`. Guest checks and `anon` grants unchanged.
- **Migration:** every existing plan whose showing isn't in `showings` (past, or changed) gets a `gone` showing row
  built from the plan's own copy (and a gone film row if needed), then the foreign key is added. No plan is lost.
- **Live site safety** (preview shares the database): the SQL keeps the old 9-argument `save_plan` as a wrapper that
  ignores the copied fields, and keeps the old columns, filled by the server from `showings`. A later cleanup SQL,
  after the new site is live, drops the wrapper and the copy columns.

## Watchlist

- `profiles.watchlist` stays a `text[]` (no foreign key: an array can't have one, and a strict check would reject a
  watchlist add for a brand-new film if a sync was missed). Instead:
- **The JSON's `ids` becomes every id the film has ever had**: the stable id first, then merged film ids and every
  listing id. So an old entry still matches even before migration, and the existing `f.ids.some(...)` code works as is.
- **A trigger normalises on write**: any alias in a saved watchlist is replaced by its film id, so old cached pages
  sending listing ids are fine. The `watchlist_added` stamping keeps the original add time when an id is renamed
  (today it would re-stamp it as "now" and the film would show up as a friend's new pick again).
- **Migration (one SQL block):** first a read-only check query you run that shows how many entries would change and
  how many match no known film. Then: back up `watchlist` and `watchlist_added` into a separate `backup` schema; map
  every entry through the aliases; keep entries that match nothing, unchanged (never dropped); merge
  `watchlist_added` keys keeping the earliest time. `prefs.hidden` is left alone: old keys still match through `ids`.
- **Recovering the old bug cases:** before that, a one-off script seeds `film_aliases` (kind `history`) from every
  version of `state/external.json` in git: listing id → TMDB id → today's film. That links the 60 gone listings found
  above. Ids with no TMDB match in history can't be recovered automatically; the check query lists them.
- When a later run merges two films, a service-role function `apply_film_merges()` renames the loser in watchlists,
  `watchlist_added` and showings (and resolves the rare clash where both had the same showing).

## What changes where

- **build.py:** resolve films and showings (above); films that resolve to the same id in one region are combined into
  one entry (e.g. the two Heats, Dune + its IMAX re-release: same TMDB film). JSON: `id` = stable id, `ids` = all
  aliases, each show gets `id`. State files and events keyed by stable id; reads accept old keys, old event `film`
  values are rewritten once. Supabase sync step at the end. `external.py` keeps working on listing ids.
- **app.js:** plans keyed by `s.id`; load plans with the showing joined in; new `save_plan` arguments; "No longer
  listed" / "Time changed, Move my plan" on a showing page and in Coming up. Watchlist code barely changes.
- **digest.py:** "Friends are going" reads plans with the showing embedded; everything else already works through
  `f["ids"]` and event `film` ids.
- **schema.sql:** new tables and functions; safety check allows `films`, `film_aliases`, `showings`; stays complete
  and re-runnable.
- **SPEC.md:** the Friends ("ids are per source") and Showings sections updated.

## Stages

1. **Tables + scraper fills them.** SQL: tables, grants, `apply_film_merges`. Scraper: resolve, sync, new JSON
   fields. The JSON stays compatible with the current site (same fields, `id` unchanged for today's films), so this
   can merge to main; you check the tables in Table Editor after the next run. History seeding script included.
2. **Plans.** SQL (compatible with the live site) + app.js on preview, then merge, then cleanup SQL.
3. **Watchlist.** Check query, migration SQL, trigger; small app.js change; digest. Preview, then merge.

No dashboard settings are needed for any of this, as far as I can tell. I'll say so per stage if that changes.

## Risks

- **Wrong merges stick.** A wrong TMDB match, or `merge()` joining two different films by title, now merges ids for
  good (undoing means a manual SQL fix). Mitigation: TMDB/IMDb only, never title-only, for linking across runs.
- **Showing ids depend on cinema names.** Renaming a cinema in the scraper makes all its showings new (old marked gone).
- **Supabase outages.** Browsing never depends on it; ids come from the local registry copy.
- **Testing limits.** No Postgres here: SQL is reviewed, not executed. The Supabase sync can only really run in
  GitHub Actions (the secret key isn't here); I'll test it against a fake client with the live JSON.

## Decisions I need from you

1. **Global film ids** (one id across regions)? Recommended: yes.
2. **Combine entries with the same TMDB id** in a region's list (two Heats become one card with both showings)?
   Recommended: yes; otherwise two cards would share one id.
3. **Time changes:** new showing + "Move my plan" (recommended), or silent match-and-update?
4. **Id format:** frozen first listing id (recommended) or opaque ids?
