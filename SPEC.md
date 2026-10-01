# Kinoprogram — spec

Browse what's on at Oslo cinemas **by film** instead of by date, and get a daily email when
films become bookable. For one person and a few friends; must cost nothing to run.

## Sources

| Source | Covers | How |
| --- | --- | --- |
| Filmweb | Every Oslo cinema on Filmweb (ODEON, Saga, Ringen, Vega, Klingenberg, Colosseum, Vika, Symra, Gimle, Kunstnernes Hus) | Public GraphQL API `movieinfoqs.filmweb.no/graphql`: `getCurrentMovies` (on sale) and `getUpcomingMovies` (announced) |
| Cinemateket | Cinemateket i Oslo (Tancred, Lillebil) | HTML: `/forestillinger/side-N` for the film list, each film page for its showings, ticket links and facts |

A film shown at both is merged into one entry (normalised title, production year within ±1, so
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
- Views: **On sale**, **Coming soon** (announced), **Watchlist**.
- Sort: newest in cinemas (default), next showing, A–Z, most showings, last chance.
  Coming soon: soonest first, newly announced, A–Z.
- Film page links: Letterboxd (with its average rating), IMDb, Rotten Tomatoes, Metacritic, found via
  Wikidata and cached in `state/external.json`. Only confident matches (IMDb id, film not series,
  year ±1, a single candidate) get direct links; otherwise a Letterboxd search link.
- Filters: search (Norwegian, English and original title, director, series), time window, my
  cinemas, hide Norwegian dubs, English subtitles only. Nothing is hidden by default.
- **English titles** toggle (on by default; also used in the email): shows the English title from
  Wikidata when there is a genuine one (not just the untranslated original), with the Norwegian
  title underneath on the film page.
- No login needed to browse; filters are remembered in the browser.
- **Sign in** with a magic link (enter email → click link; no password). Signed-in users get:
  settings synced across devices, a watchlist, and the daily email.

## Daily email

- Sent at about 09:00 Oslo time, only to subscribers whose personalised list is non-empty.
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

- More cinemas/regions (other Oslo-area venues; Virden/Brandon, Manitoba as a separate region).
- Per-user alert when a specific watchlist film gets a new showing (beyond becoming new).
- Push notifications.
