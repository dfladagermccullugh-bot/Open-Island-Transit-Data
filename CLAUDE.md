# Open Island Transit Data: working notes for Claude

Read this first. It holds what is not obvious from the code.

## What this is

A public archive of Island Transit's (Whidbey and Camano Islands, WA)
GTFS-Realtime feeds, with daily measurement of the feed's quality in
survey-error terms (coverage, nonresponse, measurement error, processing
error, paradata) and, later, an on-time dataset inferred from vehicle
positions. Owner: Davin. It is a portfolio piece; visibility is the priority.
Every public number must trace to code in this repository.

Full design, metric definitions and phases: `docs/methods.md`,
`docs/decisions.md`, `docs/codebook.md`, `docs/collector-setup.md`.

## What is live (started 2026-11-19 21:24:41 UTC)

| Thing | Detail |
|---|---|
| Collector | Supabase project `open-island-transit-data`, ref `ekczqlmunyumtgtthppb`, region us-west-1, org `wmwcputgoumwxcnvcpbm`, free plan |
| Edge function | `collect`, version `collect/0.2.0`, JWT verification off; it checks a shared secret held in Supabase Vault (`collector_secret`) via `collector_secret_ok` |
| pg_cron jobs | `collect-realtime` (20 s: vehiclePositions, tripUpdates), `collect-alerts` (5 min), `collect-static-passio-night` (10:15 UTC), `collect-static-passio-day` (16:30 UTC), `collect-static-trillium` (Mon 10:20 UTC), `prune-archived` (11:40 UTC), `prune-cron-history` |
| Public read access | URL and publishable key in `.env.example` and `pipeline/config.py`. Not secrets |
| GitHub workflows | `ci.yml`; `nightly.yml` (08:35 and 14:35 UTC: archive complete Pacific days to Releases `raw-YYYY-MM`, manifest on the `data` branch); `watchdog.yml` (every 30 min; opens an issue titled "Collector problem") |
| Cloud environment | `open-island-transit-data`, allowed hosts: `passio3.com`, `data.trilliumtransit.com`, `ekczqlmunyumtgtthppb.supabase.co` |
| Cost | $0. Keep it that way |

## What the feed checks established

- Feeds: `https://passio3.com/islandtransit/passioTransit/gtfs/realtime/{vehiclePositions,tripUpdates,serviceAlerts}`. No key. Stated rate limit 6,000 requests per minute per IP.
- Vehicles carry trip ID, route ID, position, bearing, speed, stop sequence, stop ID, timestamp, occupancy. Never sent: `start_date`, `start_time`, `direction_id`, `current_status`, any `delay`.
- The header timestamp equals the request time, so staleness must come from vehicle timestamps.
- Trip updates are predicted clock times, refresh about once a minute, and include upcoming trips that have no bus yet.
- Real-time IDs match the **Passio** static GTFS, which is regenerated daily. The Passio zip sometimes arrives without its calendar files and central directory (seen at night, not in the morning); `pipeline/gtfs/zipread.py` reads it anyway.
- Passio marks every stop as a timepoint with interpolated times. True timepoints come from the **Trillium** static GTFS, which uses different IDs (386 of 410 stop codes overlap).
- No license or terms were found for the feeds. See `LICENSE-DATA.md`.
- Service runs roughly 3:35 AM to 8:25 PM Pacific on weekdays. No trip crosses midnight, so a service day is a Pacific calendar date.

## Working rules

- Develop on the session's `claude/...` branch, then fast-forward `main` and push both. Scheduled workflows and Pages run only from `main`. Davin approved pushing to `main`.
- Commit trailer: `Co-Authored-By: Claude <noreply@anthropic.com>` plus the `Claude-Session:` line. No model name.
- Every SQL change applied to Supabase is also saved as a numbered file in `supabase/migrations/`, with the same text.
- Redeploying `collect` replaces the whole function: send all four files (`index.ts`, `protobuf.ts`, `zipscan.ts`, `prune.ts`) and bump `VERSION`. A version change is an instrument change; note it in `docs/decisions.md`.
- Never delete stored responses except through `prune_raw`, which checks the archive first. Never delete `fetch_log` rows.
- Do not pause or alter the `collect-*` cron jobs without telling Davin; gaps in collection cannot be recovered.
- The archive only handles complete Pacific days. Do not archive the current day with `--release`: it would record a partial day in the manifest.
- Public text (README, docs, site) is factual and plain. Davin does final wording.
- Replies to Davin: short, structured, plain language. He prefers that Claude does everything it can and that he does only what Claude cannot.
- Standing decisions: no server, no paid services, publish all data including `occupancy_status` and the raw archive, site on GitHub Pages, on-time window 1 minute early to 5 minutes late at true timepoints.

## Checks

```bash
pip install -e ".[dev]"
ruff check . && ruff format --check .
pytest -q
deno test --allow-read supabase/functions/collect/
python scripts/watchdog.py        # collector healthy?
python scripts/verify_feed.py     # feed fields and ID match; run in service hours
```

## Next steps, in order

1. **Verify the first nightly archive** (service day 2026-11-19): the `Nightly archive` run succeeded, `manifest/raw.json` on the `data` branch lists the day with a `release_tag`, and release `raw-2026-11` holds the files at the recorded sizes. The upload step had not run for real when these notes were written; fix it if it failed. Also compare the 10:15 UTC and 16:30 UTC Passio zips (`zip_has_directory`).
2. **Parsed daily tables** in the nightly job: vehicle positions (one row per vehicle and vehicle timestamp, with first and last fetch and times seen), trip-update stop predictions, alerts, as Parquet, using `pipeline/parse/realtime.py`.
3. **Static GTFS versioning** by content hash (ignore the rolling `feed_info` dates), one pinned version per service day.
4. **Passio to Trillium crosswalk** for stops and true timepoints (stop code first, then distance).
5. **Quality metrics** per `docs/methods.md`, exported as small files to the `data` branch.
6. **Site**: Quarto with Observable JS on GitHub Pages (status, quality, methods, data pages). Needs Davin to set Pages source to "GitHub Actions".
7. **Arrival inference and on-time figures**, labelled experimental until field validation.
8. **First monthly release and quality report** around day 30.

## Waiting on Davin

- Send the Island Transit notice email (drafted in his Gmail, to `info@islandtransit.org`).
- Pages setting (step 6), optional Porkbun CNAME `transit` to `dfladagermccullugh-bot.github.io`.
- About 40 timed field observations for validation (step 7).
- Open question: move the repo from the `dfladagermccullugh-bot` account to his main account before announcing.
