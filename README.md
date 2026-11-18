# Cinecrab

Every film showing in Oslo (Filmweb, Cinemateket, Revier Film Club, Deichman Bjørvika, Sommerro) and Westman, Manitoba (MovieScout, CinemaClock,
Evans Theatre), browsed **by film** instead of by date, with a daily email of films newly on sale and
newly announced. See [SPEC.md](SPEC.md).

- Site: https://belacmu.github.io/kinoprogram/
- First-time setup of sign-in and email: [SETUP.md](SETUP.md)
- Runs on GitHub Actions ([update.yml](.github/workflows/update.yml)) six times a day; the email goes out after 09:00 Oslo.

## Layout

| Path | What |
| --- | --- |
| `scraper/sources.py` | Fetches Filmweb (GraphQL) and Cinemateket (HTML) |
| `scraper/revier.py` | Fetches Revier Film Club (Eventbrite organizer page) |
| `scraper/deichman.py` | Fetches the library's film screenings (Hoopla API) |
| `scraper/sommerro.py` | Fetches Sommerro's dinner + film evenings (HTML) |
| `scraper/build.py` | Merges by film, tracks what's new and logs feed events, writes `site/data/films.json` and `state/seen.json` |
| `scraper/digest.py` | Sends each subscriber their personalised email via Gmail |
| `site/` | The static site (no build step) |
| `supabase/schema.sql` | Accounts table + security rules |
| `state/seen.json` | What has been on sale / announced and when (committed by the workflow) |

## Run locally

```bash
python3 scraper/build.py                 # fetch + write site/data/films.json
python3 -m http.server -d site 8000      # then open http://localhost:8000
python3 scraper/digest.py --dry-run      # show what subscribers would get; sends nothing
```

Python 3.9+ standard library only.

## Configuration

Repository **variables** (Settings → Secrets and variables → Actions → Variables):
`SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY` (both public by design).

Repository **secrets**: `SUPABASE_SECRET_KEY`, `GMAIL_USER`, `GMAIL_APP_PASSWORD`.

Without them the site still works (browsing, filters, a watchlist kept in the browser); sign-in and
emails switch on once they're set.

## Tests

`sh scripts/test.sh` runs them all: the scraper's film ids and cards (`scraper/test_filmids.py`), a syntax check of
`site/app.js`, and the database schema (`supabase/tests/`: `schema.sql` applied twice to a scratch Postgres, then film cards,
`resolve_films`, pruning and past plans checked). The database part needs a local Postgres, or `DATABASE_URL` pointing at a scratch
server; never the real Supabase project. GitHub runs them on every push that touches the scraper, the schema or the app
(`.github/workflows/tests.yml`).
