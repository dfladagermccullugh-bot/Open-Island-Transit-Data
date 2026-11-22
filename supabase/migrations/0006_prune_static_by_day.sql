-- Open Island Transit Data: let the prune job clear archived schedule zips.
--
-- prune_static cleared a zip only when it had been logged more than 72 hours
-- ago. A day is pruned at 11:40 UTC three days later, when its 16:30 UTC zip
-- is about 67 hours old, and the prune job does not come back to a day, so
-- the zips of archived days were never cleared (first real run, 2026-11-22:
-- static_cleared 0). The age rule now matches prune_raw: the Pacific date
-- the zip was logged on must be at least 3 days before today. The hash check
-- is unchanged; the caller still confirms the archive holds the file.
create or replace function public.prune_static(p_ids bigint[], p_sha256 text[])
returns bigint
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_today   date := (now() at time zone 'America/Los_Angeles')::date;
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
     and (s.logged_at at time zone 'America/Los_Angeles')::date <= v_today - 3;
  get diagnostics v_deleted = row_count;
  return v_deleted;
end;
$$;

revoke all on function public.prune_static(bigint[], text[]) from public, anon, authenticated;
grant execute on function public.prune_static(bigint[], text[]) to service_role;
