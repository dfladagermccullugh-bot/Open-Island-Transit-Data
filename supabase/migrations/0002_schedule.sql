-- Open Island Transit Data: scheduler.
--
-- Requires two Vault secrets, created once at setup (never committed):
--   collector_function_url  e.g. https://<project-ref>.supabase.co/functions/v1/collect
--   collector_secret        random string; the same value is set as the edge
--                           function secret COLLECTOR_SECRET
--
--   select vault.create_secret('<url>', 'collector_function_url');
--   select vault.create_secret(encode(extensions.gen_random_bytes(32), 'hex'), 'collector_secret');

-- collector_tick: fired by pg_cron. Either asks the edge function to fetch,
-- or, while backing off, logs the skipped tick so the gap is explained.
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
    timeout_milliseconds := 25000
  );
end;
$$;

revoke all on function public.collector_tick(text, text[]) from public, anon, authenticated;

-- Vehicle positions and trip updates: every 20 seconds, all day.
select cron.schedule(
  'collect-realtime',
  '20 seconds',
  $$select public.collector_tick('realtime', array['vehiclePositions', 'tripUpdates'])$$
);

-- Service alerts: every 5 minutes.
select cron.schedule(
  'collect-alerts',
  '*/5 * * * *',
  $$select public.collector_tick('realtime', array['serviceAlerts'])$$
);

-- Static GTFS from Passio: daily at 10:15 UTC (2:15 AM PST / 3:15 AM PDT),
-- before the first trip of the day.
select cron.schedule(
  'collect-static-passio',
  '15 10 * * *',
  $$select public.collector_tick('static', array['passio'])$$
);

-- Static GTFS from Trillium: weekly, Mondays 10:20 UTC.
select cron.schedule(
  'collect-static-trillium',
  '20 10 * * 1',
  $$select public.collector_tick('static', array['trillium'])$$
);

-- pg_cron never cleans its own run history; keep 7 days.
select cron.schedule(
  'prune-cron-history',
  '5 9 * * *',
  $$delete from cron.job_run_details where end_time < now() - interval '7 days'$$
);
