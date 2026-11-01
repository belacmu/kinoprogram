-- Kinoprogram: one profile row per signed-in user.
-- Paste this whole file into Supabase → SQL Editor → New query → Run. Safe to re-run.

-- Safety check: this must only ever run in a brand-new, empty project. If the database already has
-- other tables (a different app's project), stop before changing anything.
do $$
begin
  if exists (select 1 from information_schema.tables
             where table_schema = 'public' and table_type = 'BASE TABLE' and table_name not in ('profiles', 'friendships', 'screenings', 'screening_people')) then
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

-- ---------------------------------------------------------------------------------------------------------------------
-- Screenings: someone invites people to a showing. Who is TOLD about it (their Feed) is its visibility; the link works at every level.
--   private  = only people with the link          friends = also the host's friends          everyone = every user (admins only, for now)
-- For now only admins can create them:  update public.profiles set is_admin = true where email = 'you@example.com';  (SQL editor only)
-- Nothing is readable or writable directly: everything goes through the functions below (security definer).
alter table public.profiles add column if not exists is_admin boolean not null default false;

create table if not exists public.screenings (
  id          uuid primary key default gen_random_uuid(),
  token       text not null unique default substr(replace(gen_random_uuid()::text, '-', ''), 1, 16),
  host        uuid not null references public.profiles (id) on delete cascade,
  visibility  text not null default 'private' check (visibility in ('private', 'friends', 'everyone')),
  region      text not null,
  film_id     text not null,
  film_title  text not null,
  film_year   text,
  poster      text,
  cinema      text not null,
  starts_at   text not null,                       -- the cinema's local time, "YYYY-MM-DDTHH:MM"
  ticket_url  text,
  note        text check (char_length(note) <= 500),
  cancelled   boolean not null default false,
  created_at  timestamptz not null default now()
);
alter table public.screenings add column if not exists visibility text not null default 'private';

-- Who is going, with the row and seats they say they bought (all optional; seats are self-reported, nothing checks them).
create table if not exists public.screening_people (
  screening   uuid not null references public.screenings (id) on delete cascade,
  person      uuid not null references public.profiles (id) on delete cascade,
  row_label   text check (char_length(row_label) <= 4),
  seats       int[] not null default '{}',
  created_at  timestamptz not null default now(),
  primary key (screening, person)
);

alter table public.screenings enable row level security;
alter table public.screening_people enable row level security;
revoke all on public.screenings, public.screening_people from anon, authenticated;

create or replace function public.am_admin()
returns boolean language sql stable security definer set search_path = '' as $$
  select coalesce((select is_admin from public.profiles where id = (select auth.uid())), false);
$$;
revoke all on function public.am_admin() from public;
grant execute on function public.am_admin() to authenticated;

create or replace function public.create_screening(p_visibility text, p_region text, p_film_id text, p_film_title text, p_film_year text,
                                                   p_poster text, p_cinema text, p_starts_at text, p_ticket_url text, p_note text)
returns text language plpgsql security definer set search_path = '' as $$
declare t text;
begin
  if not public.am_admin() then raise exception 'Only admins can host screenings for now'; end if;
  if p_visibility not in ('private', 'friends', 'everyone') then raise exception 'Unknown visibility'; end if;
  insert into public.screenings (host, visibility, region, film_id, film_title, film_year, poster, cinema, starts_at, ticket_url, note)
  values ((select auth.uid()), p_visibility, left(p_region, 20), left(p_film_id, 80), left(p_film_title, 200), left(p_film_year, 10), left(p_poster, 500),
          left(p_cinema, 120), left(p_starts_at, 16), nullif(left(btrim(p_ticket_url), 500), ''), nullif(left(btrim(p_note), 500), ''))
  returning token into t;
  return t;
end;
$$;
revoke all on function public.create_screening(text, text, text, text, text, text, text, text, text, text) from public;
grant execute on function public.create_screening(text, text, text, text, text, text, text, text, text, text) to authenticated;

-- One screening by its link: readable by anyone with the token (signed in or not). Names are display names, never emails.
create or replace function public.get_screening(p_token text)
returns jsonb language sql stable security definer set search_path = '' as $$
  select jsonb_build_object(
    'token', s.token, 'visibility', s.visibility, 'region', s.region, 'film_id', s.film_id, 'film_title', s.film_title, 'film_year', s.film_year,
    'poster', s.poster, 'cinema', s.cinema, 'starts_at', s.starts_at, 'ticket_url', s.ticket_url, 'note', s.note,
    'cancelled', s.cancelled, 'mine_host', s.host = (select auth.uid()),
    'host_name', coalesce(nullif(h.display_name, ''), split_part(h.email, '@', 1)),
    'people', coalesce((
      select jsonb_agg(jsonb_build_object(
        'name', coalesce(nullif(p.display_name, ''), split_part(p.email, '@', 1)), 'row', sp.row_label, 'seats', sp.seats,
        'me', sp.person = (select auth.uid())) order by sp.created_at)
      from public.screening_people sp join public.profiles p on p.id = sp.person where sp.screening = s.id), '[]'::jsonb))
  from public.screenings s join public.profiles h on h.id = s.host
  where s.token = p_token;
$$;
revoke all on function public.get_screening(text) from public;
grant execute on function public.get_screening(text) to anon, authenticated;

-- The screenings this user can see, for the pinned part of the Feed: hosted by them, joined by them, their friends' (friends or
-- everyone), and everyone-visible ones. Upcoming only (the page drops the ones already over, by the cinema's own clock).
create or replace function public.visible_screenings()
returns jsonb language sql stable security definer set search_path = '' as $$
  select coalesce(jsonb_agg(x.j order by x.j ->> 'starts_at'), '[]'::jsonb) from (
    select jsonb_build_object(
      'token', s.token, 'visibility', s.visibility, 'region', s.region, 'film_id', s.film_id, 'film_title', s.film_title, 'poster', s.poster,
      'cinema', s.cinema, 'starts_at', s.starts_at, 'created', to_char(s.created_at at time zone 'utc', 'YYYY-MM-DD"T"HH24:MI'),
      'host_name', coalesce(nullif(h.display_name, ''), split_part(h.email, '@', 1)), 'mine_host', s.host = (select auth.uid()),
      'going', (select count(*) from public.screening_people sp where sp.screening = s.id),
      'me_going', exists (select 1 from public.screening_people sp where sp.screening = s.id and sp.person = (select auth.uid())),
      'names', coalesce((select jsonb_agg(coalesce(nullif(p.display_name, ''), split_part(p.email, '@', 1)) order by sp.created_at)
                         from (select * from public.screening_people q where q.screening = s.id order by q.created_at limit 6) sp
                         join public.profiles p on p.id = sp.person), '[]'::jsonb)) as j
    from public.screenings s join public.profiles h on h.id = s.host
    where (select auth.uid()) is not null and not s.cancelled
      and s.starts_at >= to_char((now() at time zone 'utc') - interval '14 hours', 'YYYY-MM-DD"T"HH24:MI')
      and (s.host = (select auth.uid())
           or exists (select 1 from public.screening_people sp where sp.screening = s.id and sp.person = (select auth.uid()))
           or s.visibility = 'everyone'
           or (s.visibility = 'friends' and exists (select 1 from public.friendships f
                 where (f.a = (select auth.uid()) and f.b = s.host) or (f.b = (select auth.uid()) and f.a = s.host))))
    order by s.starts_at limit 30) x;
$$;
revoke all on function public.visible_screenings() from public;
grant execute on function public.visible_screenings() to authenticated;

create or replace function public.join_screening(p_token text, p_row text, p_seats int[])
returns void language plpgsql security definer set search_path = '' as $$
declare sid uuid; clean int[];
begin
  if (select auth.uid()) is null then raise exception 'Sign in to join'; end if;
  select id into sid from public.screenings where token = p_token and not cancelled;
  if sid is null then raise exception 'Screening not found'; end if;
  select coalesce(array_agg(distinct x order by x), '{}') into clean from unnest(coalesce(p_seats, '{}')) x where x between 1 and 99;
  insert into public.screening_people (screening, person, row_label, seats)
  values (sid, (select auth.uid()), nullif(upper(left(btrim(p_row), 4)), ''), clean[1:12])
  on conflict (screening, person) do update set row_label = excluded.row_label, seats = excluded.seats;
end;
$$;
revoke all on function public.join_screening(text, text, int[]) from public;
grant execute on function public.join_screening(text, text, int[]) to authenticated;

create or replace function public.leave_screening(p_token text)
returns void language sql security definer set search_path = '' as $$
  delete from public.screening_people where person = (select auth.uid())
    and screening = (select id from public.screenings where token = p_token);
$$;
revoke all on function public.leave_screening(text) from public;
grant execute on function public.leave_screening(text) to authenticated;

-- Cancel your own screening (its page says so; nobody can join, and it leaves everyone's Feed).
create or replace function public.cancel_screening(p_token text)
returns void language sql security definer set search_path = '' as $$
  update public.screenings set cancelled = true where token = p_token and host = (select auth.uid());
$$;
revoke all on function public.cancel_screening(text) from public;
grant execute on function public.cancel_screening(text) to authenticated;
