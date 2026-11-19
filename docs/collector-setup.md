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
   within 20 seconds.

6. **Check**:

   ```sql
   select feed, last_ok_at, last_ok_age_s, last_entity_count, attempts_today, failed_today
   from status_now;
   ```

   Each feed should show a successful fetch in the last minute (five minutes
   for alerts).

## To pause or stop

```sql
select cron.alter_job(jobid, active := false) from cron.job where jobname like 'collect-%';
```

## The running collector

- Project reference: `ekczqlmunyumtgtthppb` (region us-west-1)
- Started: 2026-11-19 21:24:41 UTC
- Public configuration: see `.env.example`

## What is public

`fetch_log`, `raw_export`, `static_export` and `status_now` are readable with
the project's publishable key. Nothing is writable except by the function.
