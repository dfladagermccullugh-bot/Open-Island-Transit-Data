-- Open Island Transit Data: start the GitHub workflows from pg_cron.
--
-- GitHub's own schedule proved unreliable for this repository: on
-- 2026-11-20 the 08:35 UTC nightly run never started and the 30-minute
-- watchdog fired once in about 12 slots. pg_cron is reliable (it drives the
-- collector), so it now also asks GitHub to start the nightly archive and
-- the watchdog. The GitHub schedules stay as a fallback; an extra run is
-- harmless (the nightly only handles pending days, one run at a time).
--
-- Requires one Vault secret, created once by the owner (never committed):
--   github_dispatch_token  fine-grained GitHub token for this repository only,
--                          permission "Actions: Read and write"
--
--   select vault.create_secret('<token>', 'github_dispatch_token');
--
-- Until it exists, dispatch_workflow does nothing.

create or replace function public.dispatch_workflow(p_workflow text)
returns bigint
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_token text;
begin
  select decrypted_secret into v_token
    from vault.decrypted_secrets where name = 'github_dispatch_token';
  if v_token is null then
    return null;
  end if;
  return net.http_post(
    url     := 'https://api.github.com/repos/dfladagermccullugh-bot/Open-Island-Transit-Data'
               || '/actions/workflows/' || p_workflow || '/dispatches',
    headers := jsonb_build_object(
                 'Authorization', 'Bearer ' || v_token,
                 'Accept', 'application/vnd.github+json',
                 'X-GitHub-Api-Version', '2022-11-28',
                 'User-Agent', 'open-island-transit-data-scheduler'),
    body    := jsonb_build_object('ref', 'main'),
    timeout_milliseconds := 20000
  );
end;
$$;

revoke all on function public.dispatch_workflow(text) from public, anon, authenticated;

-- Nightly archive: shortly after each GitHub schedule slot (08:35, 14:35 UTC).
select cron.schedule(
  'dispatch-nightly',
  '40 8,14 * * *',
  $$select public.dispatch_workflow('nightly.yml')$$
);

-- Watchdog: every 30 minutes.
select cron.schedule(
  'dispatch-watchdog',
  '12,42 * * * *',
  $$select public.dispatch_workflow('watchdog.yml')$$
);
