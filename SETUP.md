# Setup: the parts you do yourself

The site already works without these steps: https://belacmu.github.io/kinoprogram/
These steps switch on **sign-in, synced settings/watchlist and the daily email**.
Budget about 20 minutes. Everything is free.

You'll collect five values along the way. Keep them in a note until step 3:

| Value | Looks like | Where it ends up |
| --- | --- | --- |
| Gmail address | `kinobyfilm@gmail.com` | Supabase + GitHub secret `GMAIL_USER` |
| Gmail app password | 16 letters, `abcd efgh ijkl mnop` | Supabase + GitHub secret `GMAIL_APP_PASSWORD` |
| Supabase project URL | `https://abcdefgh.supabase.co` | GitHub variable `SUPABASE_URL` |
| Supabase publishable key | `sb_publishable_…` | GitHub variable `SUPABASE_PUBLISHABLE_KEY` |
| Supabase secret key | `sb_secret_…` | GitHub secret `SUPABASE_SECRET_KEY` |

---

## 1. A Gmail account to send from

A dedicated account keeps your personal address out of it.

1. Create the account at https://accounts.google.com/signup (any name, e.g. "Cinecrab").
2. Turn on 2-Step Verification (Google requires it for app passwords):
   https://myaccount.google.com/signinoptions/twosv
3. Create an app password: https://myaccount.google.com/apppasswords
   - App name: `kinoprogram` → **Create**.
   - Copy the 16-letter password (spaces don't matter). You won't see it again.

## 2. Supabase project

1. Sign up at https://supabase.com/dashboard (signing in with GitHub is fine).
2. **New project**
   - Name: `kinoprogram`
   - Database password: click **Generate a password** (you won't need it again, but save it)
   - Region: **North EU (Stockholm)** or **Central EU (Frankfurt)**
   - Plan: Free → **Create new project**, wait a minute or two.
3. Create the table: left sidebar **SQL Editor** → **New query** → paste the entire contents of
   [`supabase/schema.sql`](supabase/schema.sql) → **Run**. It should say "Success. No rows returned".
4. Site address: **Authentication → URL Configuration** → Site URL:
   `https://belacmu.github.io/kinoprogram/` → Save.
5. Send sign-in emails through your Gmail (required: Supabase's built-in mailer only delivers to
   members of your Supabase team, so your friends wouldn't get their codes):
   **Authentication → Emails → SMTP Settings** → enable **Custom SMTP**
   - Sender email: your Gmail address · Sender name: `Cinecrab`
   - Host: `smtp.gmail.com` · Port: `465`
   - Username: your Gmail address · Password: the app password from step 1
   - Save.
6. Make the emails contain a **code** (required; the site signs you in with a one-time code):
   **Authentication → Emails → Templates**. Do this for both **Magic Link** and **Confirm signup**:
   - Subject: `Your Cinecrab code: {{ .Token }}`
   - Body (replace everything there):

     ```html
     <h2>Your sign-in code</h2>
     <p style="font-size:28px;letter-spacing:4px"><b>{{ .Token }}</b></p>
     <p>Type it on Cinecrab to sign in. It expires in an hour.
     If you didn't ask for it, ignore this email.</p>
     ```
   - Save each template.

7. Collect the keys: **Project Settings** (gear icon) →
   - **Data API**: copy the **Project URL**.
   - **API Keys**: copy the **Publishable key** (`sb_publishable_…`), and under **Secret keys**
     reveal and copy the default secret key (`sb_secret_…`).
   The secret key can read every subscriber's email; it only goes into GitHub secrets, never anywhere else.

## 3. Give the values to GitHub

Run these in Terminal, one at a time. The `secret` commands ask you to paste the value, so it
never shows up on screen or in your shell history. Replace the two placeholder values in the
`variable` commands with yours (those two are public by design).

```bash
gh variable set SUPABASE_URL -R github.com/belacmu/kinoprogram --body "https://YOUR-PROJECT.supabase.co"
```
```bash
gh variable set SUPABASE_PUBLISHABLE_KEY -R github.com/belacmu/kinoprogram --body "sb_publishable_YOUR_KEY"
```
```bash
gh secret set SUPABASE_SECRET_KEY -R github.com/belacmu/kinoprogram
```
```bash
gh secret set GMAIL_USER -R github.com/belacmu/kinoprogram
```
```bash
gh secret set GMAIL_APP_PASSWORD -R github.com/belacmu/kinoprogram
```

(Or in the browser: https://github.com/belacmu/kinoprogram/settings/secrets/actions, using the
**Secrets** and **Variables** tabs.)

## 4. Tell Claude you're done

Claude will then redeploy the site (so sign-in appears) and send a test email. After that:

1. Open https://belacmu.github.io/kinoprogram/ → **Sign in** → enter your own email → type the
   6-digit code from the email you get.
2. Under **Account**, tick **Daily email at 9:00**.
3. Share the site link with friends; they sign up the same way.

---

### If something goes wrong

- **No sign-in email arrives**: check spam; check step 2.5 (SMTP). In Supabase,
  **Authentication → Logs** shows send errors.
- **The email has a link but no code**: step 2.6 (templates) wasn't saved for that template.
- **Run history / errors** for the daily job: https://github.com/belacmu/kinoprogram/actions
- **Supabase project paused**: free projects pause after a week with no activity. The daily job
  reads from it every morning, which should prevent that; if it happens, click **Restore** in the dashboard.
