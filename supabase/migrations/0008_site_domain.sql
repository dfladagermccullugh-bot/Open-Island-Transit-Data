-- The site moved to islandtransitdata.org on November 27, 2026. GitHub
-- answers the old github.io address with a redirect, which the freshness
-- check would not follow, so it now asks the new address directly.
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
    url     := 'https://islandtransitdata.org/data/build.json',
    headers := jsonb_build_object('User-Agent', 'open-island-transit-data-scheduler'),
    timeout_milliseconds := 20000
  );
  update public.collector_state set site_request_id = v_id where id = 1;
  return v_id;
end;
$$;
