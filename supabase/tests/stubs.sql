-- The parts of a Supabase project that schema.sql relies on, for testing it on a plain Postgres (supabase/tests/run.sh).
-- auth.uid() is whoever the test sets: select set_config('test.uid', '<uuid>', false).
create role anon;
create role authenticated;
create role service_role;
create schema auth;
grant usage on schema auth to anon, authenticated, service_role;
create table auth.users (id uuid primary key, email text, raw_user_meta_data jsonb not null default '{}'::jsonb);
create function auth.uid() returns uuid language sql stable as $$ select nullif(current_setting('test.uid', true), '')::uuid $$;
create function auth.jwt() returns jsonb language sql stable as $$ select '{}'::jsonb $$;
grant execute on function auth.uid(), auth.jwt() to anon, authenticated, service_role;
grant usage on schema public to anon, authenticated, service_role;
