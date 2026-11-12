-- Kinoprogram: one profile row per signed-in user.
-- Paste this whole file into Supabase → SQL Editor → New query → Run. Safe to re-run.

-- Safety check: this must only ever run in a brand-new, empty project. If the database already has
-- other tables (a different app's project), stop before changing anything.
do $$
begin
  if exists (select 1 from information_schema.tables
             where table_schema = 'public' and table_type = 'BASE TABLE' and table_name not in ('profiles', 'friendships', 'plans')) then
    raise exception 'This database already has other tables, so it is not a new Cinecrab project. Nothing was changed.';
  end if;
end $$;

-- Short friend code, e.g. K7QM4P (shown as K7Q-M4P): 6 characters, no look-alikes (0/O, 1/I/L).
create or replace function public.new_friend_code()
returns text language plpgsql set search_path = '' as $$
declare
  alphabet constant text := 'ABCDEFGHJKMNPQRSTUVWXYZ23456789';
  code text;
begin
  loop
    code := '';
    for i in 1..6 loop
      code := code || substr(alphabet, 1 + floor(random() * length(alphabet))::int, 1);
    end loop;
    exit when not exists (select 1 from public.profiles where friend_code = code);
  end loop;
  return code;
end;
$$;

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

-- Friends: a name friends see (defaults to the email) and a code others can enter to add you.
alter table public.profiles add column if not exists display_name text check (char_length(display_name) <= 40);
alter table public.profiles add column if not exists friend_code text;
update public.profiles set friend_code = public.new_friend_code() where friend_code is null;
alter table public.profiles alter column friend_code set default public.new_friend_code();
alter table public.profiles alter column friend_code set not null;
create unique index if not exists profiles_friend_code_key on public.profiles (friend_code);

-- When each watchlist film was added ({film id: "2026-10-27T08:28"}, UTC), for "Friends' new picks" in the daily email.
-- Kept by a trigger, never written by the app; films already on a list before this existed have no time (never "new").
alter table public.profiles add column if not exists watchlist_added jsonb not null default '{}'::jsonb;

create or replace function public.stamp_watchlist_adds()
returns trigger language plpgsql set search_path = '' as $$
begin
  new.watchlist_added := coalesce((
    select jsonb_object_agg(i, v) filter (where v is not null)
    from (select i, coalesce(old.watchlist_added -> i,
                             case when i <> all(old.watchlist)
                                  then to_jsonb(to_char(now() at time zone 'utc', 'YYYY-MM-DD"T"HH24:MI')) end) as v
          from unnest(new.watchlist) as i) x), '{}'::jsonb);
  return new;
end;
$$;

drop trigger if exists on_watchlist_changed on public.profiles;
create trigger on_watchlist_changed
  before update of watchlist on public.profiles
  for each row execute function public.stamp_watchlist_adds();

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
-- The daily job reads subscribers with the secret (service_role) key; new projects don't grant that automatically.
grant select on public.profiles to service_role;
grant update (subscribed, prefs, watchlist, updated_at, display_name) on public.profiles to authenticated;

-- Create the profile row when someone signs up.
create or replace function public.handle_new_user()
returns trigger language plpgsql security definer set search_path = '' as $$
begin
  insert into public.profiles (id, email, display_name)
  values (new.id, new.email, nullif(left(btrim(new.raw_user_meta_data ->> 'display_name'), 40), ''))
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

-- ---------------------------------------------------------------- friends
-- One row per pair of friends, a < b. Made only by redeem_friend_code(); each side can read and remove it.
create table if not exists public.friendships (
  a          uuid not null references public.profiles on delete cascade,
  b          uuid not null references public.profiles on delete cascade,
  created_at timestamptz not null default now(),
  primary key (a, b),
  check (a < b)
);
create index if not exists friendships_b_idx on public.friendships (b);

alter table public.friendships enable row level security;

drop policy if exists "read own friendships" on public.friendships;
create policy "read own friendships" on public.friendships
  for select to authenticated using ((select auth.uid()) in (a, b));

drop policy if exists "remove own friendships" on public.friendships;
create policy "remove own friendships" on public.friendships
  for delete to authenticated using ((select auth.uid()) in (a, b));

revoke all on public.friendships from anon, authenticated;
grant select, delete on public.friendships to authenticated;
-- The daily job lists friends' recent watchlist adds.
grant select on public.friendships to service_role;

-- Enter a friend's code. Returns {status: ok | already | self | unknown, name}; typing is forgiving
-- (case, spaces and dashes are ignored).
create or replace function public.redeem_friend_code(code text)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare
  me uuid := (select auth.uid());
  clean text := upper(regexp_replace(coalesce(code, ''), '[^A-Za-z0-9]', '', 'g'));
  friend uuid;
  friend_name text;
begin
  if me is null then raise exception 'Not signed in'; end if;
  select id, coalesce(nullif(display_name, ''), email) into friend, friend_name
    from public.profiles where friend_code = clean;
  if not found then return jsonb_build_object('status', 'unknown'); end if;
  if friend = me then return jsonb_build_object('status', 'self'); end if;
  insert into public.friendships (a, b) values (least(me, friend), greatest(me, friend)) on conflict do nothing;
  return jsonb_build_object('status', case when found then 'ok' else 'already' end, 'name', friend_name);
end;
$$;

revoke all on function public.redeem_friend_code(text) from public;
grant execute on function public.redeem_friend_code(text) to authenticated;

-- Your friends and their watchlists: only a name, an id and film ids, never anything else on the profile.
create or replace function public.friends_watchlists()
returns table (friend_id uuid, name text, watchlist text[])
language sql stable security definer set search_path = '' as $$
  select p.id, coalesce(nullif(p.display_name, ''), p.email), p.watchlist
  from public.friendships f
  join public.profiles p on p.id = case when f.a = (select auth.uid()) then f.b else f.a end
  where (select auth.uid()) in (f.a, f.b)
  order by f.created_at;
$$;

revoke all on function public.friends_watchlists() from public;
grant execute on function public.friends_watchlists() to authenticated;

-- What your friends recently added to their watchlists, for the Feed: one row per film still on their list that has an add
-- time (UTC, "YYYY-MM-DDTHH:MI", stamped by on_watchlist_changed). Only a name, an id, a film id and a time.
create or replace function public.friends_feed()
returns table (friend_id uuid, name text, film text, added_at text)
language sql stable security definer set search_path = '' as $$
  select p.id, coalesce(nullif(p.display_name, ''), p.email), w.key, w.value #>> '{}'
  from public.friendships f
  join public.profiles p on p.id = case when f.a = (select auth.uid()) then f.b else f.a end
  cross join lateral jsonb_each(p.watchlist_added) w
  where (select auth.uid()) in (f.a, f.b)
    and w.key = any(p.watchlist)
  order by w.value #>> '{}' desc
  limit 400;
$$;

revoke all on function public.friends_feed() from public;
grant execute on function public.friends_feed() to authenticated;

-- ---------------------------------------------------------------- showings: "I'm going"
-- One row per person per showing they're going to. The showing is copied into the row (film, cinema, time, ticket link), so a plan
-- still makes sense after the schedule changes. showing_id is made by the app: "<film id>|<start time>|<cinema>".
create table if not exists public.plans (
  user_id      uuid not null references public.profiles on delete cascade,
  showing_id   text not null check (char_length(showing_id) <= 300),
  film_id      text not null check (char_length(film_id) <= 100),
  film_title   text not null check (char_length(film_title) <= 200),
  cinema       text not null check (char_length(cinema) <= 100),
  starts       text not null check (starts ~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$'),   -- local time at the cinema, "2026-11-07T19:00"
  region       text not null check (char_length(region) <= 30),
  ticket       text check (char_length(ticket) <= 500),
  tell_friends boolean not null default true,     -- friends see it in their Feed
  seats        text check (char_length(seats) <= 30),
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  primary key (user_id, showing_id)
);
create index if not exists plans_starts_idx on public.plans (starts);

alter table public.plans enable row level security;

drop policy if exists "read own plans" on public.plans;
create policy "read own plans" on public.plans for select to authenticated using ((select auth.uid()) = user_id);
drop policy if exists "add own plans" on public.plans;
create policy "add own plans" on public.plans for insert to authenticated with check ((select auth.uid()) = user_id);
drop policy if exists "change own plans" on public.plans;
create policy "change own plans" on public.plans for update to authenticated using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);
drop policy if exists "remove own plans" on public.plans;
create policy "remove own plans" on public.plans for delete to authenticated using ((select auth.uid()) = user_id);

revoke all on public.plans from anon, authenticated;
grant select, insert, update, delete on public.plans to authenticated;

-- ---------------------------------------------------------------- showings: friends, invites, guests
-- Guests are anonymous users (no email), so a profile may have no email.
alter table public.profiles alter column email drop not null;

-- A private invite token per plan, and which invite a plan joined through.
alter table public.plans add column if not exists invite_token uuid not null default gen_random_uuid();
alter table public.plans add column if not exists joined_via uuid;
create unique index if not exists plans_invite_token_key on public.plans (invite_token);
create index if not exists plans_joined_via_idx on public.plans (joined_via) where joined_via is not null;

-- Friends' upcoming plans (only those with "Tell friends" on), for the Feed and a showing's page.
create or replace function public.friends_plans(since text)
returns table (friend_id uuid, name text, showing_id text, film_id text, film_title text, cinema text,
               starts text, region text, ticket text, seats text, created_at timestamptz)
language sql stable security definer set search_path = '' as $$
  select p.user_id, coalesce(nullif(pr.display_name, ''), pr.email, 'Someone'),
         p.showing_id, p.film_id, p.film_title, p.cinema, p.starts, p.region, p.ticket, p.seats, p.created_at
  from public.friendships f
  join public.profiles pr on pr.id = case when f.a = (select auth.uid()) then f.b else f.a end
  join public.plans p on p.user_id = pr.id
  where (select auth.uid()) in (f.a, f.b)
    and p.tell_friends
    and p.starts >= since
  order by p.starts, p.created_at
  limit 400;
$$;
revoke all on function public.friends_plans(text) from public;
grant execute on function public.friends_plans(text) to authenticated;

-- What an invite link shows: the inviter's plan first, then anyone who joined through it. Works without signing in;
-- names only (never an email), seats, and the showing.
create or replace function public.plan_by_invite(token uuid)
returns table (is_host boolean, name text, seats text, film_id text, film_title text, cinema text,
               starts text, region text, ticket text)
language sql stable security definer set search_path = '' as $$
  select (p.invite_token = token), coalesce(nullif(pr.display_name, ''), 'Someone'), p.seats,
         p.film_id, p.film_title, p.cinema, p.starts, p.region, p.ticket
  from public.plans p
  join public.profiles pr on pr.id = p.user_id
  where p.invite_token = token or p.joined_via = token
  order by (p.invite_token = token) desc, p.created_at
  limit 100;
$$;
revoke all on function public.plan_by_invite(uuid) from public;
grant execute on function public.plan_by_invite(uuid) to anon, authenticated;
