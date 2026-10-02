# Accounts and keys

What Cinecrab depends on, where each secret lives, and how to replace it. **This file is public (the
repo is public): it lists accounts and where secrets are stored, never passwords or keys.** Keep the
actual passwords in your password manager. Check what's set up with `sh scripts/check-setup.sh`.

| Service | Used for | Account (fill in) | Secret / variable | Where the value lives |
| --- | --- | --- | --- | --- |
| GitHub | Hosting, the daily job | `belacmu` | none (the `gh` login on your Mac) | GitHub |
| Gmail (sender) | Daily email + sign-in codes | `cinecrabapp@gmail.com` | `GMAIL_USER`, `GMAIL_APP_PASSWORD` | Password: your password manager. App password: GitHub secret only |
| Supabase | Accounts, watchlists, subscriptions | _not created yet_ (project URL: `https://________.supabase.co`) | `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY` (variables), `SUPABASE_SECRET_KEY` (secret) | Login: password manager (or GitHub sign-in). Keys: GitHub |
| TMDB | Film ids, English titles | `belacmu`-linked TMDB account | `TMDB_READ_TOKEN` | GitHub secret; regenerate at themoviedb.org → Settings → API |
| MovieScout | Westman and Winnipeg showtimes | none (permission by email: small personal project, once a day, don't overload; extended to Winnipeg in October 2026 on the condition we poll sparingly) | none | Their reply emails: keep them. Daily request counts are in `state/moviescout.json` (`log`) |

## Rules
- Never commit passwords, API keys, tokens or `.conf` files. Secrets go in GitHub Actions secrets
  (`gh secret set NAME -R github.com/belacmu/kinoprogram`, which asks you to paste the value).
- GitHub secrets can't be read back, only replaced. So the sender's password and the Supabase login
  must also live in your password manager.
- If a key leaks (for example pasted into a chat), regenerate it at the service and re-run the
  `gh secret set` command.

## Rotating things
- **Gmail app password:** myaccount.google.com/apppasswords → create a new one → `gh secret set GMAIL_APP_PASSWORD …`. Supabase's SMTP settings also hold it (Authentication → Emails → SMTP Settings): update both.
- **Supabase keys:** Project Settings → API Keys → regenerate → update `SUPABASE_PUBLISHABLE_KEY` / `SUPABASE_SECRET_KEY`.
- **TMDB token:** regenerate in TMDB settings → `gh secret set TMDB_READ_TOKEN …`.
