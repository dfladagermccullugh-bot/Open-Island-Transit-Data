# Decisions

| Date | Decision | Reason |
|---|---|---|
| 2026-11-18 | Collect all routes | Collection cost is the same, and data not collected cannot be recovered later |
| 2026-11-18 | Poll vehicle positions and trip updates every 20 s, alerts every 5 min, around the clock | About 0.1% of the host's stated rate limit; off-hours responses are part of the record |
| 2026-11-18 | Collector on Supabase (scheduled edge function), pipeline and archive on GitHub | No server to run; sub-minute scheduling; no cost |
| 2026-11-18 | Store raw responses before parsing; treat them as the source of truth | Parsing can be redone; collection cannot |
| 2026-11-18 | Keep every field the feed sends, including `occupancy_status` | Full transparency; it is a per-vehicle category, not data about a person |
| 2026-11-18 | Publish raw and derived data from the first day | Visibility; data sits in Releases so it can be withdrawn if the agency objects |
| 2026-11-18 | Identify the collector by project name and repository URL in the User-Agent | Lets the host see who is polling and where to complain |
| 2026-11-18 | On-time window: 1 minute early to 5 minutes late, at true timepoints only | Common agency convention; Passio schedule times at other stops are interpolated |
| 2026-11-18 | One collector function for real-time and static downloads | Fewer parts to deploy than the two functions first planned |
| 2026-11-19 | Keep polling trip updates every 20 s although they refresh about once a minute | Cheap, and the refresh rate is itself something to measure; revisit after a week of data |
| 2026-11-19 | Limit prediction-error figures to trips matched to a reporting vehicle | The trip-update feed also lists upcoming trips that have no bus yet |
| 2026-11-19 | Use the feed's speed field to detect stationary buses | Present on every vehicle in the daytime check |
| 2026-11-19 | The collector checks its shared secret by asking the database, not from a function environment variable | Setup needs no manual step, and the secret never leaves the database |
| 2026-11-19 | Download the Passio static GTFS twice a day (2:15 AM and 8:30 AM Pacific) | The zip was incomplete at night and complete in the morning; sampling both times measures that |
| 2026-11-19 | Collection started 21:24:41 UTC | First logged fetch |
| 2026-11-19 | Archive complete Pacific days shortly after midnight, not the same evening | A day is not finished until midnight; off-hours fetches belong to it |
| 2026-11-19 | Collector version 0.2.0 from 21:37 UTC | Adds the prune step; fetching is unchanged |
| 2026-11-19 | Parsed tables store runs of unchanged values, not one row per fetch | Polling every 20 s repeats most values; runs keep every change and how long each value was seen, at a fraction of the size |
| 2026-11-19 | Parsed tables are built from the archived raw file, not from the collector | Every derived row then traces to a published file and its hash |
| 2026-11-19 | Parsed tables go to their own `parsed-YYYY-MM` releases, rebuilt when the table code version changes | Raw files never change; derived files can be regenerated |
| 2026-11-19 | Static GTFS versions are identified by the hash of every file except `feed_info.txt` | Passio rewrites its feed_info dates daily and Trillium stamps generation time; neither is a schedule change |
| 2026-11-19 | Each service day is pinned to the first complete download that day, else a matching incomplete download, else the latest earlier complete download | Uses the schedule as published for that day where possible, and records which rule applied |
