-- Open Island Transit Data: a record of prune runs, longer scheduler
-- timeouts for slow jobs, and a cheaper status view.

-- ---------------------------------------------------------------------------
-- prune_log: one row per day the prune job tried, or one row with a note when
-- it had nothing to do. Until now the outcome of a prune run was kept only in
-- pg_net's response table, which holds a few hours.
-- ---------------------------------------------------------------------------
create table public.prune_log (
  prune_id          bigint generated always as identity primary key,
  ran_at            timestamptz not null default clock_timestamp(),
  service_date      date,                 -- null for a run-level note or error
  files_confirmed   integer,              -- archive files found at their recorded size
  deleted           bigint,               -- stored responses deleted
  static_cleared    bigint,               -- static GTFS bodies cleared
  error             text,
  note              text,
  collector_version text not null
);

alter table public.prune_log enable row level security;
create policy "public read" on public.prune_log for select to anon, authenticated using (true);
revoke insert, update, delete, truncate on public.prune_log from anon, authenticated;
grant select on public.prune_log to anon, authenticated;

create or replace function public.record_prune(p_rows jsonb)
returns integer
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_count integer;
begin
  insert into public.prune_log
    (service_date, files_confirmed, deleted, static_cleared, error, note, collector_version)
  select
    (r ->> 'service_date')::date,
    (r ->> 'files_confirmed')::integer,
    (r ->> 'deleted')::bigint,
    (r ->> 'static_cleared')::bigint,
    r ->> 'error',
    r ->> 'note',
    coalesce(r ->> 'collector_version', 'unknown')
  from jsonb_array_elements(p_rows) as r;
  get diagnostics v_count = row_count;
  return v_count;
end;
$$;

revoke all on function public.record_prune(jsonb) from public, anon, authenticated;
grant execute on function public.record_prune(jsonb) to service_role;

-- ---------------------------------------------------------------------------
-- collector_tick: same as before, except that the scheduler now waits longer
-- for the slow jobs. A static download may take 60 seconds and a prune run
-- checks several files per day, so 25 seconds cut their replies short.
-- Realtime ticks keep 25 seconds.
-- ---------------------------------------------------------------------------
create or replace function public.collector_tick(p_kind text, p_targets text[])
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_until  timestamptz;
  v_url    text;
  v_secret text;
  v_now    timestamptz := clock_timestamp();
begin
  if p_kind = 'realtime' then
    select backoff_until into v_until from public.collector_state where id = 1;
    if v_until is not null and v_now < v_until then
      insert into public.fetch_log (feed, scheduled_at, error_class, error_msg, collector_version)
      select t, v_now, 'backoff_skip', 'backing off until ' || v_until::text, 'scheduler'
      from unnest(p_targets) as t;
      return;
    end if;
  end if;

  select decrypted_secret into v_url
    from vault.decrypted_secrets where name = 'collector_function_url';
  select decrypted_secret into v_secret
    from vault.decrypted_secrets where name = 'collector_secret';
  if v_url is null or v_secret is null then
    raise exception 'collector secrets are not configured in Vault';
  end if;

  perform net.http_post(
    url     := v_url,
    headers := jsonb_build_object(
                 'Content-Type', 'application/json',
                 'x-collector-secret', v_secret),
    body    := jsonb_build_object(
                 'kind', p_kind,
                 'targets', to_jsonb(p_targets),
                 'scheduled_at', v_now),
    timeout_milliseconds := case p_kind
                              when 'static' then 90000
                              when 'prune'  then 140000
                              else 25000
                            end
  );
end;
$$;

revoke all on function public.collector_tick(text, text[]) from public, anon, authenticated;

-- ---------------------------------------------------------------------------
-- status_now: collecting_since read the whole fetch log on every page view.
-- Rows are never deleted and fetch_id only grows, so the first row by primary
-- key gives the same answer from the index.
-- ---------------------------------------------------------------------------
create or replace view public.status_now
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
  (select logged_at from public.fetch_log order by fetch_id limit 1) as collecting_since
from (values ('vehiclePositions'), ('tripUpdates'), ('serviceAlerts')) as f (feed)
left join last_ok l on l.feed = f.feed
left join today t on t.feed = f.feed
group by f.feed, l.requested_at, l.entity_count, l.newest_entity_ts, l.latency_ms;

grant select on public.status_now to anon, authenticated;
