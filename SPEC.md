# Kinoprogram — spec

Browse what's on at the cinemas in a region **by film** instead of by date, and get a daily email
when films become bookable. Regions: **Oslo** and **Westman** (Brandon / Virden area, Manitoba).
For one person and a few friends; must cost nothing to run.

## Sources

| Source | Covers | How |
| --- | --- | --- |
| Filmweb | Every Oslo cinema on Filmweb (ODEON, Saga, Ringen, Vega, Klingenberg, Colosseum, Vika, Symra, Gimle, Kunstnernes Hus) | Public GraphQL API `movieinfoqs.filmweb.no/graphql`: `getCurrentMovies` (on sale) and `getUpcomingMovies` (announced) |
| Cinemateket | Cinemateket i Oslo (Tancred, Lillebil) | HTML: `/forestillinger/side-N` for the film list, each film page for its showings, ticket links and facts |

**Westman** (times in Manitoba time):

| Source | Covers | How |
| --- | --- | --- |
| Landmark Cinemas | Landmark Brandon, full schedule months ahead | Embedded JSON on its Brandon showtimes page, saved to `state/landmark-brandon.json` and used when fresh (≤36 h). Landmark refuses automated requests (tested 2026-10-01: even from a Canadian VPN exit a script gets 403, while a browser gets in), so this needs a real visit (planned: a "Send to Kino" bookmark). Until then CinemaClock covers Landmark. |
| CinemaClock | Landmark (fallback), Evans, Gaiety (Glenboro), Community Theatre (Carnduff); Derrick (Virden), Avalon (Souris), Moosomin picked up automatically if CinemaClock lists them | Server-rendered theatre pages, about a week ahead |
| Evans Theatre | Brandon University's cinema, whole season | Static site, one page per film |

Small theatres sell at the door: their showings link to the theatre page and count as "on sale".
Landmark re-releases: the year comes from the title ("(1978)", "20th Anniversary") or is left empty.

A film shown at more than one source is merged into one entry (normalised title, production year within ±1, so
two different films with the same title stay separate). Version suffixes such as "– 70mm" and a
trailing "(1946)" in the title are ignored when matching; the latter is used as the year.

## Definitions

- **On sale**: a film with at least one future showing that has a ticket link.
- **Announced**: a film Filmweb lists as upcoming, or a film with showings whose tickets are not on sale yet.
- **New**: a film that becomes on sale after having **no** on-sale showings for more than 7 days
  (or never). A new version or format of a film already on sale (e.g. a 70mm run) is not new.
  The first run records a baseline and reports nothing.
- **Newly announced**: a film that gets a concrete Norwegian date (Filmweb premiere date, or
  showings) for the first time while not yet on sale. Films with no date don't trigger until they
  get one; a film dropping from on sale back to announced doesn't trigger.
- **Dubbed showing**: tagged "Norsk tale" for a film not produced in Norway.
- **English subtitles**: Filmweb tag "Engelsk tekst", or Cinemateket fact "Tekst: Engelsk".

## Site (GitHub Pages)

- Poster grid; clicking a poster opens the film with all its showings grouped by day, each with a
  ticket link. Deep link: `#film/<id>`.
- Region switcher (Oslo / Westman), remembered per browser; `?r=westman` links to a region.
- Views: **On sale**, **Coming soon** (announced), **Watchlist**.
- Sort: newest in cinemas (default), next showing, A–Z, most showings, last chance.
  Coming soon: soonest first, newly announced, A–Z.
- Film page links: Letterboxd (with its average rating), IMDb, Rotten Tomatoes, Metacritic, found via
  Wikidata and cached in `state/external.json`. Only confident matches (IMDb id, film not series,
  year ±1 using the Norwegian premiere year when Filmweb has no production year, running time
  within 10 min, a single candidate) get direct links; otherwise a Letterboxd search link.
  Letterboxd is always reached via the IMDb id, never via Wikidata's stored slug.
- Filters: search (Norwegian, English and original title, director, series), time window, my
  cinemas, hide Norwegian dubs, English subtitles only. Nothing is hidden by default.
- **English titles** are always shown when we found a genuine one (not just the untranslated
  original) for a confidently matched film, with the Norwegian title underneath on the film page.
- On the film page, clicking a cinema tag narrows its showings to that cinema (several can be picked).
- No login needed to browse; filters are remembered in the browser.
- **Sign in** with a magic link (enter email → click link; no password). Signed-in users get:
  settings synced across devices, a watchlist, and the daily email.

## Daily email

- Sent at about 09:00 local time per region, to subscribers who chose that region (default Oslo),
  only when their personalised list is non-empty.
- **New on sale**: films that became new since the previous email, filtered by the subscriber's
  settings (my cinemas, hide dubs, English subtitles only). Watchlist films appear first, and can
  be set to always be included regardless of filters.
- **Newly announced** (can be turned off): films newly announced since the previous email, with
  their premiere date. Filters apply only where showings are known.
- Every email has a one-click unsubscribe link.

## Infrastructure (all free tiers)

- **GitHub Actions** runs `scraper/build.py` six times a day (refreshes the site data) and
  `scraper/digest.py` once a day after 09:00 Oslo. State (`state/seen.json`) is committed back to
  the repo.
- **GitHub Pages** hosts `site/` plus the generated `data/films.json`.
- **Supabase** (free) stores accounts (magic-link auth) and a `profiles` row per user:
  settings, watchlist, subscription, unsubscribe token. Row-level security limits each user to
  their own row; the daily job reads subscribers with the secret key.
- **Gmail** (dedicated account, app password) sends the emails over SMTP.

## Later / out of scope for v1

- More Oslo-area venues (Deichman, Cinema Neuf, Weird and Wonderful Cinema, FilmrullKlubb).
- Derrick Theatre (Virden): Facebook only; best route is getting it listed on CinemaClock.
- Per-user alert when a specific watchlist film gets a new showing (beyond becoming new).
- Push notifications.
