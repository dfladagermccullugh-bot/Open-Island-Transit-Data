-- Open Island Transit Data: collector storage.
-- Supabase is only the collector. It holds a rolling buffer of raw responses
-- and the permanent fetch log. Everything else is built from these in the
-- GitHub pipeline.

create extension if not exists pg_cron;
create extension if not exists pg_net with schema extensions;

-- ---------------------------------------------------------------------------
-- fetch_log: one row per HTTP request attempt (the paradata). Kept forever.
-- ---------------------------------------------------------------------------
create table public.fetch_log (
  fetch_id          bigint generated always as identity primary key,
  feed              text not null
                    check (feed in ('vehiclePositions', 'tripUpdates', 'serviceAlerts')),
  scheduled_at      timestamptz,          -- when the scheduler fired
  requested_at      timestamptz,          -- when the function sent the request
  responded_at      timestamptz,          -- when the full body had arrived
  latency_ms        integer,
  http_status       integer,              -- null if no HTTP response at all
  bytes             integer,              -- size of the body as received
  sha256            text,                 -- of the body as received
  header_ts         timestamptz,          -- FeedHeader.timestamp
  entity_count      integer,              -- FeedMessage.entity count
  newest_entity_ts  timestamptz,          -- max vehicle / trip_update timestamp
  error_class       text,                 -- null | timeout | network | http | decode | backoff_skip
  error_msg         text,
  collector_version text not null,
  logged_at         timestamptz not null default clock_timestamp()
);

create index fetch_log_requested_at_idx on public.fetch_log (requested_at);
create index fetch_log_feed_logged_idx on public.fetch_log (feed, logged_at desc);

-- ---------------------------------------------------------------------------
-- raw_response: exact response bytes, gzipped. Deleted only after the GitHub
-- archive manifest confirms the day is stored there.
-- ---------------------------------------------------------------------------
create table public.raw_response (
  fetch_id bigint primary key references public.fetch_log (fetch_id),
  body_gz  bytea not null
);
-- Already gzipped: skip Postgres's own compression attempt.
alter table public.raw_response alter column body_gz set storage external;

-- ---------------------------------------------------------------------------
-- static_gtfs_fetch: one row per static GTFS download attempt.
-- ---------------------------------------------------------------------------
create table public.static_gtfs_fetch (
  static_fetch_id   bigint generated always as identity primary key,
  source            text not null check (source in ('passio', 'trillium')),
  url               text not null,
  requested_at      timestamptz,
  responded_at      timestamptz,
  latency_ms        integer,
  http_status       integer,
  bytes             integer,
  sha256            text,
  zip_has_directory boolean,              -- end-of-central-directory record present
  files             text[],               -- entry names found by walking local headers
  error_class       text,
  error_msg         text,
  collector_version text not null,
  body              bytea,                -- the zip exactly as received
  logged_at         timestamptz not null default clock_timestamp()
);
alter table public.static_gtfs_fetch alter column body set storage external;

-- ---------------------------------------------------------------------------
-- collector_state: single row used for polite backoff.
-- ---------------------------------------------------------------------------
create table public.collector_state (
  id                   integer primary key check (id = 1),
  consecutive_failures integer not null default 0,
  backoff_until        timestamptz,
  updated_at           timestamptz not null default clock_timestamp()
);
insert into public.collector_state (id) values (1);

-- ---------------------------------------------------------------------------
-- Row level security: the public may read; only the service role writes.
-- ---------------------------------------------------------------------------
alter table public.fetch_log         enable row level security;
alter table public.raw_response      enable row level security;
alter table public.static_gtfs_fetch enable row level security;
alter table public.collector_state   enable row level security;

create policy "public read" on public.fetch_log         for select to anon, authenticated using (true);
create policy "public read" on public.raw_response      for select to anon, authenticated using (true);
create policy "public read" on public.static_gtfs_fetch for select to anon, authenticated using (true);
create policy "public read" on public.collector_state   for select to anon, authenticated using (true);

revoke insert, update, delete, truncate on public.fetch_log, public.raw_response,
  public.static_gtfs_fetch, public.collector_state from anon, authenticated;

-- ---------------------------------------------------------------------------
-- record_fetches: called by the edge function once per tick.
-- p_rows is a JSON array; each element is one fetch attempt, optionally with
-- "body_gz_b64" (the gzipped response body, base64).
-- ---------------------------------------------------------------------------
create or replace function public.record_fetches(p_rows jsonb)
returns integer
language plpgsql
security definer
set search_path = ''
as $$
declare
  r         jsonb;
  v_id      bigint;
  v_n       integer := 0;
  v_failed  boolean := false;
  v_fails   integer;
begin
  for r in select * from jsonb_array_elements(p_rows) loop
    insert into public.fetch_log (
      feed, scheduled_at, requested_at, responded_at, latency_ms, http_status,
      bytes, sha256, header_ts, entity_count, newest_entity_ts,
      error_class, error_msg, collector_version
    ) values (
      r->>'feed',
      (r->>'scheduled_at')::timestamptz,
      (r->>'requested_at')::timestamptz,
      (r->>'responded_at')::timestamptz,
      (r->>'latency_ms')::integer,
      (r->>'http_status')::integer,
      (r->>'bytes')::integer,
      r->>'sha256',
      (r->>'header_ts')::timestamptz,
      (r->>'entity_count')::integer,
      (r->>'newest_entity_ts')::timestamptz,
      r->>'error_class',
      r->>'error_msg',
      r->>'collector_version'
    ) returning fetch_id into v_id;

    if r ? 'body_gz_b64' and r->>'body_gz_b64' is not null then
      insert into public.raw_response (fetch_id, body_gz)
      values (v_id, decode(r->>'body_gz_b64', 'base64'));
    end if;

    -- Failures that mean "the host is struggling or refusing us".
    if r->>'error_class' in ('timeout', 'network')
       or (r->>'http_status')::integer = 429
       or (r->>'http_status')::integer >= 500 then
      v_failed := true;
    end if;
    v_n := v_n + 1;
  end loop;

  if v_failed then
    update public.collector_state
       set consecutive_failures = consecutive_failures + 1,
           updated_at = clock_timestamp()
     where id = 1
    returning consecutive_failures into v_fails;
    -- Keep full cadence for the first 3 failing ticks, then back off:
    -- 40 s, 80 s, 160 s ... capped at 10 minutes.
    if v_fails > 3 then
      update public.collector_state
         set backoff_until = clock_timestamp()
             + least(interval '20 seconds' * power(2, least(v_fails - 3, 10)), interval '10 minutes')
       where id = 1;
    end if;
  else
    update public.collector_state
       set consecutive_failures = 0, backoff_until = null, updated_at = clock_timestamp()
     where id = 1 and (consecutive_failures <> 0 or backoff_until is not null);
  end if;

  return v_n;
end;
$$;

-- ---------------------------------------------------------------------------
-- record_static: called by the edge function for static GTFS downloads.
-- ---------------------------------------------------------------------------
create or replace function public.record_static(p_row jsonb)
returns bigint
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_id bigint;
begin
  insert into public.static_gtfs_fetch (
    source, url, requested_at, responded_at, latency_ms, http_status, bytes, sha256,
    zip_has_directory, files, error_class, error_msg, collector_version, body
  ) values (
    p_row->>'source',
    p_row->>'url',
    (p_row->>'requested_at')::timestamptz,
    (p_row->>'responded_at')::timestamptz,
    (p_row->>'latency_ms')::integer,
    (p_row->>'http_status')::integer,
    (p_row->>'bytes')::integer,
    p_row->>'sha256',
    (p_row->>'zip_has_directory')::boolean,
    case when p_row ? 'files' and jsonb_typeof(p_row->'files') = 'array'
         then array(select jsonb_array_elements_text(p_row->'files')) end,
    p_row->>'error_class',
    p_row->>'error_msg',
    p_row->>'collector_version',
    case when p_row->>'body_b64' is not null then decode(p_row->>'body_b64', 'base64') end
  ) returning static_fetch_id into v_id;
  return v_id;
end;
$$;

revoke all on function public.record_fetches(jsonb) from public, anon, authenticated;
revoke all on function public.record_static(jsonb)  from public, anon, authenticated;
grant execute on function public.record_fetches(jsonb) to service_role;
grant execute on function public.record_static(jsonb)  to service_role;

-- ---------------------------------------------------------------------------
-- Public views.
-- ---------------------------------------------------------------------------

-- raw_export: what the nightly GitHub archive job pulls.
create view public.raw_export
with (security_invoker = true) as
select f.*, encode(r.body_gz, 'base64') as body_gz_b64
from public.fetch_log f
join public.raw_response r using (fetch_id);

-- static_export: static GTFS downloads with the zip as base64.
create view public.static_export
with (security_invoker = true) as
select static_fetch_id, source, url, requested_at, responded_at, latency_ms, http_status,
       bytes, sha256, zip_has_directory, files, error_class, error_msg, collector_version,
       logged_at, encode(body, 'base64') as body_b64
from public.static_gtfs_fetch;

-- status_now: read live by the site and the watchdog.
create view public.status_now
with (security_invoker = true) as
with today as (
  select *
  from public.fetch_log
  where logged_at >= (date_trunc('day', now() at time zone 'America/Los_Angeles')
                      at time zone 'America/Los_Angeles')
),
last_ok as (
  select distinct on (feed) feed, requested_at, entity_count, newest_entity_ts, latency_ms
  from public.fetch_log
  where http_status = 200 and error_class is null
    and logged_at >= now() - interval '2 days'
  order by feed, logged_at desc
)
select
  f.feed,
  l.requested_at                              as last_ok_at,
  extract(epoch from now() - l.requested_at)::integer as last_ok_age_s,
  l.entity_count                              as last_entity_count,
  l.newest_entity_ts                          as last_newest_entity_ts,
  l.latency_ms                                as last_latency_ms,
  count(t.fetch_id)                           as attempts_today,
  count(t.fetch_id) filter (where t.http_status = 200 and t.error_class is null) as ok_today,
  count(t.fetch_id) filter (where t.error_class is not null or t.http_status <> 200) as failed_today,
  max(t.entity_count)                         as max_entities_today,
  (select min(logged_at) from public.fetch_log) as collecting_since
from (values ('vehiclePositions'), ('tripUpdates'), ('serviceAlerts')) as f (feed)
left join last_ok l on l.feed = f.feed
left join today t on t.feed = f.feed
group by f.feed, l.requested_at, l.entity_count, l.newest_entity_ts, l.latency_ms;

grant select on public.raw_export, public.static_export, public.status_now to anon, authenticated;
