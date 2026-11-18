-- Kinoprogram: one profile row per signed-in user.
-- Paste this whole file into Supabase → SQL Editor → New query → Run. Safe to re-run.

-- Safety check: this must only ever run in a brand-new, empty project. If the database already has
-- other tables (a different app's project), stop before changing anything.
do $$
begin
  if exists (select 1 from information_schema.tables
             where table_schema = 'public' and table_type = 'BASE TABLE' and table_name not in ('profiles', 'friendships', 'plans', 'films', 'film_aliases', 'showings', 'watch_options', 'film_marks')) then
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

-- On every change to a watchlist: each id becomes its film's stable id (through film_aliases: an older listing id, or a film that was
-- merged into another), in order, without repeats; an id no film is known by stays as it is, so nothing is ever dropped. Then each film
-- keeps the add time it had under any of its ids (the earliest), and a film that wasn't on the list before gets the time now.
create or replace function public.stamp_watchlist_adds()
returns trigger language plpgsql set search_path = '' as $$
declare
  ids text[];
  was text[];
begin
  select coalesce(array_agg(c order by pos), '{}') into ids
    from (select coalesce(a.film_id, w.i) as c, min(w.n) as pos
          from unnest(new.watchlist) with ordinality as w(i, n)
          left join public.film_aliases a on a.alias = w.i
          group by 1) x;
  select coalesce(array_agg(distinct coalesce(a.film_id, w.i)), '{}') into was
    from unnest(old.watchlist) as w(i) left join public.film_aliases a on a.alias = w.i;
  new.watchlist := ids;
  new.watchlist_added := coalesce((
    select jsonb_object_agg(c, v) filter (where v is not null)
    from (select c, coalesce(
                      (select to_jsonb(min(e.value #>> '{}'))
                         from jsonb_each(old.watchlist_added || new.watchlist_added) e
                         left join public.film_aliases a on a.alias = e.key
                         where coalesce(a.film_id, e.key) = c),
                      case when c <> all(was) then to_jsonb(to_char(now() at time zone 'utc', 'YYYY-MM-DD"T"HH24:MI')) end) as v
          from unnest(ids) as c) x), '{}'::jsonb);
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
grant update (watchlist) on public.profiles to service_role;   -- sync_films renames a merged film's id in watchlists (the trigger does it)
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
-- Filled by the programme update (scraper/filmids.py) through sync_films() and sync_showings(), with the secret key. Films are never
-- deleted: a film that disappears gets gone_at, and a merged film points at the film it was merged into. A showing that disappears
-- before it starts gets gone_at; once it has played, prune_showings() deletes it unless someone had a plan for it (a past plan keeps
-- its showing: the film, cinema and time it was for).
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
-- The film's card, built up run by run, so a film that has left every cinema can still be shown (scraper/filmids.py card()):
-- {en, poster, director, runtime, kind, genres, countries, blurb, lb, last: {region: "YYYY-MM-DD" (its last showing there)}}.
alter table public.films add column if not exists meta jsonb not null default '{}'::jsonb;

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

-- Films and aliases from one programme update. p = {films: [{id, title, year, tmdb, imdb, meta}], merges: [{loser, winner}],
-- aliases: [{alias, film_id, kind}], live: [film id, ...] (every film in any region's current data), complete: whether films not in
-- `live` may be marked gone}. Runs as the caller (only the secret key may call it).
create or replace function public.sync_films(p jsonb)
returns void language plpgsql set search_path = '' as $$
declare
  m record;
  live text[] := array(select jsonb_array_elements_text(coalesce(p -> 'live', '[]'::jsonb)));
begin
  -- A film sent without a card keeps the one it has (the scraper sends the whole card, already merged with what was here).
  insert into public.films as f (id, title, year, tmdb, imdb, meta)
  select x.id, left(coalesce(nullif(x.title, ''), x.id), 300), nullif(x.year, ''), x.tmdb, x.imdb, coalesce(x.meta, '{}'::jsonb)
  from jsonb_to_recordset(coalesce(p -> 'films', '[]'::jsonb)) as x(id text, title text, year text, tmdb integer, imdb text, meta jsonb)
  on conflict (id) do update
    set title = excluded.title, year = coalesce(excluded.year, f.year), tmdb = coalesce(excluded.tmdb, f.tmdb),
        imdb = coalesce(excluded.imdb, f.imdb), meta = case when excluded.meta = '{}'::jsonb then f.meta else excluded.meta end,
        last_seen = now(), gone_at = null;

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

  -- Watchlists with a merged film's old id: saving the list again lets stamp_watchlist_adds rename it, keeping its add time.
  update public.profiles set watchlist = watchlist
  where watchlist && array(select x.loser from jsonb_to_recordset(coalesce(p -> 'merges', '[]'::jsonb)) as x(loser text, winner text));

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
-- film, cinema, time, region and ticket link come from there, never from the app.
create table if not exists public.plans (
  user_id      uuid not null references public.profiles on delete cascade,
  showing_id   text not null,
  tell_friends boolean not null default true,     -- friends see it in their Feed
  seats        text check (char_length(seats) <= 30),
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  primary key (user_id, showing_id)
);

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

-- Plans used to keep a copy of their showing (film, title, cinema, time, region, ticket link, written by the app); the showing is now
-- the one place those live (public.showings, through the foreign key below).
drop trigger if exists plans_copy_showing on public.plans;
drop function if exists public.plans_copy_showing();
drop function if exists public.plans_match_showings();
drop index if exists public.plans_starts_idx;
alter table public.plans drop constraint if exists plans_showing_id_matches;
alter table public.plans drop column if exists film_id, drop column if exists film_title, drop column if exists cinema,
  drop column if exists starts, drop column if exists region, drop column if exists ticket;

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
  insert into public.plans (user_id, showing_id, seats, joined_via, party)
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

drop function if exists public.save_plan(text, text, text, text, text, text, text, text, uuid);   -- the older form, which took a copy of the showing
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
create index if not exists plans_showing_id_idx on public.plans (showing_id);
alter table public.plans drop constraint if exists plans_showing_fk;
alter table public.plans add constraint plans_showing_fk foreign key (showing_id) references public.showings on delete restrict;

-- For the daily email's "Friends are going" (secret key only): the plans with "Tell friends" on for showings from `since` on, with
-- their showing.
create or replace function public.plans_going_since(since text)
returns table (user_id uuid, showing_id text, film_id text, cinema text, starts text, region text, created_at timestamptz)
language sql stable set search_path = '' as $$
  select p.user_id, p.showing_id, s.film_id, s.cinema, s.starts, s.region, p.created_at
  from public.plans p join public.showings s on s.id = p.showing_id
  where p.tell_friends and s.starts >= since
  order by s.starts;
$$;
revoke all on function public.plans_going_since(text) from public;
revoke execute on function public.plans_going_since(text) from anon, authenticated;
grant execute on function public.plans_going_since(text) to service_role;

-- A showing whose time changed (gone, replaced_by the new one): move your plan to the new time, keeping your invite link and party,
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

-- ---------------------------------------------------------------- watchlists hold stable film ids (docs/stable-ids.md, stage 3)
-- Watchlists saved before this hold listing ids ("cm-agnus-dei"). Saving each list again lets stamp_watchlist_adds rename every id to
-- its film's stable id and keep its add time; ids no film is known by stay. Only lists with something to change are touched.
update public.profiles p set watchlist = watchlist
where exists (select 1 from unnest(p.watchlist) w join public.film_aliases a on a.alias = w where a.film_id <> w)
   or cardinality(p.watchlist) <> (select count(distinct w) from unnest(p.watchlist) w);

-- ---------------------------------------------------------------- finished showings aren't kept (unless planned)
-- Showings that started before `before` ("YYYY-MM-DD", local date; the scraper sends two days ago) are deleted by each programme
-- update, except those someone had a plan for: plans are never deleted here, so a past plan still says which film, cinema and time it
-- was for. Films stay, with the last day they played in each region on their card (films.meta.last).
-- Returns how many showings were deleted. Secret key only.
create or replace function public.prune_showings(before text)
returns integer language plpgsql security definer set search_path = '' as $$
declare
  n integer;
begin
  if before !~ '^\d{4}-\d{2}-\d{2}$' or before > to_char(now() at time zone 'utc' - interval '1 day', 'YYYY-MM-DD') then
    raise exception 'prune_showings needs a day at least one day ago, "YYYY-MM-DD"' using errcode = '22023';
  end if;
  update public.showings set replaced_by = null
    where replaced_by in (select s.id from public.showings s
                          where s.starts < before and not exists (select 1 from public.plans p where p.showing_id = s.id));
  delete from public.showings s
    where s.starts < before and not exists (select 1 from public.plans p where p.showing_id = s.id);
  get diagnostics n = row_count;
  return n;
end;
$$;
revoke all on function public.prune_showings(text) from public;
revoke execute on function public.prune_showings(text) from anon, authenticated;
grant execute on function public.prune_showings(text) to service_role;

-- ---------------------------------------------------------------- any film by any id it has had
-- For the app: each id asked for (a watchlist's, an old link's; at most 500), the film it is now (through film_aliases and merges),
-- with its card and the regions where it has upcoming showings. An id no film is known by isn't returned. Public, like the tables.
create or replace function public.resolve_films(ids text[])
returns table (asked text, id text, title text, year text, tmdb integer, imdb text, meta jsonb, gone_at timestamptz, regions text[])
language sql stable set search_path = '' as $$
  with recursive walk as (
    select w.i as asked, coalesce(a.film_id, w.i) as fid, 0 as hops
    from unnest(ids[1:500]) as w(i) left join public.film_aliases a on a.alias = w.i
    union all
    select w.asked, f.merged_into, w.hops + 1
    from walk w join public.films f on f.id = w.fid
    where f.merged_into is not null and w.hops < 10
  ), ends as (
    select distinct on (asked) asked, fid from walk order by asked, hops desc
  )
  select l.asked, f.id, f.title, f.year, f.tmdb, f.imdb, f.meta, f.gone_at,
         array(select distinct s.region from public.showings s
               where s.film_id = f.id and s.gone_at is null
                 and s.starts >= to_char(now() at time zone 'utc' - interval '12 hours', 'YYYY-MM-DD"T"HH24:MI')  -- local times: a margin
               order by 1)
  from ends l join public.films f on f.id = l.fid;
$$;
revoke all on function public.resolve_films(text[]) from public;
grant execute on function public.resolve_films(text[]) to anon, authenticated;

-- ---------------------------------------------------------------- films someone has wanted to see (private)
-- A film is marked the first time anyone puts it on a watchlist: signed in (the trigger below) or not (the app calls
-- mark_watchlisted when a heart is tapped, and once for the hearts a browser already has). The mark stays when the film leaves the
-- list. Only the programme update reads it (to decide which films to look up where to watch); nobody can see who marked what, or
-- even which films are marked.
create table if not exists public.film_marks (
  film_id  text primary key references public.films on delete cascade,
  first_at timestamptz not null default now()
);
alter table public.film_marks enable row level security;
revoke all on public.film_marks from anon, authenticated;
grant select on public.film_marks to service_role;

-- Mark films by any id they have had (an old listing id, a merged film): the film they are now. Unknown ids are ignored.
create or replace function public.mark_films(ids text[])
returns void language sql security definer set search_path = '' as $$
  insert into public.film_marks (film_id)
  select distinct coalesce(f.merged_into, f.id)
  from unnest(ids) as w(i)
  left join public.film_aliases a on a.alias = w.i
  join public.films f on f.id = coalesce(a.film_id, w.i)
  on conflict (film_id) do nothing;
$$;
revoke all on function public.mark_films(text[]) from public;
revoke execute on function public.mark_films(text[]) from anon, authenticated;
-- What the app calls (anyone, signed in or not): at most 200 ids a call.
create or replace function public.mark_watchlisted(ids text[])
returns void language sql security definer set search_path = '' as $$
  select public.mark_films(ids[1:200]);
$$;
revoke all on function public.mark_watchlisted(text[]) from public;
grant execute on function public.mark_watchlisted(text[]) to anon, authenticated, service_role;

create or replace function public.mark_watchlist_films()
returns trigger language plpgsql security definer set search_path = '' as $$
begin
  perform public.mark_films(new.watchlist);
  return null;
end;
$$;
drop trigger if exists on_watchlist_marked on public.profiles;
create trigger on_watchlist_marked
  after insert or update of watchlist on public.profiles
  for each row execute function public.mark_watchlist_films();
-- Every film already on a watchlist (safe to re-run).
select public.mark_films(array(select distinct w from public.profiles p, unnest(p.watchlist) w));

-- ---------------------------------------------------------------- where to watch (streaming, renting, buying)
-- For marked films (above) with no showings anywhere, the programme update asks TMDB where each can be watched
-- (TMDB's data comes from JustWatch, which must be credited where it is shown) and keeps one row per country the site covers, also
-- when there's nothing (so it isn't asked again). Checked when first needed, then again after a couple of weeks while still needed.
-- options = {stream|free|ads|rent|buy: [{name, logo}]} (TMDB's order); link = TMDB's watch page for that film and country.
create table if not exists public.watch_options (
  film_id    text not null references public.films on delete cascade,
  country    text not null check (country ~ '^[A-Z]{2}$'),
  options    jsonb not null default '{}'::jsonb,
  link       text check (link is null or link ~* '^https://'),
  checked_at timestamptz not null default now(),
  primary key (film_id, country)
);
alter table public.watch_options enable row level security;
drop policy if exists "anyone can read watch options" on public.watch_options;
create policy "anyone can read watch options" on public.watch_options for select to anon, authenticated using (true);
revoke all on public.watch_options from anon, authenticated;
grant select on public.watch_options to anon, authenticated;
grant select on public.watch_options to service_role;

-- One update's checks. p = [{film_id, country, options, link}]: each replaces that film's row for that country, stamped now.
-- Rows for films the database doesn't know are skipped. Secret key only.
create or replace function public.sync_watch_options(p jsonb)
returns integer language plpgsql security definer set search_path = '' as $$
declare
  n integer;
begin
  insert into public.watch_options as w (film_id, country, options, link, checked_at)
  select x.film_id, upper(x.country), coalesce(x.options, '{}'::jsonb), case when x.link ~* '^https://' then x.link end, now()
  from jsonb_to_recordset(coalesce(p, '[]'::jsonb)) as x(film_id text, country text, options jsonb, link text)
  where exists (select 1 from public.films f where f.id = x.film_id) and x.country ~* '^[a-z]{2}$'
  on conflict (film_id, country) do update set options = excluded.options, link = excluded.link, checked_at = now();
  get diagnostics n = row_count;
  return n;
end;
$$;
revoke all on function public.sync_watch_options(jsonb) from public;
revoke execute on function public.sync_watch_options(jsonb) from anon, authenticated;
grant execute on function public.sync_watch_options(jsonb) to service_role;
