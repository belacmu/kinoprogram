# Cinecrab — spec

Browse what's on at the cinemas in a region **by film** instead of by date, and get a daily email
when films become bookable. Regions: **Oslo**, **Westman** (Brandon / Virden area, Manitoba), **Winnipeg** and
**Costa del Sol**.
For one person and a few friends; must cost nothing to run.

## Sources

| Source | Covers | How |
| --- | --- | --- |
| Filmweb | Every Oslo cinema on Filmweb (ODEON, Saga, Ringen, Vega, Klingenberg, Colosseum, Vika, Symra, Gimle, Kunstnernes Hus) | Public GraphQL API `movieinfoqs.filmweb.no/graphql`: `getCurrentMovies` (on sale) and `getUpcomingMovies` (announced) |
| Cinemateket | Cinemateket i Oslo (Tancred, Lillebil) | HTML: `/forestillinger/side-N` for the film list, each film page for its showings, ticket links and facts |
| Revier Film Club | Free screenings at the Revier hotel (Kongens gate 5), Wednesdays and Fridays 18:00 | Eventbrite only, one event per screening named "Title (year, 1t 40m)": the organizer page's embedded JSON for the list, each event page for director ("Regi: …"), description and ticket release date. Free, but a seat must be reserved, so a showing counts as on sale while Eventbrite has places; "Fully booked" when it doesn't. |
| Deichman Bjørvika | The main library's film screenings (Kinoen, Kinosalen), free | Its Hoopla ticket shop's public JSON API (`/api/public/v3.0/organizations/915950447`; behind a queue-it redirect that only needs cookies kept). The event list holds everything the library does; the film showings are those labelled "Filmvisning" / "Film og samtale" (the "Barnas kino:", "Film fra Sør:" and "Filmvisning:" prefixes and a trailing ". Lør kl. 14" are stripped from the title; year, running time and English subtitles come from the facts line in the description). A film-and-talk night or a double bill keeps its event name and is type Talks & events. Bookable when the event has ticket types (`available_ticket_types`), otherwise "Free tickets from <date>" (tickets open a week ahead, children's screenings the same morning). School and kindergarten "Utekino" screenings are for booked groups and left out. |
| Sommerro | Hotel Sommerro's film evenings: a dinner, brunch or tea **package** and a film, prepaid | HTML: `/kultur/filmopplevelser/` has one block per screening (film, year, date, "(Utsolgt)" / "(Få plasser igjen)"); each series page gives what the package includes. Sold out shows as "Sold out", a few left in the note. Booking is a widget on the site, so a showing links to its series page. |

**Manitoba** (Westman and Winnipeg, times in Manitoba time): CinemaClock for about the next week at every cinema,
MovieScout beyond it (advance sales months ahead).

| Source | Covers | How |
| --- | --- | --- |
| CinemaClock | Every cinema both regions show except Strand and Roxy, and the Cinematheque, about the next week | Server-rendered theatre pages, twice a day |
| MovieScout | Beyond CinemaClock's week: Landmark Brandon, the four Winnipeg Cineplex cinemas and Landmark Grant Park (advance sales months ahead), and whatever the small Westman theatres list; Strand (Melita) and Roxy (Neepawa) entirely | Public showtimes API, **used with MovieScout's permission for this small personal project** on the condition we poll it sparingly. One fetch a day for both regions, one request a second, at most 150 requests a day (normally ~70), cached in `state/moviescout.json` (the day's count is logged in it). **Discovery**: the films playing near a point between the regions (Portage la Prairie) on a date, one request per date (`movies/list/near`, at most 10 films; a full answer two weeks or more out is asked again per city): every date 7–30 days out daily, later dates out to 120 days weekly, plus the Thursday/Friday of each "coming soon" release daily. **Times**: each film and date found, near each city (`showtimes?movie_id&date&lat&lng`), when first seen and again 10 days out. A film with no showings at our cinemas near a city is parked for that city and its first showing there (`showtimes/first-showing`) is checked daily if it plays in the other region (weekly otherwise), so the second region adding it is found the next day. Strand and Roxy: per theatre, the next 14 days, each date every 3 days. A cinema whose CinemaClock page fails gets its next week from MovieScout instead. Titles seen on MovieScout are remembered with their ids, so a film CinemaClock lists keeps its MovieScout id (watchlist, "new") and gets exact IMDb/TMDB ids. |
| MovieScout "coming soon" | Both regions' **Coming soon**: chain releases opening in Canada (has a MovieGlu id, not flagged indie), about 3 months ahead, shown as "Opens in Canada … not scheduled here yet" until a cinema in the region lists showtimes | National upcoming list, 1–2 requests a day. MovieScout also gives each film's exact IMDb/TMDB ids (one request per film, cached), used for links instead of title matching. |
| Evans Theatre | Westman: Brandon University's cinema, whole season | Static site, one page per film |
| Dave Barber Cinematheque | Winnipeg: about a month ahead, a ticket link per showing | Its own site (Filmbot): the calendar and Coming soon pages for the films, each film's page for dates, times, ticket links and facts |

Westman cinemas: Landmark Brandon, Evans Theatre, Gaiety (Glenboro), Derrick (Virden), Avalon (Souris), Strand (Melita),
Roxy (Neepawa), Community Theatre (Carnduff), Moosomin. Winnipeg cinemas: Scotiabank Theatre, Cineplex McGillivray,
Cineplex Kildonan Place, SilverCity St. Vital, Landmark Grant Park, Cinema City Northgate and Garden City Cinemas
(second-run; not on MovieScout, so only CinemaClock's week), and the Cinematheque.
What MovieScout's sparing use doesn't give: times on a far date that a cinema adds after the film was found there
appear 10 days out; a one-off event more than 30 days out that isn't on a release date can take up to a week to
appear; for a day or two after the cinemas publish their next week, the days CinemaClock doesn't reach yet show
only advance showings. MovieScout has no ticket links: its showings link the cinema's page.

Landmark itself refuses automated requests (tested 2026-10-01, even from a Canadian VPN exit), so
it isn't fetched directly. Cineplex's site and API are behind bot protection, so neither is fetched.
Small theatres sell at the door: their showings link to the theatre page and count as "on sale".
Landmark re-releases: the year comes from the title ("(1978)", "20th Anniversary") or is left empty.

A film shown at more than one source is merged into one entry (normalised title, production year within ±1, so
two different films with the same title stay separate). Version suffixes such as "– 70mm" and a
trailing "(1946)" in the title are ignored when matching; the latter is used as the year.

## Meal packages

A showing that comes with a meal (Sommerro always; Vega's dinner screenings when Filmweb says so in the showing's
info) carries the tag **Meal + film** (🍽 on its showing), and Sommerro's note says it's a prepaid package. It costs
far more than a cinema ticket, so the tag keeps it from passing as a normal showing; it is also a format filter on the
film page like IMAX. Films that only play as a meal package are never "new" or "newly announced" (they're always on
offer), so they don't reach the email or What's new. Prices aren't published in a scrapable way, so none are shown.

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
- Region switcher (Oslo / Westman / Winnipeg / Costa del Sol), remembered per browser; `?r=westman` links to a region.
- **One grid with section headers, by when films play**: Playing from today (or tomorrow, or the picked day) · From this week · From next week ·
  From later in <month> · From <month>… A film "starts" at its first showing, or its confirmed premiere if
  earlier. Films without a date are left out (they appear only when you search for them).
  - The first section is a chosen day, Today by default (so nothing asks you to pick); once nothing
    more is on today under your filters, it's the next day something is (usually tomorrow), still
    counting as the default.
  - A day control in the bar: a calendar icon with a label (just the icon only on very narrow
    bars): "Date ▾" on Today, which counts as no filter, or the picked day
    ("Thu 15 Oct ▾"), highlighted like active filters. It opens a month picker with a Today button
    beside the month arrows, as in most calendars (days with nothing playing under the current
    filters are greyed out).
  - Picking a day shows the page as it will be then: that day's films first, with that day's
    cinemas and times on the cards, then everything after it in the usual sections; films only
    playing before it drop out, and a film opened from there starts at (and highlights) that day.
    The section stays even when empty, saying nothing's on. The day isn't remembered: reloading
    goes back to Today; switching region keeps it. Searching ignores it.
- **What's new**: a button in the header (beside the location and account buttons, since it isn't a
  filter) opens a panel (a bottom sheet on phones) of films that
  went on sale or got a date here in the last 30 days, after tracking began, by day (Today ·
  Yesterday · Earlier this week · Last week · Earlier, newest first; the day pins while scrolling).
  Each day has two clearly labelled grids of the usual cards: **Tickets on sale** first, then
  **Newly announced**. One card per film, for what last happened to it; no "New" flags (it's all
  new) and announced films aren't dimmed. Hearts and hiding work as in the grid (hidden films are
  dimmed and last, watchlist films first). Types, cinemas and language filters apply; Tickets and
  the Watchlist switch don't. A card opens the film on top; closing it goes back to the panel.
  Deep link: `#new`.
- Section headers stick below the top bar; clicking one collapses it to a single line (count + first
  titles). The jump bar links to each section (opening it if collapsed) and has Collapse all /
  Expand all.
- Header: the title, then What's new, the location and the account button, always on one row: when
  it's tight they compact only as far as needed (What's new to an icon, the location's pin, "Sign in"
  to an icon, the location's arrow, a smaller title), else the buttons wrap under the title.
- Top bar (the filters): **All films / ♥ Watchlist**, then a search field (always a field, phones
  too; Escape clears it), the day control, and on narrow screens a Filters button. From 640px wide
  the switch and the bar share one row; on phones they're two rows. The day control keeps its label
  and the search field gives way; while you type in a narrow search field it takes the whole bar.
- Defaults: order within sections is best rated first; only Films are shown (Shorts, Live & stage and
  Talks & events are opt-in; searching still finds every type); picking a specific cinema switches
  Tickets to "On sale", and going back to all cinemas switches it back to "All". Tickets
  (All / On sale / Not on sale yet) narrows the grid.
- Filters live in a sidebar (a slide-in panel on narrow screens, with Reset and "Show N films" pinned
  at the bottom): Order within sections (by date / best rated first / fewest showings first), Tickets,
  Types, Cinemas (two columns), and Oslo's language options.
- Films without tickets on sale have dimmed posters and a "Not on sale yet" tag.
- Search covers every film, including undated ones, and says plainly when nothing matches.
- Film page links: Letterboxd (with its average rating), IMDb, Rotten Tomatoes, Metacritic, found via
  Wikidata and cached in `state/external.json`. Only confident matches (IMDb id, film not series,
  year ±1 using the Norwegian premiere year when Filmweb has no production year, running time
  within 10 min, a single candidate) get direct links; otherwise a Letterboxd search link.
  Letterboxd is always reached via the IMDb id, never via Wikidata's stored slug.
- Filters: search (Norwegian, English and original title, director, series), time window, my
  cinemas, hide Norwegian dubs, English subtitles only. Nothing is hidden by default.
- **English titles** are always shown when we found a genuine one (not just the untranslated
  original) for a confidently matched film, with the Norwegian title underneath on the film page.
- On the film page, beside the poster under its buttons (under the poster on phones), two menus narrow its showings, each highlighted like an active filter once
  something is picked: **Cinemas** (several can be picked; only when it plays at two or more) and **Format** (IMAX,
  ScreenX, SUPREME, Engelsk tekst, Dubbed, …: whatever its showings carry, except "Norsk tekst", and only those on
  some showings but not all). Picked formats all apply (IMAX + Engelsk tekst = IMAX showings with English
  subtitles); one no remaining showing has is dimmed, and picking it picks it alone instead. Spelling variants
  ("MIRAGE", "Mirage") count as one format. A menu stays open while you pick in it.
- Under them, a strip of days (All days, then each day with showings under what's picked) lists one day or all.
  It's one row that scrolls sideways, fading at an edge with more days beyond it: swiped on phones, with ‹ › arrows
  at both ends on wider screens (only when the days don't all fit; each dimmed at its end). Picking a day keeps the
  strip where it was. Opened while a day is picked on the main page, the film page lists that day (if the film plays
  then), still opening at the top, with that day scrolled into view; otherwise all days.
- **Types** filter: Films · Shorts · Live & stage (opera, ballet, theatre, concerts) · Talks & events
  (lectures, archive evenings, mystery screenings, launches). Classified from explicit signals only
  (title patterns, Filmweb genre/show type, under 45 min = short); non-film items carry a badge.
  Nothing hidden by default; also applies to the email.
- Norwegian-dub and English-subtitle filters are Oslo-only (hidden in Westman, ignored in its email).
- Oslo Coming soon drops films that premiere within 14 days but only have showings elsewhere in
  Norway (e.g. festival titles in Bergen), and films that premiered 3+ days ago with no showings
  anywhere. Filmweb's upcoming list is national, so this is checked per film via its showings.
- No login needed to browse; filters are remembered in the browser.
- **Sign in** with a one-time code (enter email → type the 6-digit code from the email; no
  password; works across devices). Signed-in users get:
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
- **Supabase** (free) stores accounts (email one-time-code auth) and a `profiles` row per user:
  settings, watchlist, subscription, unsubscribe token. Row-level security limits each user to
  their own row; the daily job reads subscribers with the secret key.
- **Gmail** (dedicated account, app password) sends the emails over SMTP.

## Later / out of scope for v1

- More Oslo-area venues (Deichman, Cinema Neuf, Weird and Wonderful Cinema, FilmrullKlubb).
- Derrick Theatre (Virden): Facebook only; best route is getting it listed on CinemaClock.
- Per-user alert when a specific watchlist film gets a new showing (beyond becoming new).
- Push notifications.
