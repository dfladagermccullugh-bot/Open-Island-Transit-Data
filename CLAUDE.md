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
| GitHub workflows | `ci.yml`; `nightly.yml` (08:35 and 14:35 UTC: archive complete Pacific days to Releases `raw-YYYY-MM`, then parsed tables to `parsed-YYYY-MM`, then the static GTFS version registry and crosswalk, then quality metrics; all except Release files go to the `data` branch); `watchdog.yml` (every 30 min; opens an issue titled "Collector problem") |
| Cloud environment | `open-island-transit-data`, allowed hosts: `passio3.com`, `data.trilliumtransit.com`, `ekczqlmunyumtgtthppb.supabase.co` |
| Cost | $0. Keep it that way |

## What the feed checks established

- Feeds: `https://passio3.com/islandtransit/passioTransit/gtfs/realtime/{vehiclePositions,tripUpdates,serviceAlerts}`. No key. Stated rate limit 6,000 requests per minute per IP.
- Vehicles carry trip ID, route ID, position, bearing, speed, stop sequence, stop ID, timestamp, occupancy. Never sent: `start_date`, `start_time`, `direction_id`, `current_status`, any `delay`.
- The header timestamp equals the request time, so staleness must come from vehicle timestamps.
- Trip updates are predicted clock times, refresh about once a minute, and include upcoming trips that have no bus yet.
- Real-time IDs match the **Passio** static GTFS, which is regenerated daily. The Passio zip sometimes arrives without its calendar files and central directory (seen at night, not in the morning); `pipeline/gtfs/zipread.py` reads it anyway.
- Passio marks every stop as a timepoint with interpolated times. **True timepoints come from Island Transit's printed timetables** (Davin's decision, 2026-11-20): `reference/timetable-timepoints-<date>.csv`, matched to Passio stops by `pipeline/gtfs/timepoints.py`, output to `timepoints/` on the `data` branch. The **Trillium** static GTFS (different IDs; 386 of 410 stop codes overlap) is kept only for comparison.
- **The Passio and Trillium schedules differ, and Passio is the current one.** Davin checked islandtransit.org (schedule effective 2026-09-27) on 2026-11-19: route 1 weekday first southbound 3:35 from Coupeville Elementary, first northbound 4:35 from Clinton Ferry, Harbor Station early bus 3:55. Passio matches all three; Trillium has 3:45, 5:10 and 3:50 (its calendar says "Year-Round starting 6/1/2025"). Trillium is stale, so its timepoint flags were checked against the printed timetables (`reference/`, `scripts/check_timepoints.py`, 2026-11-19): of 147 printed timepoint route-stops matched to Passio, 136 are Trillium timepoints, 6 are Trillium stops not marked as timepoints, 5 are new stops missing from Trillium; 29 Trillium timepoints are not printed; 12 printed names could not be tied to a Passio stop at first; after links Davin confirmed on 2026-11-20 (overrides file), 1 remains: "South Loop" on 2C (an on-request loop, not a stop). Of 5,224 Passio stop times at Trillium timepoints, 52% match a Trillium time exactly. A service change is announced for 2026-12-27; the static version registry should pick it up as a new Passio version.
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

1. **Verify the first nightly run** (service day 2026-11-19, first run 2026-11-20 08:35 UTC): the `Nightly archive` run succeeded; `manifest/raw.json`, `manifest/parsed.json` and `manifest/static.json` on the `data` branch list the day, `crosswalk/` has one folder, and `quality/days/2026-11-19.json` exists (a partial day: collection began 13:24 Pacific) with a `release_tag`; releases `raw-2026-11` and `parsed-2026-11` hold the files at the recorded sizes. Neither upload step has run for real yet; fix them if they failed. Also compare the 10:15 UTC and 16:30 UTC Passio zips (`zip_has_directory`).
2. ~~Parsed daily tables~~: done (`pipeline/tables/`, `TABLES_VERSION` `tables/0.1.0`). Rows are runs of unchanged values; see `docs/codebook.md`. Bumping `TABLES_VERSION` rebuilds every day on the next nightly run (7 days per run).
3. ~~Static GTFS versioning~~: done (`pipeline/gtfs/versions.py`, `manifest/static.json`). Verify after the first nightly run that 2026-11-19 is pinned for both sources.
4. ~~Passio to Trillium crosswalk~~: done (`pipeline/gtfs/crosswalk.py`, `crosswalk/` on the `data` branch, about 300 KB per version pair; watch growth if Passio content changes daily).
5. **Quality metrics**: first set done (`pipeline/quality/`, `QUALITY_VERSION` `quality/0.1.0`, output `quality/days/*.json` and `quality/daily.csv` on the `data` branch). Still to add once stop events and shapes exist: stop-level coverage, 30-day missing vehicles, randomness-of-gaps model, off-shape positions, jitter, prediction error, unassignable trips.
6. **Site**: Quarto with Observable JS on GitHub Pages (status, quality, methods, data pages). Needs Davin to set Pages source to "GitHub Actions".
7. **Arrival inference and on-time figures**, labelled experimental until field validation. Timepoints: the pairs in the day's `timepoints/` folder (`pipeline.gtfs.timepoints.timepoint_pairs`).
- **Service change 2026-12-27**: once the new guides are posted, add `reference/timetable-timepoints-2026-12-27.csv` and its overrides file (steps in `reference/README.md`). Reading the three September guide PDFs with Firecrawl cost 124 credits. Until then, days from 2026-12-27 use the September list and `timepoint_names_resolved_share` in `quality/daily.csv` will show whether stops changed.
8. **First monthly release and quality report** around day 30.

## Waiting on Davin

- Send the Island Transit notice email (drafted in his Gmail, to `info@islandtransit.org`).
- Pages setting (step 6), optional Porkbun CNAME `transit` to `dfladagermccullugh-bot.github.io`.
- About 40 timed field observations for validation (step 7).
- Optional: tell Island Transit their Trillium feed (data.trilliumtransit.com, used by trip planners) still has a pre-September schedule. Could go in the notice email.
- Open question: move the repo from the `dfladagermccullugh-bot` account to his main account before announcing.
