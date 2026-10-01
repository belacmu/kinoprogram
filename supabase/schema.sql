-- Kinoprogram: one profile row per signed-in user.
-- Paste this whole file into Supabase → SQL Editor → New query → Run. Safe to re-run.

create table if not exists public.profiles (
  id                uuid primary key references auth.users on delete cascade,
  email             text not null,
  subscribed        boolean not null default false,
  prefs             jsonb not null default '{}'::jsonb,   -- {cinemas:[], hideDubbed, englishSubs, watchlistAlways}
  watchlist         text[] not null default '{}',          -- film ids, e.g. "fw-EDI20261336", "cm-agnus-dei"
  unsubscribe_token uuid not null default gen_random_uuid() unique,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);

alter table public.profiles enable row level security;

-- Each user can read and change only their own row.
drop policy if exists "read own profile" on public.profiles;
create policy "read own profile" on public.profiles
  for select to authenticated using ((select auth.uid()) = id);

drop policy if exists "update own profile" on public.profiles;
create policy "update own profile" on public.profiles
  for update to authenticated using ((select auth.uid()) = id) with check ((select auth.uid()) = id);

-- Users may only change these columns (not email or the unsubscribe token).
revoke insert, update, delete on public.profiles from anon, authenticated;
grant select on public.profiles to authenticated;
grant update (subscribed, prefs, watchlist, updated_at) on public.profiles to authenticated;

-- Create the profile row when someone signs up.
create or replace function public.handle_new_user()
returns trigger language plpgsql security definer set search_path = '' as $$
begin
  insert into public.profiles (id, email) values (new.id, new.email)
  on conflict (id) do nothing;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- Keep the stored email in step if a user changes it.
create or replace function public.handle_user_email_change()
returns trigger language plpgsql security definer set search_path = '' as $$
begin
  update public.profiles set email = new.email where id = new.id;
  return new;
end;
$$;

drop trigger if exists on_auth_user_email_changed on auth.users;
create trigger on_auth_user_email_changed
  after update of email on auth.users
  for each row execute function public.handle_user_email_change();

-- One-click unsubscribe from the email link, no login needed.
create or replace function public.unsubscribe(token uuid)
returns boolean language plpgsql security definer set search_path = '' as $$
begin
  update public.profiles set subscribed = false, updated_at = now() where unsubscribe_token = token;
  return found;
end;
$$;

revoke all on function public.unsubscribe(uuid) from public;
grant execute on function public.unsubscribe(uuid) to anon, authenticated;
