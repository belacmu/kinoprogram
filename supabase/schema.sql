-- Kinoprogram: one profile row per signed-in user.
-- Paste this whole file into Supabase → SQL Editor → New query → Run. Safe to re-run.

-- Safety check: this must only ever run in a brand-new, empty project. If the database already has
-- other tables (a different app's project), stop before changing anything.
do $$
begin
  if exists (select 1 from information_schema.tables
             where table_schema = 'public' and table_type = 'BASE TABLE' and table_name not in ('profiles', 'friendships', 'plans', 'films', 'film_aliases', 'showings')) then
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
  if coalesce((auth.jwt() ->> 'is_anonymous')::boolean, false) then raise exception 'Create an account first'; end if;  -- guests (anonymous accounts) can't try friend codes
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
revoke execute on function public.redeem_friend_code(text) from anon;

-- Your friends and their watchlists: only a name, an id and film ids, never anything else on the profile.
create or replace function public.friends_watchlists()
returns table (friend_id uuid, name text, watchlist text[])
language sql stable security definer set search_path = '' as $$
  select p.id, coalesce(nullif(p.display_name, ''), p.email), p.watchlist
  from public.friendships f
  join public.profiles p on p.id = case when f.a = (select auth.uid()) then f.b else f.a end
  where (select auth.uid()) in (f.a, f.b)
    and not coalesce((auth.jwt() ->> 'is_anonymous')::boolean, false)
  order by f.created_at;
$$;

revoke all on function public.friends_watchlists() from public;
grant execute on function public.friends_watchlists() to authenticated;
revoke execute on function public.friends_watchlists() from anon;

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
    and not coalesce((auth.jwt() ->> 'is_anonymous')::boolean, false)
    and w.key = any(p.watchlist)
  order by w.value #>> '{}' desc
  limit 400;
$$;

revoke all on function public.friends_feed() from public;
grant execute on function public.friends_feed() to authenticated;
revoke execute on function public.friends_feed() from anon;

-- ---------------------------------------------------------------- films and showings (stable ids, docs/stable-ids.md)
-- Filled by the programme update (scraper/filmids.py) through sync_films() and sync_showings(), with the secret key. Nothing here is
-- ever deleted: a film or showing that disappears gets gone_at, and a merged film points at the film it was merged into.
-- A film's id is the first listing id it was seen with ("fw-EDI20261336"); every listing id it has had is in film_aliases.
-- A showing's id is "<film id>|<start>|<cinema>" when first seen, and never changes, even if its film is merged later.
create table if not exists public.films (
  id          text primary key,
  title       text not null,
  year        text,
  tmdb        integer,
  imdb        text,
  merged_into text references public.films on delete restrict,
  first_seen  timestamptz not null default now(),
  last_seen   timestamptz not null default now(),
  gone_at     timestamptz,
  check (merged_into is null or merged_into <> id)
);
create index if not exists films_tmdb_idx on public.films (tmdb) where tmdb is not null;
create index if not exists films_merged_into_idx on public.films (merged_into) where merged_into is not null;

create table if not exists public.film_aliases (
  alias      text primary key,
  film_id    text not null references public.films on delete restrict,
  kind       text not null default 'listing' check (kind in ('listing', 'merged', 'history')),
  created_at timestamptz not null default now()
);
create index if not exists film_aliases_film_id_idx on public.film_aliases (film_id);

create table if not exists public.showings (
  id          text primary key,
  film_id     text not null references public.films on delete restrict,
  region      text not null,
  cinema      text not null,
  starts      text not null check (starts ~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$'),   -- local time at the cinema, "2026-11-07T19:00"
  title       text not null,                                                     -- the film's title as that region lists it
  ticket      text check (ticket is null or ticket ~* '^https?://'),
  first_seen  timestamptz not null default now(),
  last_seen   timestamptz not null default now(),
  gone_at     timestamptz,                         -- disappeared before it started (a showing that has simply started is not gone)
  replaced_by text references public.showings on delete restrict,   -- gone, and the same film at the same cinema got a new time that day
  unique (film_id, cinema, starts)
);
create index if not exists showings_region_starts_idx on public.showings (region, starts);

-- The programme is public (it's what the site's JSON has): anyone may read it; only the update (secret key) writes it.
alter table public.films enable row level security;
alter table public.film_aliases enable row level security;
alter table public.showings enable row level security;
drop policy if exists "anyone can read films" on public.films;
create policy "anyone can read films" on public.films for select to anon, authenticated using (true);
drop policy if exists "anyone can read film aliases" on public.film_aliases;
create policy "anyone can read film aliases" on public.film_aliases for select to anon, authenticated using (true);
drop policy if exists "anyone can read showings" on public.showings;
create policy "anyone can read showings" on public.showings for select to anon, authenticated using (true);
revoke all on public.films, public.film_aliases, public.showings from anon, authenticated;
grant select on public.films, public.film_aliases, public.showings to anon, authenticated;
grant select, insert, update on public.films, public.film_aliases, public.showings to service_role;

-- Films and aliases from one programme update. p = {films: [{id, title, year, tmdb, imdb}], merges: [{loser, winner}],
-- aliases: [{alias, film_id, kind}], live: [film id, ...] (every film in any region's current data), complete: whether films not in
-- `live` may be marked gone}. Runs as the caller (only the secret key may call it).
create or replace function public.sync_films(p jsonb)
returns void language plpgsql set search_path = '' as $$
declare
  m record;
  live text[] := array(select jsonb_array_elements_text(coalesce(p -> 'live', '[]'::jsonb)));
begin
  insert into public.films as f (id, title, year, tmdb, imdb)
  select x.id, left(coalesce(nullif(x.title, ''), x.id), 300), nullif(x.year, ''), x.tmdb, x.imdb
  from jsonb_to_recordset(coalesce(p -> 'films', '[]'::jsonb)) as x(id text, title text, year text, tmdb integer, imdb text)
  on conflict (id) do update
    set title = excluded.title, year = coalesce(excluded.year, f.year), tmdb = coalesce(excluded.tmdb, f.tmdb),
        imdb = coalesce(excluded.imdb, f.imdb), last_seen = now(), gone_at = null;

  -- Merges: the loser points at the winner, its aliases and showings move over. A showing both films had stays with the winner;
  -- the loser's copy is gone and points at it.
  for m in select * from jsonb_to_recordset(coalesce(p -> 'merges', '[]'::jsonb)) as x(loser text, winner text) loop
    continue when m.loser = m.winner or not exists (select 1 from public.films where id = m.winner);
    update public.films set merged_into = m.winner where merged_into = m.loser;
    update public.films set merged_into = m.winner, gone_at = null where id = m.loser;
    update public.film_aliases set film_id = m.winner where film_id = m.loser;
    if exists (select 1 from public.films where id = m.loser) then
      insert into public.film_aliases (alias, film_id, kind) values (m.loser, m.winner, 'merged')
      on conflict (alias) do update set film_id = excluded.film_id, kind = 'merged';
    end if;
    update public.showings l set gone_at = coalesce(l.gone_at, now()), replaced_by = w.id
      from public.showings w
      where l.film_id = m.loser and w.film_id = m.winner and w.cinema = l.cinema and w.starts = l.starts;
    update public.showings l set film_id = m.winner
      where l.film_id = m.loser
        and not exists (select 1 from public.showings w where w.film_id = m.winner and w.cinema = l.cinema and w.starts = l.starts);
  end loop;

  insert into public.film_aliases (alias, film_id, kind)
  select x.alias, x.film_id, coalesce(x.kind, 'listing')
  from jsonb_to_recordset(coalesce(p -> 'aliases', '[]'::jsonb)) as x(alias text, film_id text, kind text)
  where exists (select 1 from public.films where id = x.film_id)
  on conflict (alias) do update set film_id = excluded.film_id;

  update public.films set gone_at = null, last_seen = now() where id = any(live) and gone_at is not null;
  if coalesce((p ->> 'complete')::boolean, false) then
    update public.films set gone_at = now() where gone_at is null and merged_into is null and not (id = any(live));
  end if;
end;
$$;
revoke all on function public.sync_films(jsonb) from public;
revoke execute on function public.sync_films(jsonb) from anon, authenticated;
grant execute on function public.sync_films(jsonb) to service_role;

-- One region's showings from one programme update. p = {region, now: local time "YYYY-MM-DDTHH:MI" (showings from then on that are
-- missing are gone; the scraper sends a time a few hours ahead, as sources drop a showing once its sales close), cinemas: [names whose missing
-- upcoming showings may be marked gone], showings: [{id, film_id, cinema, starts, title, ticket}]}.
create or replace function public.sync_showings(p jsonb)
returns void language plpgsql set search_path = '' as $$
declare
  r text := p ->> 'region';
  t text := p ->> 'now';
  ok text[] := array(select jsonb_array_elements_text(coalesce(p -> 'cinemas', '[]'::jsonb)));
  seen text[];
begin
  if r is null or t is null then raise exception 'sync_showings needs region and now'; end if;
  insert into public.showings as s (id, film_id, region, cinema, starts, title, ticket)
  select x.id, x.film_id, r, x.cinema, x.starts, left(coalesce(nullif(x.title, ''), x.film_id), 300),
         case when x.ticket ~* '^https?://' then x.ticket end
  from jsonb_to_recordset(coalesce(p -> 'showings', '[]'::jsonb)) as x(id text, film_id text, cinema text, starts text, title text, ticket text)
  where exists (select 1 from public.films where id = x.film_id)
  on conflict (id) do update
    set film_id = excluded.film_id, region = excluded.region, title = excluded.title, ticket = excluded.ticket,
        last_seen = now(), gone_at = null, replaced_by = null;
  seen := array(select x ->> 'id' from jsonb_array_elements(coalesce(p -> 'showings', '[]'::jsonb)) x);

  -- Upcoming showings at a working cinema that weren't in this update are gone.
  update public.showings set gone_at = now()
    where region = r and starts >= t and gone_at is null and cinema = any(ok) and not (id = any(seen));
  -- One showing gone and one new for the same film, cinema and day: the new one replaces it (the app offers to move plans).
  update public.showings g set replaced_by = n.id
    from public.showings n
    where g.region = r and g.gone_at = now() and g.replaced_by is null
      and n.region = r and n.first_seen = now() and n.film_id = g.film_id and n.cinema = g.cinema and left(n.starts, 10) = left(g.starts, 10)
      and (select count(*) from public.showings g2 where g2.region = r and g2.gone_at = now() and g2.film_id = g.film_id
             and g2.cinema = g.cinema and left(g2.starts, 10) = left(g.starts, 10)) = 1
      and (select count(*) from public.showings n2 where n2.region = r and n2.first_seen = now() and n2.film_id = g.film_id
             and n2.cinema = g.cinema and left(n2.starts, 10) = left(g.starts, 10)) = 1;
end;
$$;
revoke all on function public.sync_showings(jsonb) from public;
revoke execute on function public.sync_showings(jsonb) from anon, authenticated;
grant execute on function public.sync_showings(jsonb) to service_role;

-- ---------------------------------------------------------------- showings: "I'm going"
-- One row per person per showing they're going to. showing_id points at public.showings (a foreign key, added below): the showing's
-- film, cinema, time, region and ticket link come from there. The copy columns (film_id .. ticket) are filled from showings by a
-- trigger, never by the app; they are kept only while older pages still read them, and will be dropped.
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
grant select on public.plans to service_role;  -- the daily email's "Friends are going" reads it with the secret key
grant select, insert, update, delete on public.plans to authenticated;

-- ---------------------------------------------------------------- showings: friends, invites, guests
-- Guests are anonymous users (no email), so a profile may have no email.
alter table public.profiles alter column email drop not null;

-- A private invite token per plan, and which invite a plan joined through.
alter table public.plans add column if not exists invite_token uuid not null default gen_random_uuid();
alter table public.plans add column if not exists joined_via uuid;
create unique index if not exists plans_invite_token_key on public.plans (invite_token);
create index if not exists plans_joined_via_idx on public.plans (joined_via) where joined_via is not null;

-- Friends' upcoming plans (only those with "Tell friends" on), for the Feed and a showing's page. The showing's details come from
-- public.showings; gone: the cinema no longer lists it, replaced_by: the showing that took its place (a new time that day).
drop function if exists public.friends_plans(text);
create function public.friends_plans(since text)
returns table (friend_id uuid, name text, showing_id text, film_id text, film_title text, cinema text,
               starts text, region text, ticket text, seats text, created_at timestamptz, gone boolean, replaced_by text)
language sql stable security definer set search_path = '' as $$
  select p.user_id, coalesce(nullif(pr.display_name, ''), pr.email, 'Someone'),
         p.showing_id, s.film_id, s.title, s.cinema, s.starts, s.region, s.ticket, p.seats, p.created_at,
         s.gone_at is not null, s.replaced_by
  from public.friendships f
  join public.profiles pr on pr.id = case when f.a = (select auth.uid()) then f.b else f.a end
  join public.plans p on p.user_id = pr.id
  join public.showings s on s.id = p.showing_id
  where (select auth.uid()) in (f.a, f.b)
    and p.tell_friends
    and not coalesce((auth.jwt() ->> 'is_anonymous')::boolean, false)
    and s.starts >= since
  order by s.starts, p.created_at
  limit 400;
$$;
revoke all on function public.friends_plans(text) from public;
grant execute on function public.friends_plans(text) to authenticated;
revoke execute on function public.friends_plans(text) from anon;

-- A party: everyone connected through invite links to the same showing. A plan that is not joined through anyone's link starts
-- its own (a new random party); joining through a link takes that plan's party. Both are decided by save_plan() on the server,
-- never by the client. Everyone in a party sees everyone in it.
alter table public.plans add column if not exists party uuid;
update public.plans set party = invite_token where party is null;
alter table public.plans alter column party set default gen_random_uuid();
alter table public.plans alter column party set not null;
drop trigger if exists plans_party on public.plans;
drop function if exists public.plans_set_party();
create index if not exists plans_party_idx on public.plans (party);

-- Content check: a ticket link must be http(s) (a javascript: link would run on this site). (Plans used to also check that showing_id
-- was made from film, time and cinema; a showing's id is now frozen while its film can be merged, so that check is gone.)
update public.plans set ticket = null where ticket is not null and ticket !~* '^https?://';
alter table public.plans drop constraint if exists plans_ticket_http;
alter table public.plans add constraint plans_ticket_http check (ticket is null or ticket ~* '^https?://');
alter table public.plans drop constraint if exists plans_showing_id_matches;

-- The showing an id names: the showing with that id, else (an id made from an older film id, "<film id>|<start>|<cinema>") the showing of
-- that film (through film_aliases), cinema and time. Null if there is none. Used by save_plan only.
create or replace function public.resolve_showing(p_id text)
returns text language sql stable set search_path = '' as $$
  select coalesce(
    (select s.id from public.showings s where s.id = p_id),
    (select s.id from public.showings s
       left join public.film_aliases a on a.alias = split_part(p_id, '|', 1)
       where s.film_id = coalesce(a.film_id, split_part(p_id, '|', 1))
         and s.starts = split_part(p_id, '|', 2)
         and s.cinema = substr(p_id, char_length(split_part(p_id, '|', 1)) + char_length(split_part(p_id, '|', 2)) + 3)
       order by s.gone_at is not null, s.id
       limit 1));
$$;
revoke all on function public.resolve_showing(text) from public;
revoke execute on function public.resolve_showing(text) from anon, authenticated;

-- The only way to write a plan: the client names a showing and its seats, nothing else. The showing's details and ticket link come
-- from public.showings; the invite token, the party and who it joined through are decided here. Joining through an invite counts
-- only for the same showing. A new plan for a showing the cinema no longer lists is refused (changing seats on one you have is fine).
-- Returns {token: the plan's private invite token (made here, so it can't be chosen or reused), showing_id: the showing's id}.
create or replace function public.save_plan(p_showing_id text, p_seats text, p_joined_via uuid)
returns jsonb language plpgsql security definer set search_path = '' as $$
declare
  me uuid := (select auth.uid());
  sid text := public.resolve_showing(p_showing_id);
  via uuid; pty uuid; mine uuid; fparties uuid[]; allp uuid[]; target uuid; tok uuid;
begin
  if me is null then raise exception 'Not signed in'; end if;
  if sid is null then raise exception 'Unknown showing' using errcode = '23503'; end if;
  perform pg_advisory_xact_lock(hashtext(sid));  -- two people going at the same moment merge one after the other
  -- my own plan for this showing, if I already have one
  select q.party into mine from public.plans q where q.user_id = me and q.showing_id = sid;
  if mine is null and exists (select 1 from public.showings where id = sid and gone_at is not null) then
    raise exception 'That showing is no longer listed' using errcode = '22023';
  end if;
  -- the invite this plan joined through (only counts for the same showing)
  select q.invite_token, q.party into via, pty
    from public.plans q where q.invite_token = p_joined_via and q.showing_id = sid;
  -- the parties my friends who are going to this showing are in
  select array_agg(distinct q.party) into fparties
    from public.plans q
    join public.friendships f on (f.a = me and f.b = q.user_id) or (f.b = me and f.a = q.user_id)
    where q.showing_id = sid;
  -- one party: the invite's first, then mine, then my friends'; everyone in the others moves into it
  allp := array_remove(array[pty, mine] || coalesce(fparties, '{}'::uuid[]), null);
  target := coalesce(allp[1], gen_random_uuid());
  insert into public.plans (user_id, showing_id, seats, joined_via, party)   -- the showing's copy columns are filled by plans_copy_showing
  values (me, sid, nullif(p_seats, ''), via, target)
  on conflict (user_id, showing_id) do update
    set seats = excluded.seats, party = excluded.party, updated_at = now()
  returning invite_token into tok;
  if cardinality(allp) > 1 then
    update public.plans set party = target where showing_id = sid and party = any(allp) and party <> target;
  end if;
  return jsonb_build_object('token', tok, 'showing_id', sid);
end;
$$;
revoke all on function public.save_plan(text, text, uuid) from public;
grant execute on function public.save_plan(text, text, uuid) to authenticated;
revoke execute on function public.save_plan(text, text, uuid) from anon;

-- The older form, for pages loaded before this change: the film, title, cinema, time, region and ticket it is given are ignored (they
-- come from public.showings); the showing is found by its id, else by film, time and cinema. Will be dropped.
create or replace function public.save_plan(p_showing_id text, p_film_id text, p_film_title text, p_cinema text, p_starts text,
                                           p_region text, p_ticket text, p_seats text, p_joined_via uuid)
returns uuid language plpgsql security definer set search_path = '' as $$
begin
  return (public.save_plan(coalesce(public.resolve_showing(p_showing_id),
                                    public.resolve_showing(p_film_id || '|' || p_starts || '|' || p_cinema), p_showing_id),
                           p_seats, p_joined_via) ->> 'token')::uuid;
end;
$$;
revoke all on function public.save_plan(text, text, text, text, text, text, text, text, uuid) from public;
grant execute on function public.save_plan(text, text, text, text, text, text, text, text, uuid) to authenticated;
revoke execute on function public.save_plan(text, text, text, text, text, text, text, text, uuid) from anon;
drop policy if exists "add own plans" on public.plans;
drop policy if exists "change own plans" on public.plans;
revoke insert, update on public.plans from authenticated;

-- Guests have no email, so they can't be subscribers either (the daily email needs an address).
alter table public.profiles drop constraint if exists profiles_subscribed_needs_email;
alter table public.profiles add constraint profiles_subscribed_needs_email check (subscribed = false or email is not null) not valid;

-- What an invite link shows: the whole party of the plan whose token it is, that plan first. Works without signing in; names only
-- (never an email), seats, and the showing. is_me: that row is the caller's own; is_friend: it is one of the caller's friends (the
-- page already gets those from friends_plans). Both are false for anonymous callers with no account, and never reveal an id.
drop function if exists public.plan_by_invite(uuid);
create or replace function public.plan_by_invite(token uuid)
returns table (is_host boolean, is_me boolean, is_friend boolean, name text, seats text, film_id text, film_title text, cinema text,
               starts text, region text, ticket text, party uuid, showing_id text, gone boolean, replaced_by text)
language sql stable security definer set search_path = '' as $$
  select (p.invite_token = token),
         (p.user_id = (select auth.uid())),
         exists (select 1 from public.friendships f where (f.a = (select auth.uid()) and f.b = p.user_id) or (f.b = (select auth.uid()) and f.a = p.user_id)),
         coalesce(nullif(pr.display_name, ''), 'Someone'), p.seats,
         s.film_id, s.title, s.cinema, s.starts, s.region, s.ticket, p.party, p.showing_id, s.gone_at is not null, s.replaced_by
  from public.plans p
  join public.profiles pr on pr.id = p.user_id
  join public.showings s on s.id = p.showing_id
  where p.party = (select q.party from public.plans q where q.invite_token = token)
  order by (p.invite_token = token) desc, p.created_at
  limit 100;
$$;
revoke all on function public.plan_by_invite(uuid) from public;
grant execute on function public.plan_by_invite(uuid) to anon, authenticated;

-- ---------------------------------------------------------------- plans point at showings (docs/stable-ids.md, stage 2)
-- Every plan's showing must be in public.showings before the foreign key is added. Plans made before showings existed are matched up
-- here; nothing is lost. Safe to re-run: once every plan has its showing, these statements change nothing.
-- 1. A plan whose showing id is unknown but whose film (or an older id of it), cinema and time are known: point it at that showing.
--    If the same person already has a plan for that showing, the older-id duplicate is removed (it is the same showing).
--    Run twice: step 2 can make showings that a second plan of the same showing then points at.
create or replace function public.plans_match_showings()
returns void language plpgsql set search_path = '' as $$
begin
  -- a plan for a showing the same person already has under its real id: the same showing twice, so the older-id copy goes
  delete from public.plans p
    using public.film_aliases a, public.showings s
    where not exists (select 1 from public.showings x where x.id = p.showing_id)
      and a.alias = p.film_id and s.film_id = a.film_id and s.cinema = p.cinema and s.starts = p.starts
      and exists (select 1 from public.plans q where q.user_id = p.user_id and q.showing_id = s.id);
  -- the others move to the showing's id (one per person and showing; a second one is then a duplicate, removed above next time)
  with m as (
    select distinct on (p.user_id, s.id) p.user_id, p.showing_id as old_id, s.id as new_id
    from public.plans p
    join public.film_aliases a on a.alias = p.film_id
    join public.showings s on s.film_id = a.film_id and s.cinema = p.cinema and s.starts = p.starts
    where not exists (select 1 from public.showings x where x.id = p.showing_id)
    order by p.user_id, s.id, p.updated_at desc
  )
  update public.plans p set showing_id = m.new_id from m where p.user_id = m.user_id and p.showing_id = m.old_id;
  delete from public.plans p
    using public.film_aliases a, public.showings s
    where not exists (select 1 from public.showings x where x.id = p.showing_id)
      and a.alias = p.film_id and s.film_id = a.film_id and s.cinema = p.cinema and s.starts = p.starts
      and exists (select 1 from public.plans q where q.user_id = p.user_id and q.showing_id = s.id);
end;
$$;
revoke all on function public.plans_match_showings() from public;
revoke execute on function public.plans_match_showings() from anon, authenticated;

alter table public.plans drop constraint if exists plans_showing_id_matches;  -- (also dropped above; repeated so this part can run alone)
do $$ begin perform public.plans_match_showings(); end $$;
-- 2. Any other plan (a showing that has been and gone, or that disappeared before showings were kept): its showing is made from the
--    plan's own copy, marked gone if it hadn't started yet. Its film too, if unknown.
insert into public.films (id, title, first_seen, last_seen, gone_at)
select distinct on (p.film_id) p.film_id, p.film_title, min(p.created_at) over (partition by p.film_id), now(), now()
from public.plans p
where not exists (select 1 from public.showings x where x.id = p.showing_id)
  and not exists (select 1 from public.film_aliases a where a.alias = p.film_id)
  and not exists (select 1 from public.films f where f.id = p.film_id)
on conflict (id) do nothing;
insert into public.film_aliases (alias, film_id)   -- every film is an alias of itself
select f.id, f.id from public.films f
where f.merged_into is null and not exists (select 1 from public.film_aliases a where a.alias = f.id)
on conflict (alias) do nothing;
insert into public.showings (id, film_id, region, cinema, starts, title, ticket, first_seen, last_seen, gone_at)
select distinct on (p.showing_id) p.showing_id, coalesce(a.film_id, p.film_id), p.region, p.cinema, p.starts, p.film_title,
       case when p.ticket ~* '^https?://' then p.ticket end, p.created_at, p.created_at,
       case when p.starts >= to_char(now(), 'YYYY-MM-DD"T"HH24:MI') then now() end
from public.plans p
left join public.film_aliases a on a.alias = p.film_id
where not exists (select 1 from public.showings x where x.id = p.showing_id)
order by p.showing_id, p.created_at
on conflict do nothing;
do $$ begin perform public.plans_match_showings(); end $$;

-- 3. The foreign key, and an index for finding everyone going to a showing.
create index if not exists plans_showing_id_idx on public.plans (showing_id);
alter table public.plans drop constraint if exists plans_showing_fk;
alter table public.plans add constraint plans_showing_fk foreign key (showing_id) references public.showings on delete restrict;

-- 4. The copy columns are filled from the showing on every write, whatever the writer sends (so the ticket link is never the client's).
create or replace function public.plans_copy_showing()
returns trigger language plpgsql set search_path = '' as $$
begin
  select left(s.film_id, 100), left(s.title, 200), left(s.cinema, 100), s.starts, left(s.region, 30),
         case when char_length(s.ticket) <= 500 then s.ticket end
    into new.film_id, new.film_title, new.cinema, new.starts, new.region, new.ticket
    from public.showings s where s.id = new.showing_id;
  return new;
end;
$$;
revoke all on function public.plans_copy_showing() from public;
revoke execute on function public.plans_copy_showing() from anon, authenticated;
drop trigger if exists plans_copy_showing on public.plans;
create trigger plans_copy_showing
  before insert or update of showing_id, film_id, film_title, cinema, starts, region, ticket on public.plans
  for each row execute function public.plans_copy_showing();
update public.plans p set ticket = s.ticket   -- bring existing copies in line with their showings (the trigger does the rest)
  from public.showings s where s.id = p.showing_id and p.ticket is distinct from s.ticket;

-- 5. A showing whose time changed (gone, replaced_by the new one): move your plan to the new time, keeping your invite link and party,
--    so the people you were going with who move too stay together. Returns the new showing's id.
create or replace function public.move_plan(p_from text)
returns text language plpgsql security definer set search_path = '' as $$
declare
  me uuid := (select auth.uid());
  dest text;
  hops int := 0;
begin
  if me is null then raise exception 'Not signed in'; end if;
  select replaced_by into dest from public.showings where id = p_from;
  while dest is not null and hops < 10
        and exists (select 1 from public.showings where id = dest and gone_at is not null and replaced_by is not null) loop
    select replaced_by into dest from public.showings where id = dest;
    hops := hops + 1;
  end loop;
  if dest is null or exists (select 1 from public.showings where id = dest and gone_at is not null) then
    raise exception 'That showing has no new time' using errcode = '22023';
  end if;
  if not exists (select 1 from public.plans where user_id = me and showing_id = p_from) then
    raise exception 'You have no plan for that showing' using errcode = '22023';
  end if;
  perform pg_advisory_xact_lock(hashtext(dest));
  if exists (select 1 from public.plans where user_id = me and showing_id = dest) then
    delete from public.plans where user_id = me and showing_id = p_from;
  else
    update public.plans set showing_id = dest, updated_at = now() where user_id = me and showing_id = p_from;
  end if;
  return dest;
end;
$$;
revoke all on function public.move_plan(text) from public;
grant execute on function public.move_plan(text) to authenticated;
revoke execute on function public.move_plan(text) from anon;
