-- Tests for schema.sql: film cards, resolve_films, prune_showings and past plans. Run with supabase/tests/run.sh, which applies
-- stubs.sql and schema.sql (twice, as it must be safe to re-run) to an empty database first. Any failed assert stops the run.
\set ON_ERROR_STOP on
\set QUIET on
set client_min_messages = warning;
\o /dev/null

-- ---------------------------------------------------------------- a film's card is kept, and only replaced by a card
select public.sync_films('{"films": [
  {"id": "fw-klara", "title": "Klara and the Sun", "year": "2026", "tmdb": 858035,
   "meta": {"poster": "https://image.tmdb.org/k.jpg", "blurb": "A robot.", "last": {"oslo": "2026-11-20"}}},
  {"id": "cm-klara", "title": "Klara og solen", "year": "2026"},
  {"id": "cc-heat", "title": "Heat", "year": "1995"}],
  "aliases": [{"alias": "fw-klara", "film_id": "fw-klara"}, {"alias": "fw-EDI1", "film_id": "fw-klara"},
              {"alias": "cm-klara", "film_id": "cm-klara"}, {"alias": "cc-heat", "film_id": "cc-heat"}],
  "live": [], "complete": false}');
do $$ begin
  assert (select meta ->> 'poster' from public.films where id = 'fw-klara') = 'https://image.tmdb.org/k.jpg', 'card stored';
  assert (select meta from public.films where id = 'cc-heat') = '{}'::jsonb, 'a film sent without a card gets an empty one';
end $$;

-- A later run that sends the film without a card (an older scraper) keeps it; one that sends a card replaces it.
select public.sync_films('{"films": [{"id": "fw-klara", "title": "Klara and the Sun", "year": "2026"}], "live": []}');
do $$ begin
  assert (select meta ->> 'blurb' from public.films where id = 'fw-klara') = 'A robot.', 'card kept when none is sent';
end $$;
select public.sync_films('{"films": [{"id": "fw-klara", "title": "Klara and the Sun", "year": "2026",
  "meta": {"poster": "https://image.tmdb.org/k.jpg", "blurb": "A robot.", "last": {"oslo": "2026-11-20", "winnipeg": "2026-12-01"}}}],
  "live": []}');
do $$ begin
  assert (select meta -> 'last' ->> 'winnipeg' from public.films where id = 'fw-klara') = '2026-12-01', 'card replaced by a new one';
end $$;

-- A film that leaves every cinema keeps its card.
select public.sync_films('{"films": [], "live": ["cc-heat"], "complete": true}');
do $$ begin
  assert (select gone_at is not null from public.films where id = 'fw-klara'), 'film in no region is gone';
  assert (select meta ->> 'poster' from public.films where id = 'fw-klara') is not null, 'a gone film keeps its card';
end $$;

-- ---------------------------------------------------------------- resolve_films: any id to the film it is now
select public.sync_films('{"films": [], "merges": [{"loser": "cm-klara", "winner": "fw-klara"}], "live": []}');
insert into public.showings (id, film_id, region, cinema, starts, title) values
  ('fw-klara|2099-01-01T19:00|Cinematheque', 'fw-klara', 'winnipeg', 'Cinematheque', '2099-01-01T19:00', 'Klara'),
  ('fw-klara|2099-01-02T19:00|Vega', 'fw-klara', 'oslo', 'Vega', '2099-01-02T19:00', 'Klara'),
  ('fw-klara|2020-01-01T19:00|Saga', 'fw-klara', 'westman', 'Saga', '2020-01-01T19:00', 'Klara');
update public.showings set gone_at = now() where id = 'fw-klara|2099-01-02T19:00|Vega';   -- cancelled: not "playing in Oslo"

set role anon;   -- public, like the tables
do $$
declare r record;
begin
  assert (select count(*) from public.resolve_films(array['fw-klara', 'fw-EDI1', 'cm-klara', 'cc-heat', 'nobody'])) = 4,
    'one row per known id; an unknown id is left out';
  for r in select * from public.resolve_films(array['fw-EDI1', 'cm-klara']) loop
    assert r.id = 'fw-klara', format('%s (an old listing id / a merged film) resolves to fw-klara, got %s', r.asked, r.id);
    assert r.meta ->> 'blurb' = 'A robot.', 'with its card';
    assert r.regions = array['winnipeg'], format('only regions with upcoming showings that are not gone, got %s', r.regions);
  end loop;
  assert (select regions from public.resolve_films(array['cc-heat'])) = '{}', 'no showings: no regions';
end $$;
reset role;

-- ---------------------------------------------------------------- prune_showings: finished showings go, unless planned
insert into auth.users (id, email) values ('00000000-0000-0000-0000-00000000000a', 'a@example.com');
select set_config('test.uid', '00000000-0000-0000-0000-00000000000a', false);
insert into public.showings (id, film_id, region, cinema, starts, title) values
  ('cc-heat|2020-02-01T19:00|Vega', 'cc-heat', 'oslo', 'Vega', '2020-02-01T19:00', 'Heat'),
  ('cc-heat|2020-02-01T21:00|Vega', 'cc-heat', 'oslo', 'Vega', '2020-02-01T21:00', 'Heat'),
  ('cc-heat|2020-02-02T19:00|Vega', 'cc-heat', 'oslo', 'Vega', '2020-02-02T19:00', 'Heat'),
  ('cc-heat|2099-02-01T19:00|Vega', 'cc-heat', 'oslo', 'Vega', '2099-02-01T19:00', 'Heat');
-- The 19:00 moved to 21:00 (replaced_by); a future showing points at a past one that will be deleted.
update public.showings set gone_at = now(), replaced_by = 'cc-heat|2020-02-01T21:00|Vega' where id = 'cc-heat|2020-02-01T19:00|Vega';
update public.showings set gone_at = now(), replaced_by = 'cc-heat|2020-02-02T19:00|Vega' where id = 'cc-heat|2099-02-01T19:00|Vega';
insert into public.plans (user_id, showing_id, seats) values
  ('00000000-0000-0000-0000-00000000000a', 'cc-heat|2020-02-01T21:00|Vega', 'H12'),
  ('00000000-0000-0000-0000-00000000000a', 'cc-heat|2099-02-01T19:00|Vega', null);

set role anon;
do $$ begin
  begin
    perform public.prune_showings('2020-03-01');
    assert false, 'anon must not prune';
  exception when insufficient_privilege then null;
  end;
end $$;
reset role;
set role authenticated;
do $$ begin
  begin
    perform public.prune_showings('2020-03-01');
    assert false, 'a signed-in user must not prune';
  exception when insufficient_privilege then null;
  end;
end $$;
reset role;

do $$ begin
  begin
    perform public.prune_showings(to_char(now(), 'YYYY-MM-DD'));
    assert false, 'today is refused (showings still on)';
  exception when invalid_parameter_value then null;
  end;
end $$;

set role service_role;
do $$
declare n int := public.prune_showings('2020-03-01');
begin
  -- Deleted: fw-klara's 2020 Westman showing, Heat's 19:00 (moved) and Heat's 2 Feb (nobody's plan). Kept: the planned 21:00.
  assert n = 3, format('3 finished, unplanned showings deleted, got %s', n);
end $$;
reset role;
do $$
declare s record;
begin
  assert (select count(*) from public.plans) = 2, 'plans are never deleted by pruning';
  select * into s from public.showings where id = 'cc-heat|2020-02-01T21:00|Vega';
  assert found, 'a finished showing someone planned is kept';
  assert s.film_id = 'cc-heat' and s.cinema = 'Vega' and s.starts = '2020-02-01T21:00', 'with its film, cinema and time';
  assert (select replaced_by from public.showings where id = 'cc-heat|2099-02-01T19:00|Vega') is null,
    'a pointer to a deleted showing is cleared';
  assert exists (select 1 from public.showings where id = 'fw-klara|2099-01-01T19:00|Cinematheque'), 'upcoming showings stay';
  assert (select count(*) from public.films) = 3, 'films are never deleted';
end $$;

-- The past plan still loads the way the app loads plans (plans joined to their showing), as its owner.
set role authenticated;
do $$
declare r record;
begin
  select p.seats, s.film_id, s.starts, s.cinema into r
    from public.plans p join public.showings s on s.id = p.showing_id
    where p.showing_id = 'cc-heat|2020-02-01T21:00|Vega';
  assert r.film_id = 'cc-heat' and r.starts = '2020-02-01T21:00' and r.seats = 'H12', 'a past plan says which film and when';
end $$;
reset role;

-- Pruning again changes nothing.
do $$ begin
  assert public.prune_showings('2020-03-01') = 0, 'nothing left to prune';
end $$;

-- ---------------------------------------------------------------- watchlists keep stable ids (existing behaviour)
update public.profiles set watchlist = array['fw-EDI1', 'cm-klara', 'fw-klara', 'unknown-id']
  where id = '00000000-0000-0000-0000-00000000000a';
do $$ begin
  assert (select watchlist from public.profiles where id = '00000000-0000-0000-0000-00000000000a') = array['fw-klara', 'unknown-id'],
    'old and merged ids become the film id, once; unknown ids stay';
end $$;

-- ---------------------------------------------------------------- where to watch
do $$ begin
  begin
    set local role anon;
    perform public.sync_watch_options('[]');
    assert false, 'anon must not write where-to-watch';
  exception when insufficient_privilege then null;
  end;
end $$;
reset role;
set role service_role;
do $$
declare n int;
begin
  n := public.sync_watch_options('[
    {"film_id": "fw-klara", "country": "NO", "options": {"stream": [{"name": "Netflix", "logo": "/nf.jpg"}]},
     "link": "https://www.themoviedb.org/movie/858035/watch?locale=NO"},
    {"film_id": "fw-klara", "country": "ca", "options": {}, "link": "javascript:alert(1)"},
    {"film_id": "no-such-film", "country": "NO", "options": {}}]');
  assert n = 2, format('two rows written, the unknown film skipped (got %s)', n);
  -- A later check replaces the row.
  perform public.sync_watch_options('[{"film_id": "fw-klara", "country": "NO", "options": {"rent": [{"name": "Apple TV", "logo": ""}]}, "link": ""}]');
end $$;
reset role;
set role anon;
do $$ begin
  assert (select options -> 'rent' -> 0 ->> 'name' from public.watch_options where film_id = 'fw-klara' and country = 'NO') = 'Apple TV',
    'anyone can read it; a new check replaced the old options';
  assert (select options from public.watch_options where film_id = 'fw-klara' and country = 'NO') ? 'stream' = false, 'nothing left over';
  assert (select link from public.watch_options where film_id = 'fw-klara' and country = 'CA') is null, 'a link that is not https is dropped';
  begin
    insert into public.watch_options (film_id, country) values ('fw-klara', 'ES');
    assert false, 'anon must not insert';
  exception when insufficient_privilege then null;
  end;
end $$;
reset role;

-- ---------------------------------------------------------------- the private mark: films someone has wanted to see
-- The watchlist saved above (fw-EDI1, cm-klara, fw-klara, unknown-id) marked fw-klara through the trigger.
do $$ begin
  assert (select array_agg(film_id order by film_id) from public.film_marks) = array['fw-klara'], 'a signed-in watchlist marks its films';
end $$;
set role anon;
do $$ begin
  perform public.mark_watchlisted(array['cc-heat', 'nobody-knows-this', 'cc-heat']);   -- signed out: a heart tapped
  begin
    perform 1 from public.film_marks;
    assert false, 'nobody but the update may read the marks';
  exception when insufficient_privilege then null;
  end;
  begin
    perform public.mark_films(array['cc-heat']);
    assert false, 'the uncapped function is not public';
  exception when insufficient_privilege then null;
  end;
end $$;
reset role;
do $$ begin
  assert (select array_agg(film_id order by film_id) from public.film_marks) = array['cc-heat', 'fw-klara'], 'anyone can mark; unknown ids ignored; once each';
end $$;
update public.profiles set watchlist = '{}' where id = '00000000-0000-0000-0000-00000000000a';
do $$ begin
  assert exists (select 1 from public.film_marks where film_id = 'fw-klara'), 'a mark stays when the film leaves the list';
end $$;

\echo 'database tests passed'
