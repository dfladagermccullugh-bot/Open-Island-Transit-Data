-- Open Island Transit Data: two checks from outside GitHub (plan of
-- November 27, 2026, Phase 2). The collector jobs (collect-*) are untouched.
--
-- 1. When the dispatch token expires. GitHub sends the expiry of a
--    fine-grained token in the header github-authentication-token-expiration,
--    but not on the 204 replies to workflow dispatches. Once a day,
--    check_dispatch_token() reads the repository (an authenticated GET, one
--    request of the 5,000 an hour the token may make) and
--    record_outside_checks() stores the date. The watchdog reads it from
--    status_now and reports 21 days ahead (a look) and 7 days ahead (a
--    problem).
-- 2. Which day the live site's figures run through. Every 6 hours
--    request_site_build() reads the site's data/build.json, and
--    record_outside_checks() stores data_through and built_at, so the live
--    badge can say when the site has fallen behind.
--
-- net._http_response keeps replies for a few hours; each request's id is kept
-- in collector_state and its reply read back a few minutes later.

alter table public.collector_state
  add column if not exists dispatch_token_expires_at timestamptz,
  add column if not exists dispatch_token_checked_at timestamptz,
  add column if not exists token_request_id bigint,
  add column if not exists site_data_through date,
  add column if not exists site_built_at timestamptz,
  add column if not exists site_checked_at timestamptz,
  add column if not exists site_request_id bigint;

create or replace function public.check_dispatch_token()
returns bigint
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_token text;
  v_id bigint;
begin
  select decrypted_secret into v_token
    from vault.decrypted_secrets where name = 'github_dispatch_token';
  if v_token is null then
    return null;
  end if;
  v_id := net.http_get(
    url     := 'https://api.github.com/repos/dfladagermccullugh-bot/Open-Island-Transit-Data',
    headers := jsonb_build_object(
                 'Authorization', 'Bearer ' || v_token,
                 'Accept', 'application/vnd.github+json',
                 'X-GitHub-Api-Version', '2022-11-28',
                 'User-Agent', 'open-island-transit-data-scheduler'),
    timeout_milliseconds := 20000
  );
  update public.collector_state set token_request_id = v_id where id = 1;
  return v_id;
end;
$$;

create or replace function public.request_site_build()
returns bigint
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_id bigint;
begin
  v_id := net.http_get(
    url     := 'https://dfladagermccullugh-bot.github.io/Open-Island-Transit-Data/data/build.json',
    headers := jsonb_build_object('User-Agent', 'open-island-transit-data-scheduler'),
    timeout_milliseconds := 20000
  );
  update public.collector_state set site_request_id = v_id where id = 1;
  return v_id;
end;
$$;

-- Reads back the replies to the two requests above, if they have arrived.
-- A reply without the header or with an unreadable body changes nothing.
create or replace function public.record_outside_checks()
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_state public.collector_state;
  v_reply record;
  v_body jsonb;
  v_expiry text;
begin
  select * into v_state from public.collector_state where id = 1;
  if v_state.token_request_id is not null then
    select status_code, headers into v_reply
      from net._http_response where id = v_state.token_request_id;
    if found then
      v_expiry := v_reply.headers ->> 'github-authentication-token-expiration';
      update public.collector_state set
        dispatch_token_checked_at = now(),
        token_request_id = null,
        dispatch_token_expires_at = coalesce(
          (replace(v_expiry, ' UTC', '')::timestamp at time zone 'UTC'),
          dispatch_token_expires_at)
      where id = 1;
    end if;
  end if;
  if v_state.site_request_id is not null then
    select status_code, content into v_reply
      from net._http_response where id = v_state.site_request_id;
    if found then
      begin
        v_body := v_reply.content::jsonb;
      exception when others then
        v_body := null;
      end;
      update public.collector_state set
        site_checked_at = now(),
        site_request_id = null,
        site_data_through = coalesce((v_body ->> 'data_through')::date, site_data_through),
        site_built_at = coalesce((v_body ->> 'built_at')::timestamptz, site_built_at)
      where id = 1;
    end if;
  end if;
end;
$$;

revoke all on function public.check_dispatch_token() from public, anon, authenticated;
revoke all on function public.request_site_build() from public, anon, authenticated;
revoke all on function public.record_outside_checks() from public, anon, authenticated;

select cron.schedule('check-dispatch-token', '17 7 * * *',
                     $$select public.check_dispatch_token()$$);
select cron.schedule('check-site-freshness', '27 */6 * * *',
                     $$select public.request_site_build()$$);
select cron.schedule('record-outside-checks', '33 * * * *',
                     $$select public.record_outside_checks()$$);

-- status_now: the same columns as before (migration 0005), then the token's
-- expiry and the live site's data date.
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
  (select logged_at from public.fetch_log order by fetch_id limit 1) as collecting_since,
  (select dispatch_token_expires_at from public.collector_state where id = 1)
                                              as dispatch_token_expires_at,
  (select site_data_through from public.collector_state where id = 1) as site_data_through,
  (select site_built_at from public.collector_state where id = 1) as site_built_at
from (values ('vehiclePositions'), ('tripUpdates'), ('serviceAlerts')) as f (feed)
left join last_ok l on l.feed = f.feed
left join today t on t.feed = f.feed
group by f.feed, l.requested_at, l.entity_count, l.newest_entity_ts, l.latency_ms;

grant select on public.status_now to anon, authenticated;
