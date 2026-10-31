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

**Costa del Sol** (Spanish time, twice a day):

| Source | Covers | How |
| --- | --- | --- |
| CarteleraCines.es | Alfil, mk2 Miramar, Kinépolis La Cañada, Red Dog | Read-only JSON-RPC endpoint, up to 14 days per cinema; sessions marked `VOSE` are original language. Lags behind the cinemas for new films. |
| mk2 Miramar's own site | mk2 Miramar, original-language (VOSE) sessions | HTML: the `/vose` page has one block per day (a few weeks of dates) with every film, time and ticket link. Replaces CarteleraCines' VOSE sessions for Miramar (its dubbed ones stay); if the page fails or changes, CarteleraCines' are used. Films merge by title. |

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
  ticket link. Deep link: `#film/<id>`. A Share button (the system share sheet on touch devices, "Copy link" on a computer) gives `f/<region>/<id>/` instead: a small generated page (scripts/write-film-pages.py, run by both deploy workflows) with that film's title, text and poster for link previews, which then opens the film; chat apps never see the `#` part.
- Region switcher (Oslo / Westman / Winnipeg / Costa del Sol), remembered per browser; `?r=westman` links to a region.
- **One grid with section headers, by when films play**: Playing from today (or tomorrow, or the picked day) · From later this week · From next week ·
  Later in <month> · <month>… A film "starts" at its first showing, or its confirmed premiere if
  earlier. Films without a date are left out (they appear only when you search for them).
  - The first section is a chosen day, Today by default (so nothing asks you to pick); once nothing
    more is on today under your filters, it's the next day something is (usually tomorrow), still
    counting as the default.
  - A day control in the bar: a calendar icon with a label (just the icon only on very narrow
    bars): "Date ▾" on Today, which counts as no filter, or the picked day
    ("Thu 15 Oct ▾"), highlighted like active filters. It opens a month picker with a Today button
    beside the month arrows, as in most calendars (days with nothing playing under the current
    filters are greyed out).
  - Under the cinemas, a card says when its showings are and how many, not their times (those are on the
    film page): over several days, the range ("Fri 30 Oct – 6 Nov") with "6 shows" on the next line; several
    showings on one day, "Today · 3 shows"; a single showing, just its day and time ("Tomorrow 19:00").
    It covers the showings that pass your filters, from the shown day on.
  - Picking a day shows the page as it will be then: that day's films first, with that day's
    cinemas on the cards, then everything after it in the usual sections; films only
    playing before it drop out, and a film opened from there starts at (and highlights) that day.
    The section stays even when empty, saying nothing's on. The day isn't remembered: reloading
    goes back to Today; switching region keeps it. Searching ignores it.
- **Feed** (reached from the Feed tab, `#feed`; replaces "What's new"): what happened lately, as posts, newest first (no day
  headings; each post says how long ago). A post is one kind of event with
  its films: "9 new films on sale", "10 new films announced", "More showings added for Dune",
  "Santi added 4 films to their watchlist" (one post per kind, per friend, per day). Posts with more
  than 5 (desktop) / 6 (phone) films show that many and **Show N more** expands them. Cards are the
  usual ones (hearts, hiding, opening a film on top; closing it returns to the feed). Events are the
  last 30 days, after tracking began, and pass the same filters as the list (types, cinemas, language;
  not Tickets or the Watchlist switch); hidden films are left out. A divider, "New since your last
  visit", separates posts newer than the last time the feed was left (kept on the device); the Feed
  tab shows how many posts are new while you're on Films. Signed out, a card invites signing in to see
  friends' picks. Events come from one log (below), so every kind of event appears in the same system.
- **Under a poster in the feed**: just the title and one short line about what the post is about (the
  heading already says what happened): on sale → the cinemas on one line (cut off with … if long), then the usual date range; announced → the first date, premiere or
  "Expected …"; more showings → "+2 showings · Saga"; a friend's pick → nothing more.
- **Navigation**: two destinations, **Feed** and **Films** (`#feed`, `#films`; no hash = Films for now; `#new`
  = Feed; `#film/…` links open on Films). A floating, translucent capsule at the bottom on phones and tablets (under 900px), links
  beside the title in the header on desktop; both mark the current page with a solid red pill and white text. Tapping the current one scrolls to the top. Filters,
  search and the list switch belong to Films only.
- Section headers stick below the top bar; clicking one collapses it to a single line (count + first
  titles). The jump bar links to each section (opening it if collapsed) and has Collapse all /
  Expand all.
- Header: the same height and spacing at every width (a fixed-size title); the title (and on desktop the Feed / Films links), then the location and the account button,
  always on one row: when it's tight they compact only as far as needed (the location's pin, "Sign in"
  to an icon, the location's arrow, a smaller title), else the buttons wrap under the title.
- Top bar (the filters): **All films | ♥ Watchlist | Friends** (three tabs; the film count shows
  only on the selected tab; icons show only when the switch is wide enough, else text only), then a
  search field (always a field, phones too; Escape clears it), the day control, and on narrow
  screens a Filters button. From 900px wide the 370px switch and the bar share one row; below that
  the switch has its own full-width row (phones and tablets alike). The day control keeps its label
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
- **Types**: Films · Shorts · Live & stage (opera, ballet, theatre, concerts) · Talks & events
  (lectures, archive evenings, mystery screenings, launches). Classified from explicit signals only
  (title patterns, Filmweb genre/show type, under 45 min = short); non-film items carry a badge.
  Only films are listed by default; shorts, live & stage and talks are turned on under **Preferences**
  (account menu when signed in, the page footer for everyone, or the line under the list that says how many
  are turned off). Preferences are kept on the device and in the account, and also apply to the email.
  Searching always finds every type.
- **Filters** panel: Sort films by (one choice), Tickets (one choice), Spoken language (one choice, where the region has
  dubbing: any / original language only, plus English or Norwegian audio in Costa del Sol) and Subtitles (Oslo:
  any / English subtitles). The two are separate and can be combined and Cinemas (several; busiest first, with "Show more" once there are many; picked ones always show).
  The filters in force show as one line of removable chips under the toolbar, with "Clear all". While any are on,
  the Filters button moves from the search bar to the start of that line (one row with its chips); with none it is
  back in the bar. Several cinemas make one chip ("5 cinemas"). The Filters button scrolls with the chips (sideways on phones and tablets; they wrap on
  desktop), while "Clear all" stays pinned at the end of the line, where the panel is always
  open and there is no button. Picking a cinema also sets
  Tickets to "On sale" (the cinema counts are showings on sale), and clearing the cinemas resets it.
- Norwegian-dub and English-subtitle filters are Oslo-only (hidden in Westman, ignored in its email).
- Oslo Coming soon drops films that premiere within 14 days but only have showings elsewhere in
  Norway (e.g. festival titles in Bergen), and films that premiered 3+ days ago with no showings
  anywhere. Filmweb's upcoming list is national, so this is checked per film via its showings.
- No login needed to browse; filters are remembered in the browser.
- **Sign in** with a one-time code (enter email → type the 6-digit code from the email; no
  password; works across devices). Signed-in users get:
  settings and watchlist synced across devices, the daily email, and friends.
- **Watchlist without an account**: anyone can tap hearts; the list is kept on the device. The
  Watchlist tab shows a box while signed out ("Keep your watchlist safe" + **Sign in or register**).
  On first sign-in the device's list is added to the account (existing accounts get it merged in).

## Friends

Signed-in users can add each other as friends to see which films their friends want to see.
Being friends means you each see the other's watchlist; that's the whole point, so there's no
privacy setting.

- **Display name**: an optional name field on the sign-in form, used only if the email is new; existing users default to their email until they set
  one. Changeable on the Friends screen. Only friends see it.
- **Friend code**: everyone has a short code, e.g. `K7Q-M4P` (6 characters, no look-alikes such as
  0/O, 1/I/L). Entry is forgiving: lowercase, spaces or a missing dash all work.
- **Friends tab** (also the account menu's Friends item): lists every film on a friend's watchlist
  (watchlist films first, then most friends first), straight under the controls. With no friends yet,
  an "Add your first friend" card sits where the films would be: your code beside **Copy invite link**
  and a box to enter a friend's code with **Add friend** inside its right end (disabled until a code is
  typed). An **Add a friend** round button next to the switch (always there on this tab) opens a dialog (a bottom
  sheet on phones) with the same two controls plus "Your friends" with **Remove** (confirmed).
  **Filters** has a Friends group on this tab (pick one or more friends, counts per friend; none = all
  friends). Signed out: a Sign in prompt. Display name stays in the account menu.
- **Sharing** sends a link with the code in it, plus the code written out ("Add me on Cinecrab:
  <link>, or code K7Q-M4P"), so it can be typed in without opening the link.
- **Opening the link**: signed in → the friend is added at once ("You and Anna are now friends").
  Signed out → the page offers both **Sign in** (then adds the friend) and the code with **Copy**
  and "Or open Cinecrab from your Home Screen and enter this code under Friends". This covers
  iPhone home-screen web apps: links always open in Safari, which keeps a separate login, but the
  friendship lives in the database, so the app sees it on its next load.
- Friendships are mutual and made by redeeming a code; no request/accept step. Removing deletes it
  for both, silently; reconnecting needs a code again. Adding yourself or an existing friend does
  nothing.
- **On posters**: small stacked initials of friends with the film on their watchlist (up to 3,
  then "+N"), separate from your own ♥.
- **Film sheet**: "On Anna's and Jon's watchlists".
- Friends in other regions show up only on films with the same id (ids are per source), which is
  fine.

Data (Supabase):
- `profiles` gains `display_name` and `friend_code` (unique, generated at sign-up and backfilled).
- `friendships (a, b, created_at)`: one row per pair, `a < b`. Users can read and delete rows they're
  in; no direct inserts.
- `redeem_friend_code(code)` (security definer) creates the friendship and returns the friend's name.
- `friends_watchlists()` (security definer) returns each friend's id, display name and watchlist,
  never their email-only fields such as prefs or the unsubscribe token (the email is only returned
  as the name fallback).

## The event log (the feed's source)

- **Catalog events**: `scraper/build.py` writes them each run into `state/*.json` (`events`) and
  `site/data/*.json`, newest first, kept 60 days: `sale` (went on sale), `announced` (got a date) and
  `showings` (a film with few showings got more, with `n` and `cinemas`). Each has a stable id
  (`type|film|time`), so a run only adds what's new and the log fills itself in from the existing
  stamps (`onSaleSince`, `announcedSince`, `shows`).
- **Friend events**: films friends added to their watchlists, from `profiles.watchlist_added` through
  the `friends_feed()` function (a name, an id, a film id and a UTC time; friends only). Needs the
  database update in `supabase/schema.sql`; without it the feed just shows no friend posts.
- The daily email still uses its own rules; moving it onto this log is a later step.

## Daily email

- Sent at about 09:00 local time per region, to subscribers who chose that region (default Oslo),
  only when their personalised list is non-empty.
- **New on sale**: films that became new since the previous email, filtered by the subscriber's
  settings (my cinemas, hide dubs, English subtitles only). Watchlist films appear first, and can
  be set to always be included regardless of filters.
- **Newly announced** (can be turned off): films newly announced since the previous email, with
  their premiere date. Filters apply only where showings are known.
- **On your watchlist** (top): watchlist films newly on sale; a watchlist film with fewer than 5
  showings that got another one (`state` `shows`, kept by build.py for films with few showings;
  this alone sends an email); and "Last chance", by a stricter rule than the site's (at most 2
  showings left, the last within 5 days, and the cinema listing a week beyond it), mentioned once.
  Newly announced watchlist films aren't repeated here; they have a filled heart in Newly announced.
- **Friends' new picks** (bottom): films friends added to their watchlists since the previous email
  (`profiles.watchlist_added`, stamped by a database trigger). Never sends an email on its own.
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
- Push notifications.
- Friends: rate
  limiting wrong code attempts; "Get a new code".
