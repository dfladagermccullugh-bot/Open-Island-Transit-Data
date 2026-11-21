# Collector setup

The collector is one Supabase project: Postgres with `pg_cron` and `pg_net`,
plus one edge function. These are the steps to deploy it from this
repository.

1. **Create a Supabase project** (free plan is enough). Note its project
   reference (`<ref>`).

2. **Apply** `supabase/migrations/0001_collector.sql`.

3. **Create two Vault secrets** in the SQL editor. They are not stored in
   this repository.

   ```sql
   select vault.create_secret(
     'https://<ref>.supabase.co/functions/v1/collect', 'collector_function_url');
   select vault.create_secret(
     encode(extensions.gen_random_bytes(32), 'hex'), 'collector_secret');
   ```

4. **Deploy the function** in `supabase/functions/collect/` with JWT
   verification off. The function checks the shared secret itself, by asking
   the database, so there is no function secret to set.

   ```bash
   supabase functions deploy collect --no-verify-jwt
   ```

5. **Apply** `supabase/migrations/0002_schedule.sql`. Collection starts
   within 20 seconds. Then apply `0003_archive_support.sql`.

6. **Check**:

   ```sql
   select feed, last_ok_at, last_ok_age_s, last_entity_count, attempts_today, failed_today
   from status_now;
   ```

   Each feed should show a successful fetch in the last minute (five minutes
   for alerts).

## Archive, prune and watchdog

- **Nightly archive** (`.github/workflows/nightly.yml`, `python -m pipeline.archive`):
  after midnight Pacific, each complete service day is pulled from the
  collector, written to files, uploaded to the month's GitHub Release, checked,
  and then listed in `manifest/raw.json` on the `data` branch.
- **Prune** (`prune-archived` cron job, `supabase/functions/collect/prune.ts`):
  once a day the collector reads that manifest and deletes its own copy of a
  day only if the day is at least 3 days old, every listed file can be
  downloaded at its recorded size, and the database's row count and digest for
  the day equal the manifest's. The fetch log is never deleted. Every run is
  recorded in `prune_log`:
  `select * from prune_log order by prune_id desc limit 5;`
- **Watchdog** (`.github/workflows/watchdog.yml`, `scripts/watchdog.py`): every
  30 minutes; fails and opens an issue if a feed has no recent successful
  fetch, the positions feed is empty in service hours, the collector is backing
  off, or the database is filling up.
- **Starting the workflows** (`supabase/migrations/0004_dispatch_workflows.sql`):
  GitHub's own schedule skipped runs for this repository, so pg_cron also
  starts the nightly archive (`dispatch-nightly`, 08:40 and 14:40 UTC) and the
  watchdog (`dispatch-watchdog`, every 30 minutes) through GitHub's
  workflow-dispatch API. It needs a fine-grained GitHub token for this
  repository only, with "Actions: Read and write", stored once in Vault:
  `select vault.create_secret('<token>', 'github_dispatch_token');`. Without
  it the jobs do nothing and only the GitHub schedules run. Recent responses:
  `select id, status_code, created from net._http_response order by id desc limit 5;`
  (204 means GitHub accepted the request).

## To pause or stop

```sql
select cron.alter_job(jobid, active := false) from cron.job where jobname like 'collect-%';
```

## The running collector

- Project reference: `ekczqlmunyumtgtthppb` (region us-west-1)
- Started: 2026-11-19 21:24:41 UTC
- Public configuration: see `.env.example`

## What is public

The project's publishable key can read the tables `fetch_log`,
`raw_response`, `static_gtfs_fetch`, `collector_state` and `prune_log`, the
views `raw_export`, `static_export` and `status_now`, and the functions
`day_digest(date)`, `pacific_day_start(date)` and `collector_health()`.
Nothing is writable except by the function.
