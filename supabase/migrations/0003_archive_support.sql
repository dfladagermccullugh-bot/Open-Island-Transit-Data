-- Support for the nightly archive, the prune job and the watchdog.

-- A service day is a calendar date in America/Los_Angeles; rows are assigned
-- to days by logged_at, which is never null.
create or replace function public.pacific_day_start(p_date date)
returns timestamptz
language sql
immutable
set search_path = ''
as $$
  select (p_date::timestamp at time zone 'America/Los_Angeles');
$$;

-- day_digest: what the archive must reproduce before a day may be pruned.
-- raw_rows counts rows that still have a stored body; digest is the md5 of
-- "fetch_id:sha256" for those rows, comma-joined in fetch_id order.
create or replace function public.day_digest(p_date date)
returns table (service_date date, fetch_rows bigint, raw_rows bigint, digest text)
language sql
stable
set search_path = ''
as $$
  select
    p_date,
    count(*),
    count(r.fetch_id),
    md5(coalesce(string_agg(f.fetch_id::text || ':' || f.sha256, ',' order by f.fetch_id)
                 filter (where r.fetch_id is not null), ''))
  from public.fetch_log f
  left join public.raw_response r using (fetch_id)
  where f.logged_at >= public.pacific_day_start(p_date)
    and f.logged_at <  public.pacific_day_start(p_date + 1);
$$;

grant execute on function public.pacific_day_start(date) to anon, authenticated, service_role;
grant execute on function public.day_digest(date) to anon, authenticated, service_role;

-- prune_raw: delete one day's stored bodies, but only if the caller proves the
-- archive holds exactly what is stored here, and the day is at least 3 days old.
-- fetch_log rows are never deleted.
create or replace function public.prune_raw(p_date date, p_raw_rows bigint, p_digest text)
returns bigint
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_today   date := (now() at time zone 'America/Los_Angeles')::date;
  v_rows    bigint;
  v_digest  text;
  v_deleted bigint;
begin
  if p_date > v_today - 3 then
    raise exception 'refusing to prune %: newer than 3 days', p_date;
  end if;
  select d.raw_rows, d.digest into v_rows, v_digest from public.day_digest(p_date) d;
  if v_rows = 0 then
    return 0;
  end if;
  if v_rows is distinct from p_raw_rows or v_digest is distinct from p_digest then
    raise exception 'refusing to prune %: archive (% rows, %) does not match database (% rows, %)',
      p_date, p_raw_rows, p_digest, v_rows, v_digest;
  end if;
  delete from public.raw_response r
  using public.fetch_log f
  where f.fetch_id = r.fetch_id
    and f.logged_at >= public.pacific_day_start(p_date)
    and f.logged_at <  public.pacific_day_start(p_date + 1);
  get diagnostics v_deleted = row_count;
  return v_deleted;
end;
$$;

revoke all on function public.prune_raw(date, bigint, text) from public, anon, authenticated;
grant execute on function public.prune_raw(date, bigint, text) to service_role;

-- prune_static: same idea for static GTFS downloads, by id list.
create or replace function public.prune_static(p_ids bigint[], p_sha256 text[])
returns bigint
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_deleted bigint;
begin
  if coalesce(array_length(p_ids, 1), 0) <> coalesce(array_length(p_sha256, 1), 0) then
    raise exception 'ids and hashes differ in length';
  end if;
  update public.static_gtfs_fetch s
     set body = null
    from unnest(p_ids, p_sha256) as a (id, sha)
   where s.static_fetch_id = a.id
     and s.sha256 = a.sha
     and s.body is not null
     and s.logged_at < now() - interval '3 days';
  get diagnostics v_deleted = row_count;
  return v_deleted;
end;
$$;

revoke all on function public.prune_static(bigint[], text[]) from public, anon, authenticated;
grant execute on function public.prune_static(bigint[], text[]) to service_role;

-- collector_health: read by the watchdog.
create or replace function public.collector_health()
returns table (
  database_bytes bigint,
  fetch_log_rows bigint,
  raw_rows bigint,
  oldest_raw_at timestamptz,
  static_bodies bigint,
  consecutive_failures integer,
  backoff_until timestamptz
)
language sql
stable
security definer
set search_path = ''
as $$
  select
    pg_database_size(current_database()),
    (select count(*) from public.fetch_log),
    (select count(*) from public.raw_response),
    (select min(f.logged_at) from public.fetch_log f join public.raw_response r using (fetch_id)),
    (select count(*) from public.static_gtfs_fetch where body is not null),
    s.consecutive_failures,
    s.backoff_until
  from public.collector_state s where s.id = 1;
$$;

grant execute on function public.collector_health() to anon, authenticated, service_role;

-- Daily prune: 11:40 UTC (3:40 AM PST), after the nightly archive has run.
select cron.schedule(
  'prune-archived',
  '40 11 * * *',
  $$select public.collector_tick('prune', array[]::text[])$$
);
