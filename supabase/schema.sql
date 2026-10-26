-- Kinoprogram: one profile row per signed-in user.
-- Paste this whole file into Supabase → SQL Editor → New query → Run. Safe to re-run.

-- Safety check: this must only ever run in a brand-new, empty project. If the database already has
-- other tables (a different app's project), stop before changing anything.
do $$
begin
  if exists (select 1 from information_schema.tables
             where table_schema = 'public' and table_type = 'BASE TABLE' and table_name not in ('profiles', 'friendships')) then
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
